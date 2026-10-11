"""Near-duplicates and case export."""

import csv
import hashlib
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from app import audit, export, jobs, neardup, worker
from tests.test_search import FakeModel

LETTER = (
    "Dear Mr Jones, we write about the supply of 400 tonnes of steel agreed on 1 March 2024. "
    "The steel was due to be delivered to our yard in Leeds on 15 April 2024 and did not arrive. "
    "Our client has had to buy replacement steel from another supplier at a higher price, and holds "
    "you responsible for the difference and for the cost of the delay to its building works. "
    "Please confirm within 14 days whether you accept liability, failing which our client will issue "
    "proceedings without further notice. We also ask you to keep all documents about this order, "
    "including emails with the haulier, delivery notes and invoices. Yours sincerely, Smith and Co."
)
OTHER = (
    "Witness statement of Jane Doe. I am the site manager for the Claimant. On 16 April 2024 I telephoned "
    "Bob Smith at the Defendant to ask where the delivery was. He told me the haulier had broken down near "
    "Sheffield and that the lorry would arrive the next morning. It did not. Over the following weeks I "
    "called him at least six times and each time was given a new date. No steel ever arrived at the site. "
    "We had to stop work on the frame for three weeks and send the crane back to the hire company."
)


@pytest.fixture
def model(rt):
    m = FakeModel()
    rt.__dict__["embedder"] = m
    return m


def work(rt, kinds=("convert", "index", "dups", "export")) -> int:
    n = 0
    while (job := jobs.claim(rt.db, kinds)) is not None:
        worker.HANDLERS[job["kind"]](rt, job)
        n += 1
    return n


@pytest.fixture
def case(new_case):
    return new_case()


def files(client, case_id, **params):
    r = client.get(f"/api/cases/{case_id}/files", params={"sort": "name", "order": "asc", **params})
    assert r.status_code == 200, r.text
    return {f["name"]: f for f in r.json()["items"]}


# --- near-duplicates ------------------------------------------------------------------------


def test_fingerprints():
    a = neardup.signature(LETTER)
    b = neardup.signature(LETTER.replace("Yours sincerely", "Yours faithfully").replace("14 days", "7 days"))
    c = neardup.signature(OTHER)
    assert neardup.similarity(a, b) >= neardup.THRESHOLD
    assert neardup.similarity(a, c) < 0.2
    assert neardup.signature("A short covering note.") is None
    # Front matter and page markers don't count.
    assert neardup.similarity(a, neardup.signature("---\ndoc_id: x\n---\n<!-- page: 1 -->\n" + LETTER)) == 1.0


def test_later_near_copy_is_flagged(admin, case, rt, model):
    admin.upload(case, "Letters/letter.txt", LETTER.encode())
    admin.upload(case, "Letters/letter final.txt", LETTER.replace("Yours sincerely", "Yours faithfully").encode())
    admin.upload(case, "statement.txt", OTHER.encode())
    admin.upload(case, "note.txt", b"Call back Tuesday.")
    work(rt)
    got = files(admin, case)
    assert got["letter.txt"]["near_duplicate"] is None
    near = got["letter final.txt"]["near_duplicate"]
    assert near["of"] == got["letter.txt"]["id"] and near["name"] == "Letters/letter.txt" and near["similarity"] >= 0.8
    assert got["statement.txt"]["near_duplicate"] is None and got["note.txt"]["near_duplicate"] is None
    assert list(files(admin, case, status="near_duplicate")) == ["letter final.txt"]
    assert admin.get(f"/api/cases/{case}/progress").json()["near_duplicates"] == 1

    # Deleting the earlier file clears the flag rather than pointing nowhere.
    with rt.db.transaction() as db:
        db.execute("DELETE FROM files WHERE id = ?", (got["letter.txt"]["id"],))
    assert files(admin, case)["letter final.txt"]["near_duplicate"] is None


def test_near_duplicates_stay_inside_a_case(admin, new_case, rt, model):
    one, two = new_case("One"), new_case("Two")
    admin.upload(one, "letter.txt", LETTER.encode())
    admin.upload(two, "letter.txt", (LETTER + " PS.").encode())
    work(rt)
    assert files(admin, two)["letter.txt"]["near_duplicate"] is None


