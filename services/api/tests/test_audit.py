"""The audit log records who did what, and detects tampering."""

from conftest import Client


def test_actions_are_recorded(admin, people, new_case):
    cid = new_case("Smith v Jones")
    anna = people("anna", "editor", [cid])
    fid = anna.upload(cid, "a.pdf", b"%PDF a").json()["file"]["id"]
    anna.upload(cid, "copy.pdf", b"%PDF a")
    anna.get(f"/api/cases/{cid}/files/{fid}/original")
    Client().sign_in("anna", "wrong password!!")

    log = admin.get("/api/audit").json()
    seen = [(e["username"], e["action"], e["outcome"]) for e in reversed(log["items"])]
    for expected in [
        ("control-center", "admin.sync", "ok"),
        ("fred", "auth.sign_in", "ok"),
        ("fred", "case.create", "ok"),
        ("fred", "user.create", "ok"),
        ("anna", "auth.password_changed", "ok"),
        ("anna", "file.upload", "ok"),
        ("anna", "file.duplicate", "refused"),
        ("anna", "file.download", "ok"),
        ("anna", "auth.sign_in", "denied"),
    ]:
        assert expected in seen, expected
    upload = next(e for e in log["items"] if e["action"] == "file.upload")
    assert upload["case_name"] == "Smith v Jones" and upload["target"] == "a.pdf"
    assert log["tamper_check"] == {"entries": len(seen), "intact": True, "first_bad_entry": None}


def test_filters(admin, new_case):
    a, b = new_case("A"), new_case("B")
    admin.upload(a, "x.pdf", b"%PDF")
    assert {e["case_id"] for e in admin.get("/api/audit", params={"case_id": b}).json()["items"]} == {b}
    assert {e["action"] for e in admin.get("/api/audit", params={"action": "file."}).json()["items"]} == {"file.upload"}
    assert admin.get("/api/audit", params={"username": "nobody"}).json()["items"] == []
    page = admin.get("/api/audit", params={"limit": 2}).json()["items"]
    older = admin.get("/api/audit", params={"limit": 2, "before": page[-1]["id"]}).json()["items"]
    assert older[0]["id"] < page[-1]["id"]


def test_tampering_detected(admin, new_case, rt):
    new_case("A")
    with rt.db.connect() as db:
        target = db.execute("SELECT id FROM audit WHERE action = 'case.create'").fetchone()[0]
        db.execute("UPDATE audit SET username = 'someone-else' WHERE id = ?", (target,))
    check = admin.get("/api/audit").json()["tamper_check"]
    assert check["intact"] is False and check["first_bad_entry"] == target


def test_deleted_entry_detected(admin, new_case, rt):
    new_case("A")
    new_case("B")
    with rt.db.connect() as db:
        db.execute("DELETE FROM audit WHERE action = 'case.create' AND target = 'A'")
    assert admin.get("/api/audit").json()["tamper_check"]["intact"] is False
