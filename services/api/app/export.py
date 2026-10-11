"""Case export: one ZIP with everything about a case, for handing over,
archiving outside this app, or opening on a computer without it.

    README.txt                      what's inside and how to check it
    originals/<folder>/<name>       uploaded files, byte for byte
    markdown/<folder>/<name>.md     the converted text, with its front matter
    metadata/case.json              the case's details, notes and people
    metadata/files.json             every file: where it is in the ZIP, its
                                    SHA-256, tags, dates, conversion details
    metadata/reviews.json           AI reviews saved in the case (each
                                    person's own questions are not included)
    audit/case-audit-trail.csv      everything recorded about the case, with
    audit/case-audit-trail.json     each entry's hash in the app's audit chain
    manifest-sha256.txt             SHA-256 of every file above

Only the Admin can export. The export is built by the worker in the
background, kept for 7 days for download, then deleted. Asking for it,
finishing it and downloading it are all in the audit log."""

import csv
import hashlib
import io
import json
import logging
import re
import shutil
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import audit, jobs
from .runtime import Runtime

log = logging.getLogger("worker.export")

KEEP_DAYS = 7
MAX_PATH = 150  # inside the ZIP, leaving room for where it's unzipped
# Already compressed: storing them as they are is as small and much faster.
STORED = {".pdf", ".docx", ".jpg", ".jpeg", ".png", ".msg", ".zip"}
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def folder(rt: Runtime) -> Path:
    return rt.cfg.data_dir / "exports"


