"""Cases, roles and case isolation. A person must never see a case they
aren't assigned to, through any route."""

import pytest


def test_admin_creates_and_lists_cases(admin, new_case):
    assert admin.get("/api/cases").json()["items"] == []
    cid = new_case("Smith v Jones", reference="2026/014", client="Smith Ltd")
    items = admin.get("/api/cases").json()["items"]
    assert [(c["id"], c["name"], c["reference"], c["client"], c["file_count"], c["member_count"]) for c in items] == [(cid, "Smith v Jones", "2026/014", "Smith Ltd", 0, 0)]
    case = admin.get(f"/api/cases/{cid}").json()
    assert case["permissions"] == {"edit": True, "upload": True, "manage": True}
    assert case["created_by"] == "fred"


def test_blank_name_refused(admin):
    assert admin.post("/api/cases", json={"name": "   "}).status_code == 400
    assert admin.post("/api/cases", json={"name": ""}).status_code == 422


def test_unknown_fields_refused(admin):
    assert admin.post("/api/cases", json={"name": "A", "status": "archived"}).status_code == 422


def test_assigned_cases_only(admin, people, new_case):
    mine, other = new_case("Mine"), new_case("Not mine")
    anna = people("anna", "editor", [mine])
    assert [c["id"] for c in anna.get("/api/cases").json()["items"]] == [mine]
    assert anna.get(f"/api/cases/{mine}").status_code == 200

    hidden = anna.get(f"/api/cases/{other}")
    unknown = anna.get("/api/cases/" + "0" * 32)
    assert hidden.status_code == unknown.status_code == 404
    assert hidden.json() == unknown.json()  # can't tell a hidden case from a missing one


def test_isolation_on_every_case_route(admin, people, new_case):
    other = new_case("Not mine")
    admin.upload(other, "secret.pdf", b"%PDF secret")
    fid = admin.get(f"/api/cases/{other}/files").json()["items"][0]["id"]
    anna = people("anna", "editor")
    routes = [
        ("GET", f"/api/cases/{other}", None),
        ("PATCH", f"/api/cases/{other}", {"notes": "x"}),
        ("GET", f"/api/cases/{other}/members", None),
        ("GET", f"/api/cases/{other}/activity", None),
        ("GET", f"/api/cases/{other}/files", None),
        ("GET", f"/api/cases/{other}/folders", None),
        ("GET", f"/api/cases/{other}/files/{fid}", None),
        ("PATCH", f"/api/cases/{other}/files/{fid}", {"tags": ["x"]}),
        ("GET", f"/api/cases/{other}/files/{fid}/original", None),
    ]
    for method, url, body in routes:
        r = anna.request(method, url, json=body) if body else anna.request(method, url)
        assert r.status_code == 404, (method, url, r.status_code)
    assert anna.upload(other, "x.pdf", b"x").status_code == 404
    assert len(admin.get(f"/api/cases/{other}/files").json()["items"]) == 1


def test_file_from_another_case_not_reachable_through_own_case(admin, people, new_case):
    mine, other = new_case("Mine"), new_case("Other")
    admin.upload(other, "secret.pdf", b"%PDF secret")
    fid = admin.get(f"/api/cases/{other}/files").json()["items"][0]["id"]
    anna = people("anna", "readonly", [mine])
    assert anna.get(f"/api/cases/{mine}/files/{fid}").status_code == 404
    assert anna.get(f"/api/cases/{mine}/files/{fid}/original").status_code == 404


def test_editor_rights(admin, people, new_case):
    cid = new_case("Case A")
    anna = people("anna", "editor", [cid])
    assert anna.get(f"/api/cases/{cid}").json()["permissions"] == {"edit": True, "upload": True, "manage": False}
    assert anna.patch(f"/api/cases/{cid}", json={"notes": "Hearing on 3 Nov", "client": "Smith Ltd"}).status_code == 200
    assert anna.patch(f"/api/cases/{cid}", json={"name": "Renamed"}).status_code == 403
    assert anna.post("/api/cases", json={"name": "New"}).status_code == 403
    assert anna.post(f"/api/cases/{cid}/archive").status_code == 403
    assert anna.post(f"/api/cases/{cid}/delete", json={"confirm_name": "Case A"}).status_code == 403
    assert anna.put(f"/api/cases/{cid}/members", json={"user_ids": []}).status_code == 403
    assert anna.upload(cid, "a.pdf", b"%PDF a").status_code == 201


