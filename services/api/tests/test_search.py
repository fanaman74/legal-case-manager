"""Passages, the keyword and meaning indexes, and search in a case.

The local embedding model isn't available in tests, so a small stand-in
turns text into vectors where words with the same meaning (delivery,
shipment) land close together. Everything else is the real code: SQLite
FTS5 and LanceDB on disk."""

import hashlib
import re

import pytest

from app import indexing, jobs, pipeline, worker
from app.index import chunk, query
from app.index.embed import EmbedUnavailable, normalise
from app.index.store import END, HIT
from tests import samples

SAME_MEANING = {
    "shipment": "deliver", "shipped": "deliver", "delivery": "deliver", "delivered": "deliver", "arrive": "deliver", "arrived": "deliver",
    "steel": "metal", "iron": "metal", "invoice": "bill", "payment": "bill", "paid": "bill",
}


class FakeModel:
    """Bag of word meanings, hashed into 64 dimensions."""

    def __init__(self):
        self.up = True
        self.calls = 0

    def embed(self, texts):
        if not self.up:
            raise EmbedUnavailable("The local AI model isn't running.")
        self.calls += 1
        out = []
        for t in texts:
            v = [0.0] * 64
            for w in re.findall(r"[a-z]+", t.lower()):
                w = SAME_MEANING.get(w, w)
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1.0
            out.append(normalise(v))
        return out


@pytest.fixture
def model(rt):
    m = FakeModel()
    rt.__dict__["embedder"] = m  # replaces the cached Ollama client
    return m


def work(rt) -> int:
    n = 0
    while (job := jobs.claim(rt.db, ("convert", "index"))) is not None:
        worker.HANDLERS[job["kind"]](rt, job)
        n += 1
    return n


def txt(s: str) -> bytes:
    return s.encode()


# --- passages -------------------------------------------------------------------------


def test_passages_keep_pages_and_headings():
    md = '---\ndoc_id: "x"\n---\n<!-- page: 1 -->\n\n# Claim\n\n## Background\n\nThe **agreement** was signed.\n\n<!-- page: 2 -->\n\n## Loss\n\nThe Claimant lost \\$5,000.\n'
    ps = chunk.chunks(md)
    assert [(p.page, p.heading) for p in ps] == [(1, "Claim > Background"), (2, "Claim > Loss")]
    assert ps[0].text == "Claim\n\nBackground\n\nThe agreement was signed."
    assert "$5,000" in ps[1].text and "doc_id" not in ps[0].text


def test_long_text_is_split_near_the_target_size():
    para = " ".join(f"Sentence number {i} says something about the contract." for i in range(200))
    ps = chunk.chunks(para)
    assert len(ps) > 5
    assert all(len(p.text) <= chunk.MAX for p in ps)
    assert ps[0].page is None


def test_plain_text_for_snippets():
    assert chunk.plain("| a | b |\n|---|---|\n| 1 | 2 |") == "| a | b |\n| 1 | 2 |"
    assert chunk.plain("See [the letter](https://x.example) and note[^1]. \\*Not\\* bold.") == "See the letter and note[1]. *Not* bold."


# --- search operators -----------------------------------------------------------------


def test_query_operators():
    assert query.parse("delivery steel").fts == '"delivery" AND "steel"'
    assert query.parse('"letter of credit"').fts == '"letter of credit"'
    assert query.parse("steel OR iron").fts == '"steel" OR "iron"'
    assert query.parse("steel -draft").fts == '("steel") NOT ("draft")'
    assert query.parse("late steel -draft").fts_any == '("late" OR "steel") NOT ("draft")'
    assert query.parse("arbitrat*").fts == '"arbitrat"*'
    p = query.parse('late "Bill of Lading" -draft')
    assert p.terms == ["late", "Bill of Lading"] and p.text == "late Bill of Lading"


