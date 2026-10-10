"""Sign-in, sessions and roles.

Passwords are hashed with Argon2id. Sessions are random tokens in an HttpOnly,
Secure, SameSite=Strict cookie; only a SHA-256 of the token is stored, so a
copy of app.db can't be used to sign in. Every request that changes something
must carry the session's CSRF token in a header and, when the browser sends
one, an Origin matching the app's own address.

The Admin account is created in the Control Center's setup wizard. The
launcher hands its username and password hash to the app through
admin-account.json, so there is one Admin password."""

import hashlib
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request

from . import audit, runtime

COOKIE = "cfm_session"
CSRF_HEADER = "x-csrf-token"
SESSION_IDLE = 8 * 3600
SESSION_MAX = 7 * 24 * 3600
MIN_PASSWORD = 12
ROLES = ("admin", "editor", "readonly")

# Same cost as the launcher (launcher/internal/auth/auth.go).
_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)
# Verified against when the username doesn't exist, so timing doesn't reveal it.
_DUMMY_HASH = _hasher.hash(secrets.token_hex(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(encoded: str, password: str) -> bool:
    try:
        return _hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def valid_username(name: str) -> bool:
    """Same rule as the launcher: 2 to 64 letters, digits, . _ - @"""
    return 2 <= len(name) <= 64 and all(c.isascii() and (c.isalnum() or c in "._-@") for c in name)


def temporary_password() -> str:
    """Four groups of four from an alphabet without look-alike characters,
    easy to read out or type: e.g. 7kqm-3vxa-hn2p-wt9e (19 characters)."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def user_view(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "must_change_password": bool(row["must_change_password"]),
        "managed_by_control_center": bool(row["provisioned"]),
    }


# --- Admin account from the launcher -----------------------------------------


def sync_admin(rt: runtime.Runtime) -> bool:
    """Create or update the Admin from the launcher's admin-account.json.
    Returns whether an Admin exists."""
    try:
        data = json.loads(rt.cfg.admin_account_file.read_text(encoding="utf-8"))
        username, pw_hash = data["username"], data["password_hash"]
        if not (isinstance(username, str) and valid_username(username) and isinstance(pw_hash, str) and pw_hash.startswith("$argon2id$")):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        username = pw_hash = None
    with rt.db.connect() as db:
        current = db.execute("SELECT * FROM users WHERE provisioned = 1").fetchone()
    if username is None:
        return current is not None
    if current and current["username"] == username and current["password_hash"] == pw_hash:
        return True
    with rt.db.transaction() as db:
        current = db.execute("SELECT * FROM users WHERE provisioned = 1").fetchone()
        clash = db.execute("SELECT id FROM users WHERE username = ? AND provisioned = 0", (username,)).fetchone()
        if clash:
            # Someone already has that username; keep the existing Admin and
            # say so in the audit log rather than merge two people.
            audit.record(db, action="admin.sync", user=None, username="control-center", outcome="refused", target=username, detail="username already used by another person")
            return current is not None
        if current:
            db.execute("UPDATE users SET username = ?, password_hash = ? WHERE id = ?", (username, pw_hash, current["id"]))
            if current["password_hash"] != pw_hash:
                db.execute("DELETE FROM sessions WHERE user_id = ?", (current["id"],))
            audit.record(db, action="admin.sync", user=None, username="control-center", target=username, detail="Admin account updated from the Control Center")
        else:
            db.execute(
                "INSERT INTO users (username, display_name, role, password_hash, provisioned, created_at) VALUES (?, ?, 'admin', ?, 1, ?)",
                (username, username, pw_hash, audit.now()),
            )
            audit.record(db, action="admin.sync", user=None, username="control-center", target=username, detail="Admin account created in the Control Center")
    return True


# --- Sessions ----------------------------------------------------------------


@dataclass
class Session:
    user: dict
    csrf: str
    token_hash: str


def start_session(db: sqlite3.Connection, user_id: int) -> tuple[str, str]:
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now = time.time()
    db.execute("DELETE FROM sessions WHERE last_seen < ? OR created_at < ?", (now - SESSION_IDLE, now - SESSION_MAX))
    db.execute("INSERT INTO sessions (token_hash, user_id, csrf, created_at, last_seen) VALUES (?, ?, ?, ?, ?)", (_token_hash(token), user_id, csrf, now, now))
    return token, csrf


def _lookup(rt: runtime.Runtime, token: str | None) -> Session | None:
    if not token:
        return None
    th = _token_hash(token)
    now = time.time()
    with rt.db.connect() as db:
        row = db.execute(
            "SELECT s.csrf, s.created_at, s.last_seen, u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
            (th,),
        ).fetchone()
        if row is None:
            return None
        if not row["active"] or now - row["last_seen"] > SESSION_IDLE or now - row["created_at"] > SESSION_MAX:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (th,))
            return None
        if now - row["last_seen"] > 60:
            db.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now, th))
    return Session(user=dict(row), csrf=row["csrf"], token_hash=th)


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        return True  # not sent by every browser on same-origin requests; CSRF token still required
    host = request.headers.get("host", "")
    parts = urlsplit(origin)
    return parts.scheme in ("https", "http") and parts.netloc.lower() == host.lower()


def session(request: Request, rt: runtime.Runtime = Depends(runtime.get)) -> Session:
    """The signed-in session, with CSRF and Origin checks on every request
    that changes something."""
    sess = _lookup(rt, request.cookies.get(COOKIE))
    if sess is None:
        raise HTTPException(401, "Sign in to continue.")
    if request.method not in ("GET", "HEAD"):
        sent = request.headers.get(CSRF_HEADER, "")
        if not (_same_origin(request) and sent and secrets.compare_digest(sent, sess.csrf)):
            raise HTTPException(403, "This request didn't come from the app. Reload the page and try again.")
    return sess


def current_user(sess: Session = Depends(session)) -> dict:
    """The signed-in user, who has already chosen their own password."""
    if sess.user["must_change_password"]:
        raise HTTPException(403, detail={"code": "password_change_required", "message": "Choose your own password to continue."})
    return sess.user


def admin_user(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "Only the Admin can do this.")
    return user
