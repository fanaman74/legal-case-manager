"""People (Admin only): add, change role, switch off, reset password, unlock,
and which cases each person is assigned to."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import accounts, audit, runtime

router = APIRouter(prefix="/api/users")

Role = Literal["editor", "readonly"]


class NewUser(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(max_length=64)
    display_name: str = Field(min_length=1, max_length=100)
    role: Role
    case_ids: list[str] = Field(default_factory=list, max_length=1000)


class UserEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(None, min_length=1, max_length=100)
    role: Role | None = None
    active: bool | None = None


class CaseList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_ids: list[str] = Field(max_length=1000)


def _view(db, rt: runtime.Runtime, row) -> dict:
    cases = [r[0] for r in db.execute("SELECT case_id FROM case_members WHERE user_id = ? ORDER BY case_id", (row["id"],))]
    return {
        **accounts.user_view(row),
        "active": bool(row["active"]),
        "locked": rt.throttle.locked(f"user|{row['username'].lower()}"),
        "last_login_at": row["last_login_at"],
        "created_at": row["created_at"],
        "case_ids": cases,
    }


def _target(db, user_id: int, *, not_admin: str | None = None):
    row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "That person doesn't exist. Reload the page.")
    if not_admin and row["role"] == "admin":
        raise HTTPException(409, not_admin)
    return row


def _check_cases(db, case_ids: list[str]) -> list[str]:
    ids = sorted(set(case_ids))
    if ids and db.execute(f"SELECT COUNT(*) FROM cases WHERE id IN ({','.join('?' * len(ids))})", ids).fetchone()[0] != len(ids):
        raise HTTPException(400, "One of those cases doesn't exist. Reload the page and try again.")
    return ids


@router.get("")
def list_users(_: dict = Depends(accounts.admin_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    with rt.db.connect() as db:
        rows = db.execute("SELECT * FROM users ORDER BY role = 'admin' DESC, display_name COLLATE NOCASE").fetchall()
        return {"items": [_view(db, rt, r) for r in rows]}


@router.post("", status_code=201)
def add_user(body: NewUser, request: Request, admin: dict = Depends(accounts.admin_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    """Creates the account with a temporary password, shown once. The person
    chooses their own password the first time they sign in."""
    if not accounts.valid_username(body.username):
        raise HTTPException(400, "Use 2 to 64 letters, numbers, dots, dashes, underscores or @ for the username.")
    display = body.display_name.strip()
    if not display:
        raise HTTPException(400, "Enter the person's name.")
    temp = accounts.temporary_password()
    with rt.db.transaction() as db:
        if db.execute("SELECT 1 FROM users WHERE username = ?", (body.username,)).fetchone():
            raise HTTPException(409, "Someone already has that username. Choose another.")
        cases = _check_cases(db, body.case_ids)
        cur = db.execute(
            "INSERT INTO users (username, display_name, role, password_hash, must_change_password, created_at) VALUES (?, ?, ?, ?, 1, ?)",
            (body.username, display, body.role, accounts.hash_password(temp), audit.now()),
        )
        uid = cur.lastrowid
        db.executemany("INSERT INTO case_members (case_id, user_id) VALUES (?, ?)", [(c, uid) for c in cases])
        audit.record(db, action="user.create", user=admin, target=body.username, detail=f"role {body.role}; {len(cases)} cases", ip=accounts.client_ip(request))
        row = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        return {"user": _view(db, rt, row), "temporary_password": temp}


@router.patch("/{user_id}")
def edit_user(user_id: int, body: UserEdit, request: Request, admin: dict = Depends(accounts.admin_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    changes = body.model_dump(exclude_none=True)
    with rt.db.transaction() as db:
        row = _target(db, user_id)
        if row["role"] == "admin" and ("role" in changes or changes.get("active") is False):
            raise HTTPException(409, "The Admin account can't be switched off or given another role.")
        if "display_name" in changes:
            changes["display_name"] = changes["display_name"].strip()
            if not changes["display_name"]:
                raise HTTPException(400, "Enter the person's name.")
        if "active" in changes:
            changes["active"] = int(changes["active"])
        changed = {k: v for k, v in changes.items() if row[k] != v}
        if changed:
            db.execute(f"UPDATE users SET {', '.join(f'{k} = ?' for k in changed)} WHERE id = ?", [*changed.values(), user_id])
            if changed.get("active") == 0 or "role" in changed:
                db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            detail = "; ".join(f"{k} {v}" for k, v in changed.items())
            audit.record(db, action="user.edit", user=admin, target=row["username"], detail=detail, ip=accounts.client_ip(request))
        return _view(db, rt, db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


@router.post("/{user_id}/reset-password")
def reset_password(user_id: int, request: Request, admin: dict = Depends(accounts.admin_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    temp = accounts.temporary_password()
    with rt.db.transaction() as db:
        row = _target(db, user_id, not_admin="The Admin password is changed in the Control Center on the host computer.")
        db.execute("UPDATE users SET password_hash = ?, must_change_password = 1 WHERE id = ?", (accounts.hash_password(temp), user_id))
        db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        audit.record(db, action="user.reset_password", user=admin, target=row["username"], ip=accounts.client_ip(request))
        rt.throttle.clear(f"user|{row['username'].lower()}")
        return {"user": _view(db, rt, row), "temporary_password": temp}


@router.post("/{user_id}/unlock")
def unlock(user_id: int, request: Request, admin: dict = Depends(accounts.admin_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    with rt.db.transaction() as db:
        row = _target(db, user_id)
        rt.throttle.clear(f"user|{row['username'].lower()}")
        audit.record(db, action="user.unlock", user=admin, target=row["username"], ip=accounts.client_ip(request))
        return _view(db, rt, row)


@router.put("/{user_id}/cases")
def set_cases(user_id: int, body: CaseList, request: Request, admin: dict = Depends(accounts.admin_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    with rt.db.transaction() as db:
        row = _target(db, user_id, not_admin="The Admin already sees every case.")
        cases = _check_cases(db, body.case_ids)
        before = {r[0] for r in db.execute("SELECT case_id FROM case_members WHERE user_id = ?", (user_id,))}
        added, removed = set(cases) - before, before - set(cases)
        db.executemany("DELETE FROM case_members WHERE case_id = ? AND user_id = ?", [(c, user_id) for c in removed])
        db.executemany("INSERT INTO case_members (case_id, user_id) VALUES (?, ?)", [(c, user_id) for c in added])
        names = dict(db.execute("SELECT id, name FROM cases").fetchall())
        for c in added:
            audit.record(db, action="case.members", user=admin, case_id=c, target=names[c], detail=f"added {row['username']}", ip=accounts.client_ip(request))
        for c in removed:
            audit.record(db, action="case.members", user=admin, case_id=c, target=names[c], detail=f"removed {row['username']}", ip=accounts.client_ip(request))
        return _view(db, rt, row)
