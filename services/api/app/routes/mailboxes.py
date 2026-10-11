"""Outlook mailboxes in a case: upload a PST, search it, preview an email,
and add chosen emails to the case.

A mailbox belongs to one case. Only people on that case can search it, and
emails can only be added to that case."""

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import access, accounts, audit, jobs, runtime
from ..index.query import QueryError, parse
from ..index.store import END, HIT
from .files import split_path

router = APIRouter(prefix="/api/cases/{case_id}/mailboxes")

EXTS = (".pst", ".ost")
_DATE = "^\\d{4}-\\d{2}-\\d{2}$"


class AddEmails(BaseModel):
    model_config = ConfigDict(extra="forbid")
    messages: list[int] = Field(min_length=1, max_length=5000)
    attachments: bool = True


def view(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "size": row["size"],
        "sha256": row["sha256"],
        "status": row["status"],
        "error": row["error"],
        "emails": row["emails"],
        "done": row["done"],
        "total": row["total"],
        "added_at": row["added_at"],
        "added_by": row["added_by_name"] if "added_by_name" in row.keys() else None,
        "added_to_case": row["added_count"] if "added_count" in row.keys() else 0,
    }


_SELECT = """SELECT m.*, u.display_name AS added_by_name, (SELECT COUNT(*) FROM mailbox_added a WHERE a.mailbox_id = m.id) AS added_count
             FROM mailboxes m LEFT JOIN users u ON u.id = m.added_by"""


def _mailbox(db: sqlite3.Connection, user: dict, case_id: str, mailbox_id: str, ready: bool = False) -> tuple[sqlite3.Row, sqlite3.Row]:
    case = access.visible_case(db, user, case_id)
    mb = db.execute(_SELECT + " WHERE m.id = ? AND m.case_id = ?", (mailbox_id, case_id)).fetchone()
    if mb is None:
        raise HTTPException(404, "That mailbox isn't in this case. It may have been removed.")
    if ready and mb["status"] != "ready":
        raise HTTPException(409, "This mailbox is still being read. Its emails can be searched when it's ready.")
    return case, mb


def _open(rt: runtime.Runtime, case_id: str, mailbox_id: str) -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{rt.storage.mailbox_db(case_id, mailbox_id)}?mode=ro", uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    return db


