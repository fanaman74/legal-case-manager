"""Background work as people see it: the status line in the top bar (for
everyone, limited to their cases) and the job list with failures and Retry
(Admin)."""

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .. import access, accounts, audit, jobs, runtime

router = APIRouter(prefix="/api")


@router.get("/status")
def status(user: dict = Depends(accounts.current_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    """Work in progress across the cases this person can see, for the
    banner ("Converting 12 of 80 files")."""
    with rt.db.connect() as db:
        allowed = access.case_ids_for(db, user)
        counts = jobs.counts(db, allowed)
        where, args = "", []
        if allowed is not None:
            if not allowed:
                return {"jobs": {}, "files": {}}
            where = f" WHERE case_id IN ({','.join('?' * len(allowed))})"
            args = allowed
        files = {r["status"]: r["n"] for r in db.execute(f"SELECT status, COUNT(*) AS n FROM files{where} GROUP BY status", args)}
    return {"jobs": counts, "files": files}


@router.get("/jobs")
def list_jobs(
    state: str | None = Query(None, pattern="^(queued|running|done|failed)$"),
    limit: int = Query(200, ge=1, le=1000),
    user: dict = Depends(accounts.admin_user),
    rt: runtime.Runtime = Depends(runtime.get),
) -> dict:
    where, args = "", []
    if state:
        where, args = "WHERE j.state = ?", [state]
    with rt.db.connect() as db:
        rows = db.execute(
            f"""SELECT j.*, c.name AS case_name, f.name AS file_name, f.folder AS file_folder, m.name AS mailbox_name
                FROM jobs j LEFT JOIN cases c ON c.id = j.case_id LEFT JOIN files f ON f.id = j.file_id
                LEFT JOIN mailboxes m ON m.id = json_extract(j.options, '$.mailbox')
                {where} ORDER BY CASE j.state WHEN 'running' THEN 0 WHEN 'failed' THEN 1 WHEN 'queued' THEN 2 ELSE 3 END, j.id DESC LIMIT ?""",
            [*args, limit],
        ).fetchall()
        counts = jobs.counts(db)
    return {
        "items": [
            {
                "id": r["id"],
                "kind": r["kind"],
                "label": jobs.LABELS.get(r["kind"], r["kind"]),
                "state": r["state"],
                "case_id": r["case_id"],
                "case_name": r["case_name"],
                "file_id": r["file_id"],
                "file": "/".join(p for p in (r["file_folder"], r["file_name"]) if p) if r["file_name"] else r["mailbox_name"] or json.loads(r["options"] or "{}").get("model"),
                "error": r["error"],
                "attempts": r["attempts"],
                "done": r["done"],
                "total": r["total"],
                "created_at": r["created_at"],
                "started_at": r["started_at"],
                "finished_at": r["finished_at"],
            }
            for r in rows
        ],
        "counts": counts,
    }


@router.post("/jobs/{job_id}/retry")
def retry(job_id: int, request: Request, user: dict = Depends(accounts.admin_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    with rt.db.transaction() as db:
        row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "That job no longer exists.")
        if row["state"] != "failed":
            raise HTTPException(409, "Only failed jobs can be retried.")
        db.execute("UPDATE jobs SET state = 'queued', error = '', attempts = 0, started_at = NULL, finished_at = NULL WHERE id = ?", (job_id,))
        if row["kind"] == "convert" and row["file_id"]:
            db.execute("UPDATE files SET status = 'queued', error = '' WHERE id = ?", (row["file_id"],))
        if row["kind"] == "pst":
            db.execute("UPDATE mailboxes SET status = 'queued', error = '' WHERE id = ?", (json.loads(row["options"] or "{}").get("mailbox"),))
        audit.record(db, action="job.retry", user=user, case_id=row["case_id"], target=jobs.LABELS.get(row["kind"], row["kind"]), detail=f"job {job_id}", ip=accounts.client_ip(request))
    return {"ok": True}
