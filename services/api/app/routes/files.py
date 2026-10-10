"""Files in a case: upload, list, folders, details, tags and download.

An upload is one file per request, with the file's bytes as the request
body and its path inside the uploaded folder in `?path=`. The browser sends
files one by one, so each file succeeds or fails on its own and a failure
never stops the rest of a batch. The body is streamed to disk and hashed as
it arrives; nothing is held in memory."""

import hashlib
import json
import sqlite3
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import access, accounts, audit, runtime

router = APIRouter(prefix="/api/cases/{case_id}")

SOURCE_TYPES = {
    ".docx": "word",
    ".pdf": "pdf",
    ".msg": "email",
    ".eml": "email",
    ".txt": "text",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".tif": "image",
    ".tiff": "image",
}
SUPPORTED = ", ".join(SOURCE_TYPES)
SORTS = {"name": "name COLLATE NOCASE", "folder": "folder COLLATE NOCASE", "type": "source_type", "size": "size", "added": "added_at"}


class FileEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tags: list[str] = Field(max_length=50)


def split_path(raw: str) -> tuple[str, str]:
    """Split an uploaded relative path into (folder, name). Folders use
    forward slashes. Rejects anything that tries to climb out of the upload."""
    parts = [p for p in raw.replace("\\", "/").split("/")]
    while parts and parts[0] == "":
        parts.pop(0)  # a leading slash
    if not parts or len(raw) > 1024:
        raise ValueError
    for p in parts:
        if p in ("", ".", "..") or len(p) > 255 or any(ord(c) < 32 or c == "\x7f" for c in p):
            raise ValueError
    return "/".join(parts[:-1]), parts[-1]


def extension(name: str) -> str:
    dot = name.rfind(".")
    return name[dot:].lower() if dot > 0 else ""


def file_view(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "folder": row["folder"],
        "type": row["source_type"],
        "ext": row["ext"],
        "size": row["size"],
        "sha256": row["sha256"],
        "status": row["status"],
        "tags": json.loads(row["tags"]),
        "added_at": row["added_at"],
        "added_by": row["added_by_name"] if "added_by_name" in row.keys() else None,
    }


_FILE_SELECT = "SELECT f.*, u.display_name AS added_by_name FROM files f LEFT JOIN users u ON u.id = f.added_by"


def _editable_case(rt: runtime.Runtime, user: dict, case_id: str) -> sqlite3.Row:
    with rt.db.connect() as db:
        case = access.visible_case(db, user, case_id)
    access.require_edit(user, case)
    return case


