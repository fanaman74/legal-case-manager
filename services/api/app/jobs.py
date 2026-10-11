"""The background job queue: a table in app.db, so there is no separate
queue service to install or run.

The web app adds jobs (convert this file, index this case); the worker takes
them one at a time per thread. A job taken by a worker that then stopped
(crash, restart) goes back in the queue when the worker starts again."""

import json
import sqlite3

from . import audit
from .db import Database

KINDS = ("convert", "index", "pst", "pst_add", "export", "model")
# How the status banner and Control Center name each kind of job.
LABELS = {"convert": "Conversion", "index": "Indexing", "pst": "Reading mailbox", "pst_add": "Adding emails", "export": "Export", "model": "Downloading AI model"}


def enqueue(db: sqlite3.Connection, kind: str, case_id: str | None, file_id: str | None = None, options: dict | None = None, user: dict | None = None) -> int | None:
    """Add a job, unless the same job is already waiting. Call inside a
    transaction. Returns the job id, or None if one was already queued."""
    if file_id is not None:
        exists = db.execute(
            "SELECT id FROM jobs WHERE kind = ? AND file_id = ? AND state = 'queued'",
            (kind, file_id),
        ).fetchone()
        if exists:
            if options:
                db.execute("UPDATE jobs SET options = ? WHERE id = ?", (json.dumps(options), exists["id"]))
            return None
    cur = db.execute(
        "INSERT INTO jobs (kind, case_id, file_id, options, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?)",
        (kind, case_id, file_id, json.dumps(options or {}), audit.now(), user["id"] if user else None),
    )
    return cur.lastrowid


def claim(database: Database, kinds: tuple[str, ...]) -> dict | None:
    """Take the oldest waiting job of these kinds."""
    marks = ",".join("?" * len(kinds))
    with database.transaction() as db:
        row = db.execute(
            f"""UPDATE jobs SET state = 'running', started_at = ?, attempts = attempts + 1
                WHERE id = (SELECT id FROM jobs WHERE state = 'queued' AND kind IN ({marks}) ORDER BY id LIMIT 1)
                RETURNING *""",
            (audit.now(), *kinds),
        ).fetchone()
    if row is None:
        return None
    job = dict(row)
    job["options"] = json.loads(job["options"] or "{}")
    return job


def finish(db: sqlite3.Connection, job_id: int) -> None:
    db.execute("UPDATE jobs SET state = 'done', finished_at = ?, error = '' WHERE id = ?", (audit.now(), job_id))


def fail(db: sqlite3.Connection, job_id: int, error: str) -> None:
    db.execute("UPDATE jobs SET state = 'failed', finished_at = ?, error = ? WHERE id = ?", (audit.now(), error, job_id))


def retry_later(db: sqlite3.Connection, job_id: int) -> None:
    db.execute("UPDATE jobs SET state = 'queued', started_at = NULL WHERE id = ?", (job_id,))


def progress(database: Database, job_id: int, done: int, total: int) -> None:
    with database.connect() as db:
        db.execute("UPDATE jobs SET done = ?, total = ? WHERE id = ?", (done, total, job_id))


def recover(database: Database) -> int:
    """Jobs left running by a worker that stopped go back in the queue."""
    with database.transaction() as db:
        return db.execute("UPDATE jobs SET state = 'queued', started_at = NULL WHERE state = 'running'").rowcount


def tidy(database: Database, keep_days: int = 7) -> None:
    """Finished jobs are kept a week for the job list, then removed."""
    with database.transaction() as db:
        db.execute("DELETE FROM jobs WHERE state IN ('done', 'cancelled') AND finished_at < strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?)", (f"-{keep_days} days",))


def counts(db: sqlite3.Connection, case_ids: list[str] | None = None) -> dict:
    """Waiting, running and failed jobs per kind, for cases the caller may
    see (None means every case)."""
    where, args = "", []
    if case_ids is not None:
        if not case_ids:
            return {}
        where = f" AND case_id IN ({','.join('?' * len(case_ids))})"
        args = list(case_ids)
    out: dict[str, dict] = {}
    for r in db.execute(
        f"SELECT kind, state, COUNT(*) AS n, SUM(done) AS done, SUM(total) AS total FROM jobs WHERE state IN ('queued', 'running', 'failed'){where} GROUP BY kind, state",
        args,
    ):
        k = out.setdefault(r["kind"], {"queued": 0, "running": 0, "failed": 0, "done": 0, "total": 0})
        k[r["state"]] = r["n"]
        if r["state"] == "running":
            k["done"], k["total"] = r["done"] or 0, r["total"] or 0
    return out