def test_readonly_rights(admin, people, new_case):
    cid = new_case("Case A")
    admin.upload(cid, "a.pdf", b"%PDF a")
    bob = people("bob", "readonly", [cid])
    assert bob.get(f"/api/cases/{cid}").json()["permissions"] == {"edit": False, "upload": False, "manage": False}
    assert bob.patch(f"/api/cases/{cid}", json={"notes": "x"}).status_code == 403
    assert bob.upload(cid, "b.pdf", b"%PDF b").status_code == 403
    fid = bob.get(f"/api/cases/{cid}/files").json()["items"][0]["id"]
    assert bob.patch(f"/api/cases/{cid}/files/{fid}", json={"tags": ["x"]}).status_code == 403
    assert bob.get(f"/api/cases/{cid}/files/{fid}/original").content == b"%PDF a"


def test_members_from_both_sides(admin, people, new_case):
    a, b = new_case("A"), new_case("B")
    anna = people("anna", "editor")
    r = admin.put(f"/api/cases/{a}/members", json={"user_ids": [anna.user_id]})
    assert [m["username"] for m in r.json()["items"]] == ["anna"]
    r = admin.put(f"/api/users/{anna.user_id}/cases", json={"case_ids": [b]})
    assert r.json()["case_ids"] == [b]
    assert [c["id"] for c in anna.get("/api/cases").json()["items"]] == [b]
    assert admin.put(f"/api/users/{anna.user_id}/cases", json={"case_ids": ["f" * 32]}).status_code == 400
    me = admin.get("/api/session").json()["user"]["id"]
    assert admin.put(f"/api/cases/{a}/members", json={"user_ids": [me]}).status_code == 400


def test_removed_member_loses_access(admin, people, new_case):
    cid = new_case("A")
    anna = people("anna", "editor", [cid])
    admin.put(f"/api/cases/{cid}/members", json={"user_ids": []})
    assert anna.get(f"/api/cases/{cid}").status_code == 404


def test_archive_and_restore(admin, people, new_case):
    cid = new_case("Old matter")
    anna = people("anna", "editor", [cid])
    assert admin.post(f"/api/cases/{cid}/archive").json()["status"] == "archived"
    assert anna.get("/api/cases").json()["items"] == []
    assert [c["id"] for c in anna.get("/api/cases", params={"archived": True}).json()["items"]] == [cid]
    r = anna.upload(cid, "late.pdf", b"%PDF late")
    assert r.status_code == 409 and "archived" in r.json()["detail"]
    assert anna.patch(f"/api/cases/{cid}", json={"notes": "x"}).status_code == 409
    assert admin.post(f"/api/cases/{cid}/restore").json()["status"] == "active"
    assert anna.upload(cid, "late.pdf", b"%PDF late").status_code == 201


def test_delete_case(admin, new_case, cfg):
    cid = new_case("Delete me")
    admin.upload(cid, "a.pdf", b"%PDF a")
    folder = cfg.cases_dir / cid
    assert folder.is_dir()
    assert admin.post(f"/api/cases/{cid}/delete", json={"confirm_name": "Delete Me"}).status_code == 400
    assert admin.post(f"/api/cases/{cid}/delete", json={"confirm_name": "Delete me"}).status_code == 204
    assert not folder.exists()
    assert not list(cfg.cases_dir.glob(".deleted-*"))
    assert admin.get(f"/api/cases/{cid}").status_code == 404
    entries = admin.get("/api/audit", params={"case_id": cid}).json()["items"]
    assert entries[0]["action"] == "case.delete" and entries[0]["target"] == "Delete me"


@pytest.mark.parametrize("bad", ["../escape", "a" * 33, "ABCDEF" + "0" * 26])
def test_malformed_case_ids(admin, bad):
    assert admin.get(f"/api/cases/{bad}").status_code in (404, 405)
    assert admin.put(f"/api/cases/{bad}/files", params={"path": "a.pdf"}, content=b"x").status_code in (404, 405)


def test_activity_shows_case_history(admin, people, new_case):
    cid = new_case("A")
    anna = people("anna", "editor", [cid])
    anna.upload(cid, "a.pdf", b"%PDF a")
    actions = [e["action"] for e in anna.get(f"/api/cases/{cid}/activity").json()["items"]]
    assert actions[0] == "file.upload"
    assert actions[-1] == "case.create"
