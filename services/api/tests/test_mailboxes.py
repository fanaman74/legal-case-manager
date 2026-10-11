"""Outlook mailboxes: reading a real PST (71 emails from the public Enron
data set, fetched by scripts/ci/fetch-samples.sh), searching it, and adding
emails to the case."""

import os
from pathlib import Path

import pytest

from app import jobs, worker
from app.pst import reader

PST = Path(__file__).parent / "samples" / "download" / "enron.pst"
pytestmark = pytest.mark.skipif(not PST.exists() and not os.environ.get("REQUIRE_PST"), reason="run scripts/ci/fetch-samples.sh for the sample mailbox")


def work(rt, kinds=("convert", "index", "pst", "pst_add")) -> int:
    n = 0
    while (job := jobs.claim(rt.db, kinds)) is not None:
        worker.HANDLERS[job["kind"]](rt, job)
        n += 1
    return n


def upload(client, case_id, name="lokay.pst", data=None):
    return client.put(f"/api/cases/{case_id}/mailboxes", params={"name": name}, content=PST.read_bytes() if data is None else data)


@pytest.fixture
def case(new_case):
    return new_case("Transwestern")


@pytest.fixture
def mailbox(admin, case, rt):
    r = upload(admin, case)
    assert r.status_code == 201, r.text
    mb = r.json()["mailbox"]
    assert mb["status"] == "queued"
    work(rt, ("pst",))
    return mb["id"]


