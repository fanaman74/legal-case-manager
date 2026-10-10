"""Audit log: who did what, to which case, and when.

Entries live in app.db and are hash-chained like the launcher's audit file:
each entry's hash covers its content and the previous entry's hash, so an
edited or deleted entry breaks the chain. Entries are written in the same
transaction as the change they describe, so a change is never saved without
its audit entry."""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

GENESIS = "0" * 64

FIELDS = ("at", "user_id", "username", "action", "outcome", "case_id", "target", "detail", "ip")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _hash(prev: str, entry: dict) -> str:
    body = json.dumps([entry[f] for f in FIELDS], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256((prev + body).encode()).hexdigest()


def record(
    db: sqlite3.Connection,
    *,
    action: str,
    user: dict | None,
    ip: str = "",
    outcome: str = "ok",
    case_id: str | None = None,
    target: str = "",
    detail: str = "",
    username: str | None = None,
) -> None:
    """Append one entry. Call inside Database.transaction()."""
    entry = {
        "at": now(),
        "user_id": user["id"] if user else None,
        "username": username if username is not None else (user["username"] if user else "anonymous"),
        "action": action,
        "outcome": outcome,
        "case_id": case_id,
        "target": target,
        "detail": detail,
        "ip": ip,
    }
    row = db.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
    prev = row["hash"] if row else GENESIS
    entry_hash = _hash(prev, entry)
    db.execute(
        f"INSERT INTO audit ({', '.join(FIELDS)}, prev_hash, hash) VALUES ({', '.join('?' * (len(FIELDS) + 2))})",
        [entry[f] for f in FIELDS] + [prev, entry_hash],
    )


def verify(db: sqlite3.Connection) -> tuple[int, int | None]:
    """Walk the chain. Returns (entries checked, id of the first bad entry or None)."""
    prev = GENESIS
    count = 0
    for row in db.execute(f"SELECT id, {', '.join(FIELDS)}, prev_hash, hash FROM audit ORDER BY id"):
        count += 1
        if row["prev_hash"] != prev or _hash(prev, dict(row)) != row["hash"]:
            return count, row["id"]
        prev = row["hash"]
    return count, None