def test_files_converted_earlier_are_checked(admin, case, rt, model):
    admin.upload(case, "a.txt", LETTER.encode())
    admin.upload(case, "b.txt", (LETTER + " With enclosures.").encode())
    work(rt)
    with rt.db.transaction() as db:  # as before this version
        db.execute("DELETE FROM near_sigs")
        db.execute("UPDATE files SET near_dup_of = NULL, near_dup_score = NULL")
        jobs.enqueue(db, "dups", case)
    assert files(admin, case)["b.txt"]["near_duplicate"] is None
    work(rt)
    assert files(admin, case)["b.txt"]["near_duplicate"]["name"] == "a.txt"


# --- export ---------------------------------------------------------------------------------


def test_names_inside_the_zip():
    assert export.safe_part('a<b>:c"d|e?f*.pdf') == "a_b__c_d_e_f_.pdf"
    assert export.safe_part("CON.txt") == "_CON.txt"
    assert export.safe_part("ends with dot. ") == "ends with dot"
    assert len(export.safe_part("x" * 300 + ".pdf")) <= 120 and export.safe_part("x" * 300 + ".pdf").endswith(".pdf")
    names = export._Names()
    assert names.take("originals", "Letters", "a.pdf") == "originals/Letters/a.pdf"
    assert names.take("originals", "letters", "A.pdf") == "originals/letters/A (2).pdf"
    assert names.take("originals", "", "notes") == "originals/notes"
    assert names.take("originals", "", "notes") == "originals/notes (2)"
    deep = names.take("originals", "/".join(["A long folder name for correspondence"] * 6), "y" * 200 + ".eml")
    assert len(deep) <= export.MAX_PATH + 10 and deep.endswith(".eml")


def test_audit_csv_cannot_run_formulas():
    out = export._csv([{"detail": "=HYPERLINK(\"x\")", "target": "plain"}]).decode("utf-8-sig")
    row = next(csv.DictReader(io.StringIO(out)))
    assert row["detail"].startswith("'=") and row["target"] == "plain"


def test_export_a_case(admin, case, rt, model):
    pdf = b"%PDF-1.4 not really a pdf"
    admin.upload(case, "Letters/letter.txt", LETTER.encode())
    admin.upload(case, "statement.txt", OTHER.encode())
    admin.patch(f"/api/cases/{case}", json={"notes": "Limitation date 1 March 2030."})
    work(rt, ("convert", "index", "dups"))
    with rt.db.transaction() as db:  # an original the converter can't read still goes in the export
        db.execute("INSERT INTO files (id, case_id, name, folder, ext, source_type, size, sha256, status, added_at) VALUES (?, ?, 'scan.pdf', '', '.pdf', 'pdf', ?, ?, 'failed', ?)",
                   ("f" * 32, case, len(pdf), hashlib.sha256(pdf).hexdigest(), audit.now()))
    rt.storage.keep_bytes(case, "f" * 32, ".pdf", pdf)

    r = admin.post(f"/api/cases/{case}/exports")
    assert r.status_code == 202, r.text
    exp = r.json()
    assert exp["state"] == "queued"
    assert admin.post(f"/api/cases/{case}/exports").json()["id"] == exp["id"]  # one at a time
    assert work(rt, ("export",)) == 1

    listed = admin.get(f"/api/cases/{case}/exports").json()
    ready = listed["items"][0]
    assert ready["state"] == "ready" and ready["files"] == 3 and ready["expires_at"] and listed["keep_days"] == 7

    r = admin.get(f"/api/cases/{case}/exports/{exp['id']}/file")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "Smith%20v%20Jones%20export" in r.headers["content-disposition"]
    assert hashlib.sha256(r.content).hexdigest() == ready["sha256"]
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(zf.namelist())
    assert {"README.txt", "manifest-sha256.txt", "metadata/case.json", "metadata/files.json", "metadata/reviews.json",
            "audit/case-audit-trail.csv", "audit/case-audit-trail.json", "originals/Letters/letter.txt",
            "originals/statement.txt", "originals/scan.pdf", "markdown/Letters/letter.txt.md", "markdown/statement.txt.md"} <= names
    assert zf.read("originals/Letters/letter.txt") == LETTER.encode()
    assert zf.read("originals/scan.pdf") == pdf
    assert "did not arrive" in zf.read("markdown/Letters/letter.txt.md").decode()

    # Every file's hash is in the manifest, and the manifest is right.
    manifest = dict(reversed(line.split("  ", 1)) for line in zf.read("manifest-sha256.txt").decode().splitlines())
    assert set(manifest) == names - {"manifest-sha256.txt"}
    for name, digest in manifest.items():
        assert hashlib.sha256(zf.read(name)).hexdigest() == digest, name

    meta = {f["name"]: f for f in json.loads(zf.read("metadata/files.json"))}
    assert meta["scan.pdf"]["sha256"] == hashlib.sha256(pdf).hexdigest() and meta["scan.pdf"]["markdown_in_zip"] is None
    assert meta["letter.txt"]["original_in_zip"] == "originals/Letters/letter.txt"
    info = json.loads(zf.read("metadata/case.json"))
    assert info["notes"] == "Limitation date 1 March 2030." and info["export"]["made_by"] == "fred"
    trail = json.loads(zf.read("audit/case-audit-trail.json"))
    assert trail["chain_intact"] is True
    actions = [e["action"] for e in trail["entries"]]
    assert "case.create" in actions and "file.upload" in actions and "case.export" in actions
    assert all(e["case_id"] == case for e in trail["entries"])
    assert "Smith v Jones" in zf.read("README.txt").decode()

    log = admin.get("/api/audit", params={"case_id": case}).json()["items"]
    done = [e for e in log if e["action"] == "case.export" and "sha256" in e["detail"]]
    assert done and ready["sha256"] in done[0]["detail"]
    assert any(e["action"] == "case.export_download" for e in log)


