"""Sign-in, sessions, CSRF, passwords and the Admin account from the launcher."""

from app import accounts
from conftest import ADMIN_PASSWORD, Client, write_admin


def test_setup_status_follows_launcher_file(cfg):
    c = Client()
    assert c.get("/api/setup").json() == {"admin_ready": False}
    write_admin(cfg)
    assert c.get("/api/setup").json() == {"admin_ready": True}


def test_admin_signs_in_with_launcher_password(cfg):
    write_admin(cfg)
    c = Client()
    r = c.sign_in("FRED", ADMIN_PASSWORD)  # usernames ignore case
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "admin"
    assert r.json()["user"]["managed_by_control_center"] is True
    cookie = r.headers["set-cookie"].lower()
    for flag in ("httponly", "secure", "samesite=strict"):
        assert flag in cookie
    assert c.get("/api/session").json()["user"]["username"] == "fred"


def test_wrong_password_and_lockout(cfg):
    write_admin(cfg)
    c = Client()
    for _ in range(5):
        assert c.sign_in("fred", "wrong password here").status_code == 401
    r = c.sign_in("fred", ADMIN_PASSWORD)
    assert r.status_code == 429
    assert "15 minutes" in r.json()["detail"]


def test_unknown_user_gets_the_same_answer(cfg):
    write_admin(cfg)
    r = Client().sign_in("nobody", "whatever whatever")
    assert r.status_code == 401
    assert r.json()["detail"] == "That username and password don't match. Check them and try again."


def test_session_required(cfg):
    c = Client()
    assert c.get("/api/cases").status_code == 401
    assert c.get("/api/session").status_code == 401


def test_csrf_token_required_for_changes(admin):
    token = admin.csrf
    admin.csrf = ""
    assert admin.post("/api/cases", json={"name": "No token"}).status_code == 403
    admin.csrf = "forged"
    assert admin.post("/api/cases", json={"name": "Bad token"}).status_code == 403
    admin.csrf = token
    r = admin.post("/api/cases", json={"name": "Other site"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert admin.post("/api/cases", json={"name": "Good"}, headers={"Origin": "https://cases.local"}).status_code == 201


def test_sign_out_ends_session(admin):
    assert admin.delete("/api/session").status_code == 204
    assert admin.get("/api/session").status_code == 401


def test_session_token_not_stored_in_clear(admin, rt):
    token = admin.http.cookies.get(accounts.COOKIE)
    with rt.db.connect() as db:
        stored = [r[0] for r in db.execute("SELECT token_hash FROM sessions")]
    assert token and token not in stored


def test_new_person_must_choose_password(admin):
    r = admin.post("/api/users", json={"username": "anna", "display_name": "Anna Lee", "role": "editor"})
    assert r.status_code == 201
    temp = r.json()["temporary_password"]
    assert len(temp) == 19
    anna = Client()
    r = anna.sign_in("anna", temp)
    assert r.json()["user"]["must_change_password"] is True
    r = anna.get("/api/cases")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "password_change_required"
    assert anna.post("/api/session/password", json={"current_password": temp, "new_password": "short"}).status_code == 400
    assert anna.post("/api/session/password", json={"current_password": "wrong", "new_password": "a long new password"}).status_code == 400
    assert anna.post("/api/session/password", json={"current_password": temp, "new_password": "a long new password"}).status_code == 200
    assert anna.get("/api/cases").status_code == 200
    # The temporary password no longer works.
    assert Client().sign_in("anna", temp).status_code == 401


def test_admin_password_changes_only_in_control_center(admin):
    r = admin.post("/api/session/password", json={"current_password": ADMIN_PASSWORD, "new_password": "something else entirely"})
    assert r.status_code == 409


def test_new_admin_password_from_launcher_signs_out_old_sessions(admin, cfg):
    write_admin(cfg, password_hash=accounts.hash_password("a brand new admin password"))
    assert Client().get("/api/setup").json()["admin_ready"]
    assert admin.get("/api/session").status_code == 401
    assert Client().sign_in("fred", ADMIN_PASSWORD).status_code == 401
    assert Client().sign_in("fred", "a brand new admin password").status_code == 200


def test_launcher_admin_cannot_take_over_existing_username(admin, cfg, people):
    people("maria", "editor")
    write_admin(cfg, username="maria")
    Client().get("/api/setup")
    r = Client().sign_in("maria", ADMIN_PASSWORD)
    assert r.status_code == 401  # maria's own password still applies
    assert Client().sign_in("fred", ADMIN_PASSWORD).status_code == 200


def test_bad_launcher_file_is_ignored(cfg):
    write_admin(cfg, password_hash="plain text")
    assert Client().get("/api/setup").json() == {"admin_ready": False}


def test_switched_off_person_is_signed_out(admin, people):
    bob = people("bob", "readonly")
    assert admin.patch(f"/api/users/{bob.user_id}", json={"active": False}).status_code == 200
    assert bob.get("/api/cases").status_code == 401
    assert Client().sign_in("bob", "bob chooses a long password").status_code == 403


def test_reset_password_and_unlock(admin, people):
    bob = people("bob", "editor")
    for _ in range(5):
        Client("10.0.0.9").sign_in("bob", "wrong password!!")
    users = {u["username"]: u for u in admin.get("/api/users").json()["items"]}
    assert users["bob"]["locked"] is True
    assert admin.post(f"/api/users/{bob.user_id}/unlock").json()["locked"] is False
    r = admin.post(f"/api/users/{bob.user_id}/reset-password")
    temp = r.json()["temporary_password"]
    assert bob.get("/api/cases").status_code == 401  # old session ended
    assert Client().sign_in("bob", temp).json()["user"]["must_change_password"] is True


def test_admin_account_protected(admin):
    me = admin.get("/api/session").json()["user"]["id"]
    assert admin.patch(f"/api/users/{me}", json={"role": "editor"}).status_code == 409
    assert admin.patch(f"/api/users/{me}", json={"active": False}).status_code == 409
    assert admin.post(f"/api/users/{me}/reset-password").status_code == 409


def test_only_admin_manages_people(people):
    anna = people("anna", "editor")
    assert anna.get("/api/users").status_code == 403
    assert anna.post("/api/users", json={"username": "x1", "display_name": "X", "role": "editor"}).status_code == 403
    assert anna.get("/api/audit").status_code == 403


def test_people_cannot_be_made_admin(admin):
    r = admin.post("/api/users", json={"username": "eve", "display_name": "Eve", "role": "admin"})
    assert r.status_code == 422


def test_duplicate_and_bad_usernames(admin):
    assert admin.post("/api/users", json={"username": "anna", "display_name": "A", "role": "editor"}).status_code == 201
    assert admin.post("/api/users", json={"username": "ANNA", "display_name": "A", "role": "editor"}).status_code == 409
    assert admin.post("/api/users", json={"username": "bad name", "display_name": "A", "role": "editor"}).status_code == 400
