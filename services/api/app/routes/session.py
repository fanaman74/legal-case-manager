"""Sign in, sign out, and change your own password."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .. import accounts, audit, runtime
from ..accounts import Session

router = APIRouter(prefix="/api")


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(max_length=64)
    password: str = Field(max_length=1024)


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(max_length=1024)
    new_password: str = Field(max_length=1024)


def _session_view(user: dict, csrf: str) -> dict:
    return {"user": accounts.user_view(user), "csrf": csrf}


@router.get("/setup")
def setup_status(rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    """Whether the Admin account exists yet. The sign-in page uses this to
    send people to the Control Center's setup wizard first."""
    return {"admin_ready": accounts.sync_admin(rt)}


@router.get("/session")
def get_session(sess: Session = Depends(accounts.session)) -> dict:
    return _session_view(sess.user, sess.csrf)


@router.post("/session")
def sign_in(body: Credentials, request: Request, response: Response, rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    ip = accounts.client_ip(request)
    keys = (f"ip|{ip}", f"user|{body.username.lower()}")
    if rt.throttle.locked(*keys):
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes, or ask the Admin to unlock your account.")
    accounts.sync_admin(rt)
    with rt.db.connect() as db:
        user = db.execute("SELECT * FROM users WHERE username = ?", (body.username,)).fetchone()
    ok = accounts.verify_password(user["password_hash"] if user else accounts._DUMMY_HASH, body.password) and user is not None
    if not ok:
        rt.throttle.fail(*keys)
        with rt.db.transaction() as db:
            audit.record(db, action="auth.sign_in", user=None, username=body.username[:64], outcome="denied", ip=ip, detail="wrong username or password")
        raise HTTPException(401, "That username and password don't match. Check them and try again.")
    if not user["active"]:
        with rt.db.transaction() as db:
            audit.record(db, action="auth.sign_in", user=dict(user), outcome="denied", ip=ip, detail="account switched off")
        raise HTTPException(403, "This account is switched off. Ask the Admin to switch it back on.")
    rt.throttle.clear(*keys)
    with rt.db.transaction() as db:
        token, csrf = accounts.start_session(db, user["id"])
        db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (audit.now(), user["id"]))
        audit.record(db, action="auth.sign_in", user=dict(user), ip=ip)
    response.set_cookie(accounts.COOKIE, token, httponly=True, secure=True, samesite="strict", path="/")
    return _session_view(dict(user), csrf)


@router.delete("/session", status_code=204)
def sign_out(request: Request, response: Response, sess: Session = Depends(accounts.session), rt: runtime.Runtime = Depends(runtime.get)) -> None:
    with rt.db.transaction() as db:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (sess.token_hash,))
        audit.record(db, action="auth.sign_out", user=sess.user, ip=accounts.client_ip(request))
    response.delete_cookie(accounts.COOKIE, path="/", secure=True, httponly=True, samesite="strict")


@router.post("/session/password")
def change_password(body: PasswordChange, request: Request, sess: Session = Depends(accounts.session), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    user = sess.user
    if user["provisioned"]:
        raise HTTPException(409, "The Admin password is changed in the Control Center on the host computer.")
    if not accounts.verify_password(user["password_hash"], body.current_password):
        raise HTTPException(400, "Your current password isn't right. Check it and try again.")
    if len(body.new_password) < accounts.MIN_PASSWORD:
        raise HTTPException(400, f"Use at least {accounts.MIN_PASSWORD} characters. A short sentence works well.")
    if body.new_password == body.current_password:
        raise HTTPException(400, "Choose a password different from the current one.")
    with rt.db.transaction() as db:
        db.execute("UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?", (accounts.hash_password(body.new_password), user["id"]))
        # Other browsers signed in as this person are signed out.
        db.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user["id"], sess.token_hash))
        audit.record(db, action="auth.password_changed", user=user, ip=accounts.client_ip(request))
    return _session_view({**user, "must_change_password": 0}, sess.csrf)
