"""Who can see and change which case. Every case route goes through these
checks on the server; the browser never decides.

- Admin: every case.
- Editor: assigned cases; can upload, tag and edit details (not rename).
- Read-only: assigned cases; can view and download only.

A case someone isn't assigned to is reported as not found, so its name and
existence don't leak."""

import sqlite3

from fastapi import HTTPException

NOT_FOUND = "That case doesn't exist, or you don't have access to it."


def case_ids_for(db: sqlite3.Connection, user: dict) -> list[str] | None:
    """The cases a user may see, or None for every case (Admin)."""
    if user["role"] == "admin":
        return None
    return [r[0] for r in db.execute("SELECT case_id FROM case_members WHERE user_id = ?", (user["id"],))]


def visible_case(db: sqlite3.Connection, user: dict, case_id: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if row is None:
        raise HTTPException(404, NOT_FOUND)
    if user["role"] != "admin" and not db.execute(
        "SELECT 1 FROM case_members WHERE case_id = ? AND user_id = ?", (case_id, user["id"])
    ).fetchone():
        raise HTTPException(404, NOT_FOUND)
    return row


def can_edit(user: dict) -> bool:
    """Upload, tag and edit details, in a case the user can already see."""
    return user["role"] in ("admin", "editor")


def require_edit(user: dict, case: sqlite3.Row) -> None:
    if not can_edit(user):
        raise HTTPException(403, "Your role is read-only in this case. Ask the Admin if you need to make changes.")
    if case["status"] == "archived":
        raise HTTPException(409, "This case is archived. The Admin can restore it to make changes.")


def permissions(user: dict, case: sqlite3.Row) -> dict:
    active = case["status"] == "active"
    return {
        "edit": can_edit(user) and active,
        "upload": can_edit(user) and active,
        "manage": user["role"] == "admin",
    }
