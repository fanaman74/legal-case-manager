"""Cases: list, create, view, edit, archive, delete, and who is assigned."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import access, accounts, audit, runtime

router = APIRouter(prefix="/api/cases")


class NewCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    reference: str = Field("", max_length=100)
    client: str = Field("", max_length=200)
    description: str = Field("", max_length=5000)
    member_ids: list[int] = Field(default_factory=list, max_length=500)


class CaseEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, min_length=1, max_length=200)
    reference: str | None = Field(None, max_length=100)
    client: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=5000)
    notes: str | None = Field(None, max_length=100_000)


class Members(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_ids: list[int] = Field(max_length=500)


class ConfirmDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_name: str = Field(max_length=200)


def _case_view(row, **extra) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "reference": row["reference"],
        "client": row["client"],
        "description": row["description"],
        "notes": row["notes"],
        "status": row["status"],
        "created_at": row["created_at"],
        **extra,
    }


def _check_members(db, user_ids: list[int]) -> list[int]:
    ids = sorted(set(user_ids))
    if not ids:
        return ids
    rows = db.execute(f"SELECT id, role FROM users WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    if len(rows) != len(ids):
        raise HTTPException(400, "One of those people doesn't exist. Reload the page and try again.")
    if any(r["role"] == "admin" for r in rows):
        raise HTTPException(400, "The Admin already sees every case, so doesn't need to be added.")
    return ids


@router.get("")
def list_cases(
    archived: bool = Query(False, description="Include archived cases"),
    user: dict = Depends(accounts.current_user),
    rt: runtime.Runtime = Depends(runtime.get),
) -> dict:
    where, args = [], []
    with rt.db.connect() as db:
        allowed = access.case_ids_for(db, user)
        if allowed is not None:
            where.append(f"c.id IN (SELECT case_id FROM case_members WHERE user_id = ?)")
            args.append(user["id"])
        if not archived:
            where.append("c.status = 'active'")
        rows = db.execute(
            f"""SELECT c.*,
                  (SELECT COUNT(*) FROM files f WHERE f.case_id = c.id) AS file_count,
                  (SELECT COUNT(*) FROM case_members m WHERE m.case_id = c.id) AS member_count
                FROM cases c {'WHERE ' + ' AND '.join(where) if where else ''}
                ORDER BY c.created_at DESC""",
            args,
        ).fetchall()
    return {"items": [_case_view(r, file_count=r["file_count"], member_count=r["member_count"]) for r in rows]}


@router.post("", status_code=201)
def create_case(body: NewCase, request: Request, user: dict = Depends(accounts.admin_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    case_id = uuid.uuid4().hex
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Give the case a name.")
    rt.storage.create_case(case_id)
    with rt.db.transaction() as db:
        members = _check_members(db, body.member_ids)
        db.execute(
            "INSERT INTO cases (id, name, reference, client, description, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (case_id, name, body.reference.strip(), body.client.strip(), body.description, audit.now(), user["id"]),
        )
        db.executemany("INSERT INTO case_members (case_id, user_id) VALUES (?, ?)", [(case_id, m) for m in members])
        audit.record(db, action="case.create", user=user, case_id=case_id, target=name, ip=accounts.client_ip(request))
        row = db.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    return _case_view(row, permissions=access.permissions(user, row))


@router.get("/{case_id}")
def get_case(case_id: str, request: Request, user: dict = Depends(accounts.current_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    with rt.db.transaction() as db:
        row = access.visible_case(db, user, case_id)
        audit.record(db, action="case.view", user=user, case_id=case_id, target=row["name"], ip=accounts.client_ip(request))
        creator = db.execute("SELECT display_name FROM users WHERE id = ?", (row["created_by"],)).fetchone()
    return _case_view(row, created_by=creator["display_name"] if creator else "", permissions=access.permissions(user, row))


@router.patch("/{case_id}")
def edit_case(case_id: str, body: CaseEdit, request: Request, user: dict = Depends(accounts.current_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    changes = body.model_dump(exclude_none=True)
    if "name" in changes:
        changes["name"] = changes["name"].strip()
        if not changes["name"]:
            raise HTTPException(400, "Give the case a name.")
    with rt.db.transaction() as db:
        row = access.visible_case(db, user, case_id)
        access.require_edit(user, row)
        if "name" in changes and changes["name"] != row["name"] and user["role"] != "admin":
            raise HTTPException(403, "Only the Admin can rename a case.")
        changed = {k: v for k, v in changes.items() if row[k] != v}
        if changed:
            db.execute(f"UPDATE cases SET {', '.join(f'{k} = ?' for k in changed)} WHERE id = ?", [*changed.values(), case_id])
            detail = f"renamed from {row['name']!r}" if "name" in changed else ""
            audit.record(db, action="case.edit", user=user, case_id=case_id, target=", ".join(sorted(changed)), detail=detail, ip=accounts.client_ip(request))
        row = db.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    return _case_view(row, permissions=access.permissions(user, row))


def _set_status(case_id: str, status: Literal["active", "archived"], request: Request, user: dict, rt: runtime.Runtime) -> dict:
    with rt.db.transaction() as db:
        row = access.visible_case(db, user, case_id)
        if row["status"] != status:
            db.execute("UPDATE cases SET status = ? WHERE id = ?", (status, case_id))
            audit.record(db, action="case.archive" if status == "archived" else "case.restore", user=user, case_id=case_id, target=row["name"], ip=accounts.client_ip(request))
        row = db.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    return _case_view(row, permissions=access.permissions(user, row))


@router.post("/{case_id}/archive")
def archive_case(case_id: str, request: Request, user: dict = Depends(accounts.admin_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    return _set_status(case_id, "archived", request, user, rt)


@router.post("/{case_id}/restore")
def restore_case(case_id: str, request: Request, user: dict = Depends(accounts.admin_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    return _set_status(case_id, "active", request, user, rt)


@router.post("/{case_id}/delete", status_code=204)
def delete_case(case_id: str, body: ConfirmDelete, request: Request, user: dict = Depends(accounts.admin_user), rt: runtime.Runtime = Depends(runtime.get)) -> None:
    """Deletes the case, its files on disk and its records. The audit log
    keeps its entries for the case."""
    with rt.db.transaction() as db:
        row = access.visible_case(db, user, case_id)
        if body.confirm_name.strip() != row["name"]:
            raise HTTPException(400, "Type the case name exactly to confirm.")
        files = db.execute("SELECT COUNT(*) FROM files WHERE case_id = ?", (case_id,)).fetchone()[0]
        db.execute("DELETE FROM cases WHERE id = ?", (case_id,))
        audit.record(db, action="case.delete", user=user, case_id=case_id, target=row["name"], detail=f"{files} files deleted", ip=accounts.client_ip(request))
        try:
            rt.storage.trash_case(case_id)
        except OSError as exc:
            raise HTTPException(409, "The case's files are in use, so the case was kept. Close any program using them and try again.") from exc
    rt.storage.empty_trash()


@router.get("/{case_id}/members")
def list_members(case_id: str, user: dict = Depends(accounts.current_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    with rt.db.connect() as db:
        access.visible_case(db, user, case_id)
        rows = db.execute(
            "SELECT u.* FROM users u JOIN case_members m ON m.user_id = u.id WHERE m.case_id = ? ORDER BY u.display_name COLLATE NOCASE",
            (case_id,),
        ).fetchall()
    return {"items": [{"id": r["id"], "display_name": r["display_name"], "username": r["username"], "role": r["role"], "active": bool(r["active"])} for r in rows]}


@router.put("/{case_id}/members")
def set_members(case_id: str, body: Members, request: Request, user: dict = Depends(accounts.admin_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    with rt.db.transaction() as db:
        row = access.visible_case(db, user, case_id)
        ids = _check_members(db, body.user_ids)
        before = {r[0] for r in db.execute("SELECT user_id FROM case_members WHERE case_id = ?", (case_id,))}
        added, removed = set(ids) - before, before - set(ids)
        db.executemany("DELETE FROM case_members WHERE case_id = ? AND user_id = ?", [(case_id, u) for u in removed])
        db.executemany("INSERT INTO case_members (case_id, user_id) VALUES (?, ?)", [(case_id, u) for u in added])
        if added or removed:
            names = dict(db.execute("SELECT id, username FROM users").fetchall())
            detail = "; ".join(p for p in (
                "added " + ", ".join(sorted(names[u] for u in added)) if added else "",
                "removed " + ", ".join(sorted(names[u] for u in removed)) if removed else "",
            ) if p)
            audit.record(db, action="case.members", user=user, case_id=case_id, target=row["name"], detail=detail, ip=accounts.client_ip(request))
    return list_members(case_id, user, rt)


@router.get("/{case_id}/activity")
def case_activity(
    case_id: str,
    before: int | None = Query(None, ge=1),
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(accounts.current_user),
    rt: runtime.Runtime = Depends(runtime.get),
) -> dict:
    with rt.db.connect() as db:
        access.visible_case(db, user, case_id)
        rows = db.execute(
            "SELECT id, at, username, action, outcome, target, detail FROM audit WHERE case_id = ? AND id < ? ORDER BY id DESC LIMIT ?",
            (case_id, before or 2**62, limit),
        ).fetchall()
    return {"items": [dict(r) for r in rows]}
