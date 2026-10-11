"""Case exports (Admin only): ask for one, see how it's going, download it.
The ZIP is built by the worker (export.py)."""

import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from .. import access, accounts, audit, export, runtime

router = APIRouter(prefix="/api/cases/{case_id}/exports")


def _case(db, user: dict, case_id: str):
    return access.visible_case(db, user, case_id)


@router.get("")
def list_exports(case_id: str, user: dict = Depends(accounts.admin_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    with rt.db.connect() as db:
        _case(db, user, case_id)
        rows = db.execute("SELECT * FROM exports WHERE case_id = ? AND state != 'expired' ORDER BY created_at DESC LIMIT 20", (case_id,)).fetchall()
        job = db.execute("SELECT done, total FROM jobs WHERE case_id = ? AND kind = 'export' AND state = 'running' ORDER BY id DESC LIMIT 1", (case_id,)).fetchone()
    items = [export.view(r) for r in rows]
    for item in items:
        if item["state"] == "building" and job:
            item["progress"] = {"done": job["done"], "total": job["total"]}
    return {"items": items, "keep_days": export.KEEP_DAYS}


@router.post("", status_code=202)
def new_export(case_id: str, request: Request, user: dict = Depends(accounts.admin_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    with rt.db.transaction() as db:
        _case(db, user, case_id)
        row = export.request(db, user, case_id, accounts.client_ip(request))
    return export.view(row)


@router.get("/{export_id}/file")
def download(case_id: str, export_id: str, request: Request, user: dict = Depends(accounts.admin_user), rt: runtime.Runtime = Depends(runtime.get)) -> FileResponse:
    if not re.fullmatch(r"[0-9a-f]{32}", export_id):
        raise HTTPException(404, "That export doesn't exist.")
    with rt.db.transaction() as db:
        case = _case(db, user, case_id)
        row = db.execute("SELECT * FROM exports WHERE id = ? AND case_id = ?", (export_id, case_id)).fetchone()
        if row is None:
            raise HTTPException(404, "That export doesn't exist.")
        if row["state"] == "expired":
            raise HTTPException(410, f"Exports are kept for {export.KEEP_DAYS} days, and this one has been deleted. Export the case again.")
        if row["state"] != "ready":
            raise HTTPException(409, "This export isn't ready yet.")
        path = export.path_of(rt, export_id)
        if not path.is_file():
            raise HTTPException(410, "This export's file is missing from the data folder. Export the case again.")
        audit.record(db, action="case.export_download", user=user, case_id=case_id, target=export_id, detail=f"zip sha256 {row['sha256']}", ip=accounts.client_ip(request))
    stamp = datetime.fromisoformat(row["finished_at"].replace("Z", "+00:00")).strftime("%Y-%m-%d")
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"{export.safe_part(case['name'])} export {stamp}.zip",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
    )


@router.delete("/{export_id}", status_code=204)
def delete(case_id: str, export_id: str, request: Request, user: dict = Depends(accounts.admin_user), rt: runtime.Runtime = Depends(runtime.get)) -> None:
    if not re.fullmatch(r"[0-9a-f]{32}", export_id):
        raise HTTPException(404, "That export doesn't exist.")
    with rt.db.transaction() as db:
        _case(db, user, case_id)
        row = db.execute("SELECT state FROM exports WHERE id = ? AND case_id = ?", (export_id, case_id)).fetchone()
        if row is None:
            raise HTTPException(404, "That export doesn't exist.")
        if row["state"] in ("queued", "building"):
            raise HTTPException(409, "This export is still being made. Delete it once it's ready.")
        db.execute("DELETE FROM exports WHERE id = ?", (export_id,))
        audit.record(db, action="case.export_delete", user=user, case_id=case_id, target=export_id, ip=accounts.client_ip(request))
    try:
        export.path_of(rt, export_id).unlink(missing_ok=True)
    except OSError:
        pass  # still open in a download; the hourly tidy removes it