def test_only_the_admin_exports(admin, case, rt, people, new_case):
    editor = people("erin", "editor", [case])
    assert editor.post(f"/api/cases/{case}/exports").status_code == 403
    assert editor.get(f"/api/cases/{case}/exports").status_code == 403
    exp = admin.post(f"/api/cases/{case}/exports").json()
    assert editor.get(f"/api/cases/{case}/exports/{exp['id']}/file").status_code == 403
    # An export is only reachable through its own case.
    other = new_case("Other")
    assert admin.get(f"/api/cases/{other}/exports/{exp['id']}/file").status_code == 404
    assert admin.get(f"/api/cases/{case}/exports/{exp['id']}/file").status_code == 409  # not built yet
    assert admin.get(f"/api/cases/{case}/exports/..%2f..%2fapp.db/file").status_code == 404


def test_exports_expire_and_can_be_deleted(admin, case, rt, model):
    exp = admin.post(f"/api/cases/{case}/exports").json()
    work(rt, ("export",))
    path = export.path_of(rt, exp["id"])
    assert path.is_file()
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat().replace("+00:00", "Z")
    with rt.db.transaction() as db:
        db.execute("UPDATE exports SET finished_at = ? WHERE id = ?", (old, exp["id"]))
    export.tidy(rt)
    assert not path.exists()
    assert admin.get(f"/api/cases/{case}/exports").json()["items"] == []
    assert admin.get(f"/api/cases/{case}/exports/{exp['id']}/file").status_code == 410

    exp = admin.post(f"/api/cases/{case}/exports").json()
    work(rt, ("export",))
    assert admin.delete(f"/api/cases/{case}/exports/{exp['id']}").status_code == 204
    assert not export.path_of(rt, exp["id"]).exists()


def test_export_of_a_deleted_case_is_tidied(admin, case, rt, model):
    exp = admin.post(f"/api/cases/{case}/exports").json()
    work(rt, ("export",))
    assert admin.post(f"/api/cases/{case}/delete", json={"confirm_name": "Smith v Jones"}).status_code == 204
    export.tidy(rt)
    assert not export.path_of(rt, exp["id"]).exists()


def test_export_without_room_fails_plainly(admin, case, rt, model, monkeypatch):
    admin.upload(case, "letter.txt", LETTER.encode())
    exp = admin.post(f"/api/cases/{case}/exports").json()
    monkeypatch.setattr(export.shutil, "disk_usage", lambda p: type("U", (), {"free": 10})())
    work(rt, ("export",))
    got = admin.get(f"/api/cases/{case}/exports").json()["items"][0]
    assert got["id"] == exp["id"] and got["state"] == "failed" and "free space" in got["error"]