@router.get("")
def list_mailboxes(case_id: str, user: dict = Depends(accounts.current_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    with rt.db.connect() as db:
        access.visible_case(db, user, case_id)
        rows = db.execute(_SELECT + " WHERE m.case_id = ? ORDER BY m.added_at DESC", (case_id,)).fetchall()
    return {"items": [view(r) for r in rows]}


@router.put("", status_code=201)
async def upload(case_id: str, request: Request, name: str = Query(..., max_length=255), user: dict = Depends(accounts.current_user), rt: runtime.Runtime = Depends(runtime.get)):
    def check() -> None:
        with rt.db.connect() as db:
            case = access.visible_case(db, user, case_id)
        access.require_edit(user, case)

    await run_in_threadpool(check)
    try:
        _, filename = split_path(name)
    except ValueError:
        raise HTTPException(400, "That file name can't be used. Rename it and try again.")
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in EXTS:
        raise HTTPException(415, "Only Outlook data files (.pst or .ost) can be added here. Upload single emails (.msg, .eml) on the Files tab.")
    limit = rt.cfg.max_mailbox_bytes
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, f"This mailbox is larger than the {limit / 2**30:g} GB limit.")
    tmp = rt.storage.incoming_mailbox(case_id)
    digest, size = hashlib.sha256(), 0
    try:
        with open(tmp, "wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"This mailbox is larger than the {limit / 2**30:g} GB limit.")
                digest.update(chunk)
                out.write(chunk)
        return await run_in_threadpool(_store, rt, user, accounts.client_ip(request), case_id, filename, ext, size, digest.hexdigest(), tmp)
    finally:
        tmp.unlink(missing_ok=True)


def _store(rt, user, ip, case_id, name, ext, size, sha, tmp):
    import os
    import stat

    with rt.db.transaction() as db:
        case = access.visible_case(db, user, case_id)
        access.require_edit(user, case)
        existing = db.execute(_SELECT + " WHERE m.case_id = ? AND m.sha256 = ?", (case_id, sha)).fetchone()
        if existing:
            return JSONResponse({"result": "duplicate", "detail": "This mailbox is already in the case.", "existing": view(existing)}, status_code=409)
        mailbox_id = uuid.uuid4().hex
        db.execute("INSERT INTO mailboxes (id, case_id, name, ext, size, sha256, added_at, added_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (mailbox_id, case_id, name, ext, size, sha, audit.now(), user["id"]))
        jobs.enqueue(db, "pst", case_id, None, {"mailbox": mailbox_id}, user=user)
        audit.record(db, action="mailbox.upload", user=user, case_id=case_id, target=name, detail=f"sha256 {sha}; {size} bytes", ip=ip)
        dest = rt.storage.mailbox(case_id, mailbox_id, ext)
        os.replace(tmp, dest)
        dest.chmod(stat.S_IREAD)
        row = db.execute(_SELECT + " WHERE m.id = ?", (mailbox_id,)).fetchone()
    return JSONResponse({"result": "stored", "mailbox": view(row)}, status_code=201)


@router.get("/{mailbox_id}")
def get_mailbox(case_id: str, mailbox_id: str, user: dict = Depends(accounts.current_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    with rt.db.connect() as db:
        _, mb = _mailbox(db, user, case_id, mailbox_id)
    out = view(mb)
    if mb["status"] == "ready":
        with closing(_open(rt, case_id, mailbox_id)) as mdb:
            out["folders"] = [dict(r) for r in mdb.execute("SELECT path, messages FROM folders ORDER BY path")]
    return out


@router.get("/{mailbox_id}/search")
def search(
    case_id: str,
    mailbox_id: str,
    request: Request,
    q: str = Query("", max_length=500),
    sender: str = Query("", max_length=200),
    to: str = Query("", max_length=200),
    folder: str = Query("", max_length=1024),
    date_from: str | None = Query(None, pattern=_DATE),
    date_to: str | None = Query(None, pattern=_DATE),
    attachments: bool = False,
    kind: str = Query("", pattern="^(|email|meeting|appointment|contact|task|note)$"),
    sort: str = Query("relevance", pattern="^(relevance|newest|oldest)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(accounts.current_user),
    rt: runtime.Runtime = Depends(runtime.get),
) -> dict:
    with rt.db.connect() as db:
        _, mb = _mailbox(db, user, case_id, mailbox_id, ready=True)
        added = {r["message"]: r["file_id"] for r in db.execute("SELECT message, file_id FROM mailbox_added WHERE mailbox_id = ?", (mailbox_id,))}
    where, args = [], []

    def like(col: str, value: str) -> None:
        where.append(f"{col} LIKE ? ESCAPE '\\'")
        args.append("%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")

    if sender:
        like("m.sender", sender)
    if to:
        where.append("(m.recipients LIKE ? ESCAPE '\\' OR m.cc LIKE ? ESCAPE '\\')")
        v = "%" + to.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        args += [v, v]
    if folder.strip("/"):
        f = folder.strip("/")
        where.append("(m.folder = ? OR substr(m.folder, 1, ?) = ?)")
        args += [f, len(f) + 1, f + "/"]
    if date_from:
        where.append("substr(m.date, 1, 10) >= ?")
        args.append(date_from)
    if date_to:
        where.append("substr(m.date, 1, 10) <= ?")
        args.append(date_to)
    if attachments:
        where.append("m.attachments != '[]'")
    if kind:
        where.append("m.kind = ?")
        args.append(kind)
    terms: list[str] = []
    if q.strip():
        try:
            parsed = parse(q)
        except QueryError as exc:
            raise HTTPException(400, str(exc)) from exc
        terms = parsed.terms
        source = "messages_fts JOIN messages m ON m.id = messages_fts.rowid"
        where.insert(0, "messages_fts MATCH ?")
        args.insert(0, parsed.fts)
        snippet = f"snippet(messages_fts, 3, '{HIT}', '{END}', '…', 28)"
        order = {"relevance": "bm25(messages_fts, 4.0, 2.0, 1.0, 1.0, 1.0)", "newest": "m.date DESC", "oldest": "m.date ASC"}[sort]
    else:
        source = "messages m"
        snippet = "substr(m.body, 1, 220)"
        order = "m.date ASC" if sort == "oldest" else "m.date DESC"
    clause = " WHERE " + " AND ".join(where) if where else ""
    try:
        with closing(_open(rt, case_id, mailbox_id)) as mdb:
            total = mdb.execute(f"SELECT COUNT(*) FROM {source}{clause}", args).fetchone()[0]
            rows = mdb.execute(
                f"SELECT m.id, m.folder, m.kind, m.subject, m.sender, m.recipients, m.date, m.attachments, {snippet} AS snippet FROM {source}{clause} ORDER BY {order}, m.id LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ).fetchall()
    except sqlite3.OperationalError as exc:
        if "fts5" in str(exc).lower() or "syntax" in str(exc).lower():
            raise HTTPException(400, "That search couldn't be read. Check the quotes and try again.") from exc
        raise
    if q.strip() and offset == 0:
        with rt.db.transaction() as db:
            audit.record(db, action="mailbox.search", user=user, case_id=case_id, target=mb["name"], detail=f"{q[:200]}; {total} results", ip=accounts.client_ip(request))
    return {
        "items": [
            {
                "id": r["id"], "folder": r["folder"], "kind": r["kind"], "subject": r["subject"], "sender": r["sender"], "to": r["recipients"],
                "date": r["date"], "attachments": len(json.loads(r["attachments"])), "snippet": " ".join((r["snippet"] or "").split()),
                "in_case": added.get(r["id"]),
            }
            for r in rows
        ],
        "total": total,
        "terms": terms,
    }


@router.get("/{mailbox_id}/messages/{message_id}")
def message(case_id: str, mailbox_id: str, message_id: int, request: Request, user: dict = Depends(accounts.current_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    with rt.db.connect() as db:
        _, mb = _mailbox(db, user, case_id, mailbox_id, ready=True)
        in_case = db.execute("SELECT file_id FROM mailbox_added WHERE mailbox_id = ? AND message = ?", (mailbox_id, message_id)).fetchone()
    with closing(_open(rt, case_id, mailbox_id)) as mdb:
        r = mdb.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    if r is None:
        raise HTTPException(404, "That email isn't in this mailbox.")
    with rt.db.transaction() as db:
        audit.record(db, action="mailbox.view", user=user, case_id=case_id, target=mb["name"], detail=f"{r['subject'][:200]} ({r['date'] or 'no date'})", ip=accounts.client_ip(request))
    return {
        "id": r["id"], "folder": r["folder"], "kind": r["kind"], "subject": r["subject"], "sender": r["sender"],
        "to": [x for x in r["recipients"].split("; ") if x], "cc": [x for x in r["cc"].split("; ") if x],
        "date": r["date"], "message_id": r["message_id"], "body": r["body"], "attachments": json.loads(r["attachments"]),
        "in_case": in_case["file_id"] if in_case else None,
    }


@router.post("/{mailbox_id}/add")
def add(case_id: str, mailbox_id: str, body: AddEmails, request: Request, user: dict = Depends(accounts.current_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    with rt.db.transaction() as db:
        case, mb = _mailbox(db, user, case_id, mailbox_id, ready=True)
        access.require_edit(user, case)
        already = {r[0] for r in db.execute("SELECT message FROM mailbox_added WHERE mailbox_id = ?", (mailbox_id,))}
        new = sorted(set(body.messages) - already)
        if new:
            jobs.enqueue(db, "pst_add", case_id, None, {"mailbox": mailbox_id, "messages": new, "attachments": body.attachments}, user=user)
            audit.record(db, action="mailbox.add", user=user, case_id=case_id, target=mb["name"], detail=f"{len(new)} emails; attachments {'included' if body.attachments else 'left in mailbox'}", ip=accounts.client_ip(request))
    return {"queued": len(new), "already_in_case": len(body.messages) - len(new)}


@router.post("/{mailbox_id}/delete", status_code=204)
def delete(case_id: str, mailbox_id: str, request: Request, user: dict = Depends(accounts.current_user), rt: runtime.Runtime = Depends(runtime.get)) -> None:
    """Remove the mailbox and its search database. Emails already added to
    the case stay in the case."""
    with rt.db.transaction() as db:
        case, mb = _mailbox(db, user, case_id, mailbox_id)
        if not access.permissions(user, case)["manage"]:
            raise HTTPException(403, "Only the Admin can remove a mailbox.")
        if mb["status"] in ("queued", "indexing"):
            raise HTTPException(409, "This mailbox is still being read. Remove it when that's finished.")
        db.execute("DELETE FROM mailboxes WHERE id = ?", (mailbox_id,))
        db.execute("UPDATE jobs SET state = 'cancelled' WHERE state = 'queued' AND json_extract(options, '$.mailbox') = ?", (mailbox_id,))
        audit.record(db, action="mailbox.delete", user=user, case_id=case_id, target=mb["name"], detail=f"sha256 {mb['sha256']}", ip=accounts.client_ip(request))
        for path in (rt.storage.mailbox(case_id, mailbox_id, mb["ext"]), rt.storage.mailbox_db(case_id, mailbox_id)):
            if path.exists():
                path.chmod(0o600)
                path.unlink()