def path_of(rt: Runtime, export_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", export_id):
        raise ValueError("bad export id")
    return folder(rt) / f"{export_id}.zip"


def safe_part(name: str) -> str:
    """A file or folder name that unzips cleanly on Windows."""
    name = _BAD.sub("_", name).strip().rstrip(".")
    if not name:
        name = "_"
    if name.split(".")[0].lower() in _RESERVED:
        name = "_" + name
    if len(name) > 120:
        stem, dot, ext = name.rpartition(".")
        name = (stem[:110] + dot + ext[:9]) if dot and len(ext) < 10 else name[:120]
    return name


class _Names:
    """Paths inside the ZIP, made unique (Windows ignores case)."""

    def __init__(self) -> None:
        self.used: set[str] = set()

    def take(self, top: str, folder_path: str, name: str) -> str:
        parts = [safe_part(p) for p in folder_path.split("/") if p]
        stem, dot, ext = safe_part(name).rpartition(".")
        if not dot:
            stem, ext = ext, ""
        # Keep paths short enough to unzip into a folder on Windows (260
        # characters in all); files.json keeps the full folder and name.
        if len("/".join([top, *parts, stem])) > MAX_PATH:
            parts = [p[:30].rstrip(" .") or "_" for p in parts]
            if len(parts) > 2 and len("/".join([top, *parts])) > MAX_PATH - 40:
                parts = [parts[0], "_", parts[-1]]  # first and last folder
        room = MAX_PATH - len("/".join([top, *parts])) - 1
        if len(stem) > room:
            stem = stem[:max(room, 20)].rstrip(" .") or "_"
        n = 1
        while True:
            leaf = f"{stem}{f' ({n})' if n > 1 else ''}{'.' + ext if dot else ''}"
            path = "/".join([top, *parts, leaf])
            if path.lower() not in self.used:
                self.used.add(path.lower())
                return path
            n += 1


def request(db, user: dict, case_id: str, ip: str = "") -> dict:
    """Queue an export of a case. Call inside a transaction."""
    running = db.execute("SELECT id FROM exports WHERE case_id = ? AND state IN ('queued', 'building')", (case_id,)).fetchone()
    if running:
        return dict(db.execute("SELECT * FROM exports WHERE id = ?", (running["id"],)).fetchone())
    export_id = uuid.uuid4().hex
    db.execute(
        "INSERT INTO exports (id, case_id, created_at, created_by, username) VALUES (?, ?, ?, ?, ?)",
        (export_id, case_id, audit.now(), user["id"], user["username"]),
    )
    jobs.enqueue(db, "export", case_id, options={"export": export_id}, user=user)
    audit.record(db, action="case.export", user=user, case_id=case_id, target=export_id, detail="export requested", ip=ip)
    return dict(db.execute("SELECT * FROM exports WHERE id = ?", (export_id,)).fetchone())


def failed(db, job: dict, message: str) -> None:
    export_id = (job.get("options") or {}).get("export")
    db.execute("UPDATE exports SET state = 'failed', error = ?, finished_at = ? WHERE id = ?", (message, audit.now(), export_id))
    jobs.fail(db, job["id"], message)
    audit.record(db, action="case.export", user=None, username="worker", outcome="failed", case_id=job["case_id"], target=export_id or "", detail=message[:300])


def _audit_rows(db, case_id: str) -> list[dict]:
    cols = ("id", *audit.FIELDS, "prev_hash", "hash")
    return [dict(r) for r in db.execute(f"SELECT {', '.join(cols)} FROM audit WHERE case_id = ? ORDER BY id", (case_id,))]


def _csv(rows: list[dict]) -> bytes:
    out = io.StringIO()
    if rows:
        w = csv.DictWriter(out, fieldnames=list(rows[0]))
        w.writeheader()
        for r in rows:
            # A leading = + - @ would run as a formula in Excel.
            w.writerow({k: ("'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v) for k, v in r.items()})
    return ("﻿" + out.getvalue()).encode("utf-8")  # BOM, so Excel reads accents


def _readme(case: dict, when: str, by: str, counts: dict, chain_ok: bool) -> str:
    return f"""Case export: {case['name']}
{'=' * (13 + len(case['name']))}

Exported {datetime.fromisoformat(when.replace("Z", "+00:00")).strftime("%d %B %Y at %H:%M UTC")} by {by}, from Case File Manager on this firm's own computer.

What's inside
-------------
originals/   The {counts['originals']} uploaded files, exactly as they were uploaded
             (byte for byte; check them against metadata/files.json).
markdown/    The text converted from each file ({counts['markdown']} files), as Markdown.
             The block at the top of each file says where it came from.
metadata/    case.json: the case's details, notes and the people assigned.
             files.json: every file, its SHA-256, tags, dates and its place
             in this ZIP. reviews.json: AI reviews saved in the case. AI
             output is unverified: check it against the cited documents.
audit/       Everything recorded about this case ({counts['audit']} entries), as CSV
             (opens in Excel) and JSON. Each entry carries its hash in the
             app's tamper-evident audit chain. When this export was made, the
             whole chain checked out: {'yes' if chain_ok else 'NO, tell the Admin'}.

Not included: Outlook mailboxes (PST/OST) themselves, because of their size.
Emails added to the case from a mailbox are in originals/ like any file.

Checking the files
------------------
manifest-sha256.txt lists the SHA-256 of every file in this ZIP. On Windows,
open PowerShell in the unzipped folder and run, for example:
  Get-FileHash "originals\\Letters\\offer.pdf"
and compare it with the line for that file.

This export contains confidential case material. Store and share it as you
would the case file itself.
"""


def run_export(rt: Runtime, job: dict) -> None:
    case_id = job["case_id"]
    export_id = job["options"].get("export", "")
    with rt.db.connect() as db:
        exp = db.execute("SELECT * FROM exports WHERE id = ? AND case_id = ?", (export_id, case_id)).fetchone()
        case = db.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if exp is None or case is None:  # the case was deleted meanwhile
        with rt.db.transaction() as db:
            jobs.finish(db, job["id"])
        return
    with rt.db.transaction() as db:
        db.execute("UPDATE exports SET state = 'building', error = '' WHERE id = ?", (export_id,))

    with rt.db.connect() as db:
        files = [dict(r) for r in db.execute("SELECT f.*, u.display_name AS added_by_name FROM files f LEFT JOIN users u ON u.id = f.added_by WHERE f.case_id = ? ORDER BY f.folder, f.name", (case_id,))]
        members = [dict(r) for r in db.execute(
            "SELECT u.username, u.display_name, u.role FROM case_members m JOIN users u ON u.id = m.user_id WHERE m.case_id = ? ORDER BY u.display_name", (case_id,))]
        mailboxes = [dict(r) for r in db.execute("SELECT name, size, sha256, emails, added_at FROM mailboxes WHERE case_id = ? ORDER BY added_at", (case_id,))]
        reviews = [dict(r) for r in db.execute(
            "SELECT id, username, kind, scope, question, provider, model, external, sources, answer, result, created_at, finished_at FROM ai_calls WHERE case_id = ? AND kind != 'ask' AND state = 'done' ORDER BY created_at",
            (case_id,))]
        audit_rows = _audit_rows(db, case_id)
        _, bad = audit.verify(db)

    out_dir = folder(rt)
    out_dir.mkdir(parents=True, exist_ok=True)
    need = sum(f["size"] for f in files) * 2 + 50 * 2**20  # originals, Markdown and room to spare
    if shutil.disk_usage(out_dir).free < need:
        with rt.db.transaction() as db:
            failed(db, job, f"There isn't enough free space on the data drive for this export (it needs about {need / 2**30:.1f} GB). Free some space, then export again.")
        return

    part = out_dir / f".{export_id}.part"
    names = _Names()
    manifest: list[tuple[str, str]] = []
    now = audit.now()
    total = len(files) * 2 + 5
    done = 0
    counts = {"originals": 0, "markdown": 0, "audit": len(audit_rows)}

    def put(zf: zipfile.ZipFile, arc: str, data: bytes) -> None:
        zf.writestr(zipfile.ZipInfo(arc, date_time=datetime.now().timetuple()[:6]), data, compress_type=zipfile.ZIP_DEFLATED)
        manifest.append((hashlib.sha256(data).hexdigest(), arc))

    try:
        with zipfile.ZipFile(part, "w", allowZip64=True) as zf:
            file_entries = []
            for f in files:
                entry = {
                    "id": f["id"], "name": f["name"], "folder": f["folder"], "type": f["source_type"], "size": f["size"],
                    "sha256": f["sha256"], "status": f["status"], "tags": json.loads(f["tags"] or "[]"),
                    "added_at": f["added_at"], "added_by": f["added_by_name"], "title": f["title"], "document_date": f["doc_date"],
                    "page_count": f["page_count"], "ocr": bool(f["ocr"]), "converted_at": f["converted_at"],
                    "markdown_sha256": f["markdown_sha256"], "warnings": json.loads(f["warnings"] or "[]"), "error": f["error"],
                    "attached_to": f["parent_id"], "near_duplicate_of": f["near_dup_of"],
                    "near_duplicate_similarity": round(f["near_dup_score"], 2) if f["near_dup_score"] is not None else None,
                    "original_in_zip": None, "markdown_in_zip": None,
                }
                src = rt.storage.original(case_id, f["id"], f["ext"])
                if src.exists():
                    arc = names.take("originals", f["folder"], f["name"])
                    digest = hashlib.sha256()
                    info = zipfile.ZipInfo(arc, date_time=_stamp(f["added_at"]))
                    info.compress_type = zipfile.ZIP_STORED if f["ext"] in STORED else zipfile.ZIP_DEFLATED
                    with src.open("rb") as fh, zf.open(info, "w", force_zip64=True) as dest:
                        for chunk in iter(lambda: fh.read(2**20), b""):
                            digest.update(chunk)
                            dest.write(chunk)
                    manifest.append((digest.hexdigest(), arc))
                    entry["original_in_zip"] = arc
                    counts["originals"] += 1
                done += 1
                md = rt.storage.markdown(case_id, f["id"])
                if md.exists():
                    arc = names.take("markdown", f["folder"], f["name"] + ".md")
                    put(zf, arc, md.read_bytes())
                    entry["markdown_in_zip"] = arc
                    counts["markdown"] += 1
                done += 1
                file_entries.append(entry)
                if done % 20 == 0:
                    jobs.progress(rt.db, job["id"], done, total)

            case_info = {
                "id": case["id"], "name": case["name"], "reference": case["reference"], "client": case["client"],
                "description": case["description"], "notes": case["notes"], "status": case["status"], "created_at": case["created_at"],
                "people": members, "mailboxes_not_included": mailboxes,
                "export": {"id": export_id, "made_at": now, "made_by": exp["username"], "app_version": rt.cfg.version},
            }
            put(zf, "metadata/case.json", _json(case_info))
            put(zf, "metadata/files.json", _json(file_entries))
            put(zf, "metadata/reviews.json", _json([
                {**r, "scope": json.loads(r["scope"]), "sources": json.loads(r["sources"]), "result": json.loads(r["result"]),
                 "external": bool(r["external"]), "note": "AI-generated, unverified"} for r in reviews]))
            put(zf, "audit/case-audit-trail.json", _json({"chain_checked_at": now, "chain_intact": bad is None, "entries": audit_rows}))
            put(zf, "audit/case-audit-trail.csv", _csv(audit_rows))
            put(zf, "README.txt", _readme(dict(case), now, exp["username"], counts, bad is None).replace("\n", "\r\n").encode("utf-8"))
            zf.writestr(zipfile.ZipInfo("manifest-sha256.txt", date_time=datetime.now().timetuple()[:6]),
                        "".join(f"{h}  {a}\r\n" for h, a in manifest), compress_type=zipfile.ZIP_DEFLATED)
    except OSError as exc:
        part.unlink(missing_ok=True)
        log.warning("export failed: %s", exc)
        with rt.db.transaction() as db:
            failed(db, job, "The export couldn't be written to the data drive. Check there's free space, then export again.")
        return

    digest = hashlib.sha256()
    with part.open("rb") as fh:
        for chunk in iter(lambda: fh.read(2**20), b""):
            digest.update(chunk)
    size = part.stat().st_size
    with rt.db.transaction() as db:
        if db.execute("SELECT 1 FROM exports WHERE id = ?", (export_id,)).fetchone() is None:
            part.unlink(missing_ok=True)  # the case was deleted while exporting
            jobs.finish(db, job["id"])
            return
        part.replace(path_of(rt, export_id))
        db.execute("UPDATE exports SET state = 'ready', size = ?, sha256 = ?, files = ?, finished_at = ? WHERE id = ?",
                   (size, digest.hexdigest(), counts["originals"], audit.now(), export_id))
        jobs.finish(db, job["id"])
        audit.record(db, action="case.export", user=None, username="worker", case_id=case_id, target=export_id,
                     detail=f"{counts['originals']} files, {counts['audit']} audit entries; zip sha256 {digest.hexdigest()}")


def _json(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")


def _stamp(iso: str | None) -> tuple:
    try:
        t = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
        return t.timetuple()[:6] if t.year >= 1980 else (1980, 1, 1, 0, 0, 0)
    except ValueError:
        return datetime.now().timetuple()[:6]


def view(row) -> dict:
    expires = None
    if row["finished_at"] and row["state"] == "ready":
        expires = (datetime.fromisoformat(row["finished_at"].replace("Z", "+00:00")) + timedelta(days=KEEP_DAYS)).isoformat().replace("+00:00", "Z")
    return {
        "id": row["id"], "state": row["state"], "error": row["error"], "size": row["size"], "sha256": row["sha256"],
        "files": row["files"], "created_at": row["created_at"], "username": row["username"], "finished_at": row["finished_at"],
        "expires_at": expires,
    }


def tidy(rt: Runtime) -> None:
    """Delete exports older than KEEP_DAYS, and leftovers of deleted cases
    or interrupted exports."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    with rt.db.transaction() as db:
        old = [r[0] for r in db.execute("SELECT id FROM exports WHERE state = 'ready' AND finished_at < ?", (cutoff,))]
        db.executemany("UPDATE exports SET state = 'expired' WHERE id = ?", [(i,) for i in old])
        keep = {r[0] for r in db.execute("SELECT id FROM exports WHERE state IN ('ready', 'queued', 'building')")}
    out = folder(rt)
    if not out.exists():
        return
    for p in out.iterdir():
        export_id = p.name.removeprefix(".").split(".")[0]
        if export_id in keep:
            continue
        try:
            p.unlink()
        except OSError:
            pass  # open in a download; next time
