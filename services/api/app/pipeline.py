"""What the worker does with a convert job: run the converter in a child
process, then file its output (Markdown with front matter, page images,
email attachments as new files in the case) and record the result."""

import hashlib
import json
import logging
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from . import audit, jobs
from .convert import CONVERTER_VERSION
from .convert.markdown import front_matter
from .runtime import Runtime

log = logging.getLogger("worker.convert")

APP_ROOT = Path(__file__).resolve().parent.parent
SOURCE_TYPES = {
    ".docx": "word", ".pdf": "pdf", ".msg": "email", ".eml": "email", ".txt": "text",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".tif": "image", ".tiff": "image",
}
MAX_ATTEMPTS = 2


def _timeout(size: int, ocr: bool) -> int:
    """Seconds a conversion may take: generous, scaled by size, since OCR of
    a long scan is slow on an ordinary computer."""
    return min((7200 if ocr else 1800) + size // 2**20 * 10, 6 * 3600)


def _path_of(row) -> str:
    return "/".join(p for p in (row["folder"], row["name"]) if p)


def run_convert(rt: Runtime, job: dict) -> None:
    case_id, file_id = job["case_id"], job["file_id"]
    with rt.db.connect() as db:
        row = db.execute("SELECT * FROM files WHERE id = ? AND case_id = ?", (file_id, case_id)).fetchone()
    if row is None:  # deleted meanwhile
        with rt.db.transaction() as db:
            jobs.finish(db, job["id"])
        return
    ocr = bool(job["options"].get("ocr"))
    with rt.db.transaction() as db:
        db.execute("UPDATE files SET status = 'converting', error = '' WHERE id = ?", (file_id,))

    original = rt.storage.original(case_id, file_id, row["ext"])
    work = rt.storage.work_dir(case_id)
    try:
        args = [sys.executable, "-m", "app.convert", str(original), row["ext"], str(work), "--previews", "--lang", rt.cfg.ocr_lang]
        if ocr:
            args.append("--ocr")
        try:
            proc = subprocess.run(
                args, cwd=APP_ROOT, capture_output=True, timeout=_timeout(row["size"], ocr),
                creationflags=0x08000000 if sys.platform == "win32" else 0,
            )
        except subprocess.TimeoutExpired:
            _failed(rt, job, row, "Converting this file took too long, so it was stopped. If it's a very long scan, try splitting it into smaller files.")
            return
        result_file = work / "result.json"
        if proc.returncode != 0 or not result_file.is_file():
            err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
            log.warning("converter exited with %s for a %s file: %s", proc.returncode, row["ext"], err[-1][:300] if err else "")
            if job["attempts"] < MAX_ATTEMPTS:
                with rt.db.transaction() as db:
                    jobs.retry_later(db, job["id"])
                return
            _failed(rt, job, row, "The converter stopped unexpectedly on this file. It may be damaged. Open it on your computer to check it, or download it and upload a fresh copy.")
            return
        result = json.loads(result_file.read_text(encoding="utf-8"))
        if "error" in result:
            _failed(rt, job, row, result["error"])
            return
        _store(rt, job, row, work, result, ocr)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _failed(rt: Runtime, job: dict, row, message: str) -> None:
    with rt.db.transaction() as db:
        db.execute("UPDATE files SET status = 'failed', error = ? WHERE id = ?", (message, row["id"]))
        jobs.fail(db, job["id"], message)
        audit.record(db, action="file.convert", user=None, username="worker", outcome="failed", case_id=row["case_id"], target=_path_of(row), detail=message[:300])


def _store(rt: Runtime, job: dict, row, work: Path, result: dict, ocr: bool) -> None:
    case_id, file_id = row["case_id"], row["id"]
    body = (work / "body.md").read_text(encoding="utf-8")

    # Email attachments become files of their own, linked to the email.
    linked = []
    with rt.db.transaction() as db:
        for i, name in enumerate(result.get("attachments", [])):
            data = (work / "attachments" / str(i)).read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            existing = db.execute("SELECT id FROM files WHERE case_id = ? AND sha256 = ?", (case_id, sha)).fetchone()
            if existing:
                linked.append({"name": name, "doc_id": existing["id"], "duplicate": existing["id"] != file_id})
                continue
            ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext not in SOURCE_TYPES:
                continue
            new_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO files (id, case_id, name, folder, ext, source_type, size, sha256, status, added_at, added_by, parent_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
                (new_id, case_id, name, row["folder"], ext, SOURCE_TYPES[ext], len(data), sha, audit.now(), row["added_by"], file_id),
            )
            rt.storage.keep_bytes(case_id, new_id, ext, data)
            jobs.enqueue(db, "convert", case_id, new_id)
            audit.record(db, action="file.attachment", user=None, username="worker", case_id=case_id, target="/".join(p for p in (row["folder"], name) if p), detail=f"extracted from {_path_of(row)}; sha256 {sha}")
            linked.append({"name": name, "doc_id": new_id})

    meta = result.get("meta") or {}
    now = audit.now()
    fields = {
        "doc_id": file_id,
        "case_id": case_id,
        "source_filename": row["name"],
        "relative_path": _path_of(row),
        "source_type": row["source_type"],
        "sha256": row["sha256"],
        "converted_at": now,
        "converter": f"case-file-manager {CONVERTER_VERSION}",
        "page_count": result.get("page_count"),
        "ocr": bool(result.get("ocr")),
        "title": result.get("title"),
        "document_date": result.get("doc_date"),
        "parent_doc_id": row["parent_id"],
    }
    if row["source_type"] == "email":
        fields.update({k: meta.get(k) for k in ("from", "to", "cc", "date", "subject", "message_id", "thread")})
    fields["attachments"] = [{"name": a["name"], "doc_id": a["doc_id"]} for a in linked]
    markdown = front_matter(fields) + body
    md_sha = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    rt.storage.write_markdown(case_id, file_id, markdown)
    rt.storage.replace_previews(case_id, file_id, work / "previews" if result.get("previews") else None)

    scanned = result.get("scanned_pages") or []
    status = "needs_ocr" if scanned and result.get("page_count") and len(scanned) == result["page_count"] else "converted"
    meta["attachments"] = linked
    with rt.db.transaction() as db:
        if db.execute("SELECT 1 FROM files WHERE id = ?", (file_id,)).fetchone() is None:
            return  # the case was deleted while converting
        db.execute(
            """UPDATE files SET status = ?, error = '', page_count = ?, ocr = ?, warnings = ?, scanned_pages = ?, meta = ?,
               doc_date = ?, title = ?, converted_at = ?, markdown_sha256 = ?, previews = ? WHERE id = ?""",
            (status, result.get("page_count"), int(bool(result.get("ocr"))), json.dumps(result.get("warnings") or []), json.dumps(scanned),
             json.dumps(meta), result.get("doc_date"), result.get("title"), now, md_sha, result.get("previews") or 0, file_id),
        )
        jobs.finish(db, job["id"])
        detail = f"markdown sha256 {md_sha}; converter {CONVERTER_VERSION}"
        if result.get("page_count"):
            detail += f"; {result['page_count']} pages"
        if result.get("ocr"):
            detail += "; OCR"
        audit.record(db, action="file.convert", user=None, username="worker", case_id=case_id, target=_path_of(row), detail=detail)
        after_convert(db, case_id, file_id, status)


def after_convert(db, case_id: str, file_id: str, status: str) -> None:
    """Next step for a converted file: add it to the case's search index."""
    jobs.enqueue(db, "index", case_id, file_id)