def find(client, case, mailbox, **params):
    r = client.get(f"/api/cases/{case}/mailboxes/{mailbox}/search", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_reader_fields():
    f = reader.open_file(str(PST))
    assert reader.count(f) == 71
    mails = {m.subject: m for m in (reader.read(item, loc, folder) for loc, folder, item in reader.walk(f))}
    m = mails["West Texas Capacity"]
    assert m.sender == "dlsmith@pplweb.com" and m.to == ["Michelle Lokay <michelle.lokay@enron.com>"]
    assert m.cc == ["jacammarano@pplweb.com", "baveety@pplweb.com"]
    assert m.date == "2001-10-24T10:19:37-07:00" and m.folder == "lokay-m/MLOKAY (Non-Privileged)/TW-Commercial Group"
    oba = mails["New OBA's"]
    assert len(oba.attachments) == 11 and oba.attachments[0]["name"] == "OBA_27602_Citizens.doc"


def test_damaged_mailbox(tmp_path):
    bad = tmp_path / "bad.pst"
    bad.write_bytes(b"!BDN not really a pst" * 100)
    with pytest.raises(reader.MailboxError, match="can't be opened"):
        reader.open_file(str(bad))


def test_upload_index_and_search(admin, case, rt, mailbox):
    mb = admin.get(f"/api/cases/{case}/mailboxes/{mailbox}").json()
    assert (mb["status"], mb["emails"]) == ("ready", 71)
    assert {"path": "lokay-m/MLOKAY (Non-Privileged)/Personal", "messages": 47} in mb["folders"]

    r = find(admin, case, mailbox, q="capacity")
    subjects = [i["subject"] for i in r["items"]]
    assert "West Texas Capacity" in subjects and r["total"] == len(r["items"])
    hit = next(i for i in r["items"] if i["subject"] == "West Texas Capacity")
    assert "\x02" in hit["snippet"] or "Capacity" in hit["subject"]
    # Operators and filters.
    assert find(admin, case, mailbox, q='"West Texas"')["total"] >= 1
    assert find(admin, case, mailbox, q="capacity", sender="pplweb")["items"][0]["subject"] == "West Texas Capacity"
    assert find(admin, case, mailbox, attachments=True, folder="lokay-m/MLOKAY (Non-Privileged)/TW-Commercial Group")["items"][0]["attachments"] >= 1
    dated = find(admin, case, mailbox, date_from="2001-10-24", date_to="2001-10-24", sort="oldest")["items"]
    assert dated and all(i["date"].startswith("2001-10-24") for i in dated)
    newest = find(admin, case, mailbox, limit=3)["items"]
    assert newest[0]["date"] >= newest[1]["date"] >= newest[2]["date"]
    r = admin.get(f"/api/cases/{case}/mailboxes/{mailbox}/search", params={"q": "-capacity"})
    assert r.status_code == 400

    full = admin.get(f"/api/cases/{case}/mailboxes/{mailbox}/messages/{hit['id']}").json()
    assert full["cc"] == ["jacammarano@pplweb.com", "baveety@pplweb.com"] and "West Texas" in full["body"]
    log = admin.get(f"/api/cases/{case}/activity").json()["items"]
    assert {"mailbox.upload", "mailbox.index", "mailbox.search", "mailbox.view"} <= {e["action"] for e in log}


def test_add_emails_to_the_case(admin, case, rt, mailbox):
    items = find(admin, case, mailbox, q="capacity OR OBA")["items"]
    pick = [i["id"] for i in items if i["subject"] in ("West Texas Capacity", "New OBA's")]
    assert len(pick) == 2
    r = admin.post(f"/api/cases/{case}/mailboxes/{mailbox}/add", json={"messages": pick, "attachments": True})
    assert r.json() == {"queued": 2, "already_in_case": 0}
    work(rt)
    files = admin.get(f"/api/cases/{case}/files", params={"sort": "name", "order": "asc"}).json()["items"]
    emails = {f["name"]: f for f in files if f["type"] == "email"}
    assert set(emails) == {"2001-10-24 West Texas Capacity.eml", "2001-10-22 New OBA's.eml"}
    west = emails["2001-10-24 West Texas Capacity.eml"]
    assert west["folder"] == "Email/lokay/lokay-m/MLOKAY (Non-Privileged)/TW-Commercial Group"
    assert west["status"] == "converted" and west["email"]["subject"] == "West Texas Capacity"
    md = admin.get(f"/api/cases/{case}/files/{west['id']}/markdown").json()
    assert 'subject: "West Texas Capacity"' in md["front_matter"]
    # The .doc attachments can't be converted; they are listed in the email.
    oba = admin.get(f"/api/cases/{case}/files/{emails["2001-10-22 New OBA's.eml"]['id']}/markdown").json()["body"]
    assert "OBA\\_27602\\_Citizens.doc" in oba
    # Shown as in the case, and never added twice.
    marked = find(admin, case, mailbox, q="capacity")["items"]
    assert next(i for i in marked if i["subject"] == "West Texas Capacity")["in_case"] == west["id"]
    assert admin.post(f"/api/cases/{case}/mailboxes/{mailbox}/add", json={"messages": pick}).json() == {"queued": 0, "already_in_case": 2}
    log = admin.get(f"/api/cases/{case}/activity").json()["items"]
    assert sum(e["action"] == "file.from_mailbox" for e in log) == 2


def test_add_without_attachments(admin, case, rt, mailbox):
    oba = next(i for i in find(admin, case, mailbox, q="OBA")["items"] if i["subject"] == "New OBA's")
    admin.post(f"/api/cases/{case}/mailboxes/{mailbox}/add", json={"messages": [oba["id"]], "attachments": False})
    work(rt)
    f = admin.get(f"/api/cases/{case}/files").json()["items"][0]
    assert f["size"] < 20_000  # the 11 Word files stayed in the mailbox
    body = admin.get(f"/api/cases/{case}/files/{f['id']}/markdown").json()["body"]
    assert "Attachments left in the mailbox: OBA\\_27602\\_Citizens.doc" in body


def test_mailbox_permissions(admin, case, rt, mailbox, people, new_case):
    reader_ = people("rita", "readonly", [case])
    editor = people("ed", "editor", [case])
    outsider = people("otto", "editor", [new_case("Other")])
    assert find(reader_, case, mailbox, q="capacity")["total"] >= 1
    assert reader_.post(f"/api/cases/{case}/mailboxes/{mailbox}/add", json={"messages": [1]}).status_code == 403
    assert upload(reader_, case, data=b"x").status_code == 403
    for url in (f"/api/cases/{case}/mailboxes", f"/api/cases/{case}/mailboxes/{mailbox}", f"/api/cases/{case}/mailboxes/{mailbox}/search", f"/api/cases/{case}/mailboxes/{mailbox}/messages/1"):
        assert outsider.get(url).status_code == 404
    other = new_case("Third case")
    assert admin.get(f"/api/cases/{other}/mailboxes/{mailbox}/search").status_code == 404  # wrong case, same id
    assert editor.post(f"/api/cases/{case}/mailboxes/{mailbox}/delete").status_code == 403


def test_upload_rules_and_delete(admin, case, rt, mailbox):
    r = upload(admin, case, name="notes.zip", data=b"PK")
    assert r.status_code == 415 and ".pst or .ost" in r.json()["detail"]
    assert upload(admin, case).status_code == 409  # same mailbox again
    item = find(admin, case, mailbox, q="capacity")["items"][0]
    admin.post(f"/api/cases/{case}/mailboxes/{mailbox}/add", json={"messages": [item["id"]]})
    work(rt)
    assert admin.post(f"/api/cases/{case}/mailboxes/{mailbox}/delete").status_code == 204
    assert admin.get(f"/api/cases/{case}/mailboxes").json()["items"] == []
    assert not any(rt.storage.case_dir(case).joinpath("mailboxes").glob(f"{mailbox}*"))
    assert len(admin.get(f"/api/cases/{case}/files").json()["items"]) == 1  # the added email stays


def test_damaged_upload_fails_with_a_reason(admin, case, rt):
    r = upload(admin, case, name="broken.pst", data=b"not a pst" * 1000)
    mb = r.json()["mailbox"]
    work(rt, ("pst",))
    mb = admin.get(f"/api/cases/{case}/mailboxes/{mb['id']}").json()
    assert mb["status"] == "failed" and "can't be opened" in mb["error"]
    assert admin.get("/api/jobs", params={"state": "failed"}).json()["items"][0]["file"] == "broken.pst"