@router.put("/files", status_code=201)
async def upload(
    case_id: str,
    request: Request,
    path: str = Query(..., description="Path of the file inside the uploaded folder, e.g. Letters/2024/offer.pdf"),
    user: dict = Depends(accounts.current_user),
    rt: runtime.Runtime = Depends(runtime.get),
):
    await run_in_threadpool(_editable_case, rt, user, case_id)
    ip = accounts.client_ip(request)
    try:
        folder, name = split_path(path)
    except ValueError:
        raise HTTPException(400, "That file name or folder can't be used. Rename it and try again.")
    ext = extension(name)
    if ext not in SOURCE_TYPES:
        await run_in_threadpool(_audit, rt, user, ip, case_id, "file.rejected", "/".join(filter(None, (folder, name))), "unsupported type", "refused")
        return JSONResponse(
            {"result": "unsupported", "detail": f"{ext or 'Files without an extension'} isn't supported. Supported types: {SUPPORTED}."},
            status_code=415,
        )
    limit = rt.cfg.max_upload_bytes
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, _too_large(limit))

    tmp = rt.storage.incoming(case_id)
    digest, size = hashlib.sha256(), 0
    try:
        with open(tmp, "wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, _too_large(limit))
                digest.update(chunk)
                out.write(chunk)
        return await run_in_threadpool(_store, rt, user, ip, case_id, folder, name, ext, size, digest.hexdigest(), tmp)
    finally:
        tmp.unlink(missing_ok=True)


def _too_large(limit: int) -> str:
    size = f"{limit / 2**30:g} GB" if limit >= 2**30 else f"{limit / 2**20:g} MB"
    return f"This file is larger than the {size} limit for one file."


def _audit(rt, user, ip, case_id, action, target, detail, outcome="ok") -> None:
    with rt.db.transaction() as db:
        audit.record(db, action=action, user=user, case_id=case_id, target=target, detail=detail, outcome=outcome, ip=ip)


def _store(rt: runtime.Runtime, user: dict, ip: str, case_id: str, folder: str, name: str, ext: str, size: int, sha256: str, tmp) -> JSONResponse:
    shown = "/".join(filter(None, (folder, name)))
    file_id = uuid.uuid4().hex
    with rt.db.transaction() as db:
        case = access.visible_case(db, user, case_id)  # the case may have been archived or deleted meanwhile
        access.require_edit(user, case)
        existing = db.execute(_FILE_SELECT + " WHERE f.case_id = ? AND f.sha256 = ?", (case_id, sha256)).fetchone()
        if existing is None:
            db.execute(
                "INSERT INTO files (id, case_id, name, folder, ext, source_type, size, sha256, added_at, added_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (file_id, case_id, name, folder, ext, SOURCE_TYPES[ext], size, sha256, audit.now(), user["id"]),
            )
            audit.record(db, action="file.upload", user=user, case_id=case_id, target=shown, detail=f"sha256 {sha256}", ip=ip)
            # Moved into place last, before the commit: if the move fails, the record is rolled back.
            rt.storage.keep(tmp, case_id, file_id, ext)
            row = db.execute(_FILE_SELECT + " WHERE f.id = ?", (file_id,)).fetchone()
            return JSONResponse({"result": "stored", "file": file_view(row)}, status_code=201)
        audit.record(db, action="file.duplicate", user=user, case_id=case_id, target=shown, detail=f"same content as {'/'.join(filter(None, (existing['folder'], existing['name'])))}", outcome="refused", ip=ip)
    return JSONResponse(
        {"result": "duplicate", "detail": "This file is already in the case, so it wasn't added again.", "existing": file_view(existing)},
        status_code=409,
    )


@router.get("/files")
def list_files(
    case_id: str,
    q: str = Query("", max_length=200, description="Text in the file name"),
    folder: str | None = Query(None, max_length=1024, description="Only this folder and its subfolders"),
    type: str | None = Query(None, pattern="^(word|pdf|email|text|image)$"),
    status: str | None = Query(None, max_length=20),
    tag: str | None = Query(None, max_length=50),
    sort: str = Query("added", pattern="^(name|folder|type|size|added)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: dict = Depends(accounts.current_user),
    rt: runtime.Runtime = Depends(runtime.get),
) -> dict:
    where, args = ["f.case_id = ?"], [case_id]
    if q:
        where.append("f.name LIKE ? ESCAPE '\\'")
        args.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if folder is not None and folder.strip("/"):
        f = folder.strip("/")
        where.append("(f.folder = ? OR substr(f.folder, 1, ?) = ?)")
        args += [f, len(f) + 1, f + "/"]
    if type:
        where.append("f.source_type = ?")
        args.append(type)
    if status:
        where.append("f.status = ?")
        args.append(status)
    if tag:
        where.append("EXISTS (SELECT 1 FROM json_each(f.tags) WHERE value = ?)")
        args.append(tag)
    clause = " AND ".join(where)
    with rt.db.connect() as db:
        access.visible_case(db, user, case_id)
        total = db.execute(f"SELECT COUNT(*) FROM files f WHERE {clause}", args).fetchone()[0]
        rows = db.execute(f"{_FILE_SELECT} WHERE {clause} ORDER BY f.{SORTS[sort]} {order.upper()}, f.id LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
    return {"items": [file_view(r) for r in rows], "total": total}


@router.get("/folders")
def folders(case_id: str, user: dict = Depends(accounts.current_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    """Every folder with the number of files directly in it. The browser
    builds the tree from the paths."""
    with rt.db.connect() as db:
        access.visible_case(db, user, case_id)
        rows = db.execute("SELECT folder, COUNT(*) AS files FROM files WHERE case_id = ? GROUP BY folder ORDER BY folder", (case_id,)).fetchall()
    return {"items": [{"path": r["folder"], "files": r["files"]} for r in rows]}


def _visible_file(db, user: dict, case_id: str, file_id: str) -> tuple[sqlite3.Row, sqlite3.Row]:
    case = access.visible_case(db, user, case_id)
    row = db.execute(_FILE_SELECT + " WHERE f.id = ? AND f.case_id = ?", (file_id, case_id)).fetchone()
    if row is None:
        raise HTTPException(404, "That file isn't in this case.")
    return case, row


@router.get("/files/{file_id}")
def get_file(case_id: str, file_id: str, user: dict = Depends(accounts.current_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    with rt.db.connect() as db:
        _, row = _visible_file(db, user, case_id, file_id)
    return file_view(row)


@router.patch("/files/{file_id}")
def edit_file(case_id: str, file_id: str, body: FileEdit, request: Request, user: dict = Depends(accounts.current_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    tags = []
    for t in body.tags:
        t = " ".join(t.split())
        if not t or len(t) > 50:
            raise HTTPException(400, "Tags must be 1 to 50 characters.")
        if t not in tags:
            tags.append(t)
    with rt.db.transaction() as db:
        case, row = _visible_file(db, user, case_id, file_id)
        access.require_edit(user, case)
        if json.loads(row["tags"]) != tags:
            db.execute("UPDATE files SET tags = ? WHERE id = ?", (json.dumps(tags), file_id))
            audit.record(db, action="file.tags", user=user, case_id=case_id, target="/".join(filter(None, (row["folder"], row["name"]))), detail=", ".join(tags) or "(none)", ip=accounts.client_ip(request))
        row = db.execute(_FILE_SELECT + " WHERE f.id = ?", (file_id,)).fetchone()
    return file_view(row)


@router.get("/files/{file_id}/original")
def download(case_id: str, file_id: str, request: Request, user: dict = Depends(accounts.current_user), rt: runtime.Runtime = Depends(runtime.get)) -> FileResponse:
    """The original, byte for byte, always as a download: uploaded files are
    untrusted, so the browser never renders them on the app's own address."""
    with rt.db.transaction() as db:
        _, row = _visible_file(db, user, case_id, file_id)
        path = rt.storage.original(case_id, file_id, row["ext"])
        if not path.is_file():
            raise HTTPException(410, "The stored copy of this file is missing from the data folder. Restore it from a backup.")
        audit.record(db, action="file.download", user=user, case_id=case_id, target="/".join(filter(None, (row["folder"], row["name"]))), ip=accounts.client_ip(request))
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=row["name"],
        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox", "Cache-Control": "no-store"},
    )