def test_query_text_cannot_change_the_query():
    # Quotes, brackets and FTS keywords typed by people are only ever words.
    for raw in ('steel" OR "x', "NEAR(a b)", "a AND b", "col:steel", "^steel", '"unclosed'):
        p = query.parse(raw)
        assert p.fts.count('"') % 2 == 0
    with pytest.raises(query.QueryError, match="not only words to leave out"):
        query.parse("-draft")
    with pytest.raises(query.QueryError, match="Type something"):
        query.parse("   ")


# --- end to end -----------------------------------------------------------------------


@pytest.fixture
def case(new_case):
    return new_case("Smith v Jones")


def search(client, case_id, q, **params):
    r = client.get(f"/api/cases/{case_id}/search", params={"q": q, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_keyword_and_meaning_search(admin, case, rt, model):
    admin.upload(case, "Pleadings/claim.pdf", samples.pdf_bytes())
    admin.upload(case, "Notes/haulier.txt", txt("Call with the haulier. The shipment of iron was late."))
    admin.upload(case, "Notes/unrelated.txt", txt("The parties met for lunch to discuss the weather."))
    work(rt)
    files = {f["name"]: f for f in admin.get(f"/api/cases/{case}/files").json()["items"]}
    assert files["claim.pdf"]["search"] == {"state": "indexed", "passages": 3}

    # Exact words: the PDF, at the page that has them, with the words marked.
    r = search(admin, case, "Agreement signed", mode="keyword")
    assert [x["file"]["name"] for x in r["results"]] == ["claim.pdf"]
    hit = r["results"][0]["hits"][0]
    assert hit["page"] == 2 and f"{HIT}Agreement{END} {HIT}signed{END}" in hit["snippet"] and hit["found_by"] == ["words"]

    # Stemming: "delivered" finds "delivery"/"delivered".
    assert search(admin, case, "delivered", mode="keyword")["results"][0]["file"]["name"] == "claim.pdf"

    # Meaning: "steel delivery" finds the haulier's note, which says neither word.
    r = search(admin, case, "steel delivery", mode="semantic")
    names = [x["file"]["name"] for x in r["results"]]
    assert r["meaning"] == "on" and "haulier.txt" in names and names.index("haulier.txt") < names.index("unrelated.txt")

    # Best match merges both, and says how each passage was found.
    r = search(admin, case, "steel delivery")
    by_name = {x["file"]["name"]: x for x in r["results"]}
    assert set(by_name["claim.pdf"]["hits"][0]["found_by"]) == {"words", "meaning"}
    assert by_name["haulier.txt"]["hits"][0]["found_by"] == ["meaning"]
    # Best match also shows passages with only some of the words.
    r = search(admin, case, "agreement weather")
    assert {"claim.pdf", "unrelated.txt"} <= {x["file"]["name"] for x in r["results"]}
    assert search(admin, case, "agreement weather", mode="keyword")["results"] == []
    assert r["index"] == {"indexed": 3, "keyword_only": 0, "waiting": 0, "failed": 0}


def test_operators_and_filters(admin, case, rt, model):
    admin.upload(case, "Letters/one.txt", txt("Draft letter about the steel contract."))
    admin.upload(case, "Letters/two.txt", txt("Final letter about the steel contract."))
    admin.upload(case, "Emails/three.eml", samples.eml_bytes())
    work(rt)
    names = lambda r: sorted(x["file"]["name"] for x in r["results"])
    assert names(search(admin, case, "steel -draft", mode="keyword")) == ["three.eml", "two.txt"]
    assert names(search(admin, case, '"final letter"', mode="keyword")) == ["two.txt"]
    assert names(search(admin, case, "steel", mode="keyword", folder="Letters")) == ["one.txt", "two.txt"]
    assert names(search(admin, case, "steel", mode="keyword", type="email")) == ["three.eml"]
    assert names(search(admin, case, "steel", mode="keyword", date_from="2024-04-01", date_to="2024-04-30")) == ["three.eml"]
    assert names(search(admin, case, "steel", mode="semantic", type="text")) == ["one.txt", "two.txt"]
    r = admin.get(f"/api/cases/{case}/search", params={"q": "-steel"})
    assert r.status_code == 400 and "words to leave out" in r.json()["detail"]


def test_search_never_crosses_cases(admin, case, rt, model, new_case, people):
    other = new_case("Other matter")
    admin.upload(case, "a.txt", txt("The steel never arrived."))
    admin.upload(other, "b.txt", txt("Secret: the steel arrived in Hull."))
    work(rt)
    for mode in ("keyword", "semantic", "hybrid"):
        r = search(admin, case, "steel arrived", mode=mode)
        assert [x["file"]["name"] for x in r["results"]] == ["a.txt"], mode
    member = people("mia", "editor", [case])
    assert member.get(f"/api/cases/{other}/search", params={"q": "steel"}).status_code == 404
    assert [x["file"]["name"] for x in search(member, case, "steel")["results"]] == ["a.txt"]
    # Each case has its own index files, inside its own folder.
    assert (rt.storage.case_dir(case) / "index" / "search.db").exists()
    assert (rt.storage.case_dir(other) / "index" / "search.db").exists()


def test_keyword_search_works_before_the_model_is_ready(admin, case, rt, model):
    model.up = False
    admin.upload(case, "a.txt", txt("The shipment was late."))
    work(rt)
    f = admin.get(f"/api/cases/{case}/files").json()["items"][0]
    assert f["search"]["state"] == "keyword_only"
    r = search(admin, case, "shipment late")
    assert r["meaning"] == "unavailable" and r["results"][0]["file"]["name"] == "a.txt"
    assert indexing.catch_up(rt) == 0  # still down: nothing queued
    model.up = True
    assert indexing.catch_up(rt) == 1
    work(rt)
    assert admin.get(f"/api/cases/{case}/files").json()["items"][0]["search"]["state"] == "indexed"
    assert search(admin, case, "delivery", mode="semantic")["results"][0]["file"]["name"] == "a.txt"
    indexing.optimise_all(rt)  # the worker's hourly tidy-up keeps search working
    assert search(admin, case, "delivery", mode="semantic")["results"][0]["file"]["name"] == "a.txt"
    status = worker.extra_status(rt)["index"]
    assert (status["indexed"], status["chunks"], status["problem"]) == (1, 1, "")


def test_converting_again_replaces_the_passages(admin, case, rt, model):
    f = admin.upload(case, "scan.pdf", samples.scanned_pdf_bytes()).json()["file"]
    work(rt)
    assert search(admin, case, "steel", mode="keyword")["results"] == []
    # Re-conversion (here: as if OCR had read it) re-indexes the new text.
    rt.storage.markdown(case, f["id"]).write_text("<!-- page: 1 -->\n\nThe steel never arrived.\n", encoding="utf-8")
    with rt.db.transaction() as db:
        pipeline.after_convert(db, case, f["id"], "converted")
    work(rt)
    r = search(admin, case, "steel", mode="keyword")
    assert r["results"][0]["hits"][0]["page"] == 1
    assert len(rt.index(case).passages(f["id"])) == 1


def test_reindex_and_audit(admin, case, rt, model, people):
    admin.upload(case, "a.txt", txt("Steel."))
    work(rt)
    reader = people("rita", "readonly", [case])
    search(reader, case, "steel")
    assert reader.post(f"/api/cases/{case}/reindex").status_code == 403
    assert admin.post(f"/api/cases/{case}/reindex").json() == {"queued": 1}
    assert work(rt) == 1
    log = admin.get(f"/api/cases/{case}/activity").json()["items"]
    s = next(e for e in log if e["action"] == "case.search")
    assert s["target"] == "steel" and s["username"] == "rita"
    assert any(e["action"] == "case.reindex" for e in log)
