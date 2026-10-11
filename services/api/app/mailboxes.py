"""What the worker does with mailbox jobs: read a PST into its search
database (job "pst"), and add chosen emails to the case as .eml files
(job "pst_add"), which are then converted and indexed like any upload."""

import hashlib
import json
import logging
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from contextlib import closing

from . import audit, jobs
from .runtime import Runtime

log = logging.getLogger("worker.mailbox")

APP_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
STALL = 1800  # seconds without progress before a mailbox read is stopped
FLAGS = 0x08000000 if sys.platform == "win32" else 0  # no console window


def _child(args: list[str]) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "app.pst", *args], cwd=APP_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", creationflags=FLAGS)


def _fail(rt: Runtime, job: dict, mailbox_id: str, message: str) -> None:
    with rt.db.transaction() as db:
        db.execute("UPDATE mailboxes SET status = 'failed', error = ? WHERE id = ?", (message, mailbox_id))
        jobs.fail(db, job["id"], message)
        row = db.execute("SELECT name, case_id FROM mailboxes WHERE id = ?", (mailbox_id,)).fetchone()
        if row:
            audit.record(db, action="mailbox.index", user=None, username="worker", outcome="failed", case_id=row["case_id"], target=row["name"], detail=message[:300])


def run_index(rt: Runtime, job: dict) -> None:
    mailbox_id = job["options"]["mailbox"]
    with rt.db.connect() as db:
        mb = db.execute("SELECT * FROM mailboxes WHERE id = ?", (mailbox_id,)).fetchone()
    if mb is None:
        with rt.db.transaction() as db:
            jobs.finish(db, job["id"])
        return
    with rt.db.transaction() as db:
        db.execute("UPDATE mailboxes SET status = 'indexing', error = '', done = 0 WHERE id = ?", (mailbox_id,))
    src = rt.storage.mailbox(mb["case_id"], mailbox_id, mb["ext"])
    dest = rt.storage.mailbox_db(mb["case_id"], mailbox_id)
    proc = _child(["index", str(src), str(dest)])
    stderr: list[str] = []
    threading.Thread(target=lambda: stderr.extend(proc.stderr), daemon=True).start()
    last = time.monotonic()
    done = total = 0
    error = ""
    watchdog = threading.Timer(STALL, lambda: proc.kill())
    watchdog.daemon = True
    watchdog.start()
    try:
        for line in proc.stdout:
            word, _, rest = line.strip().partition(" ")
            if word == "progress":
                done, total = (int(x) for x in rest.split())
                if time.monotonic() - last > 1:
                    last = time.monotonic()
                    jobs.progress(rt.db, job["id"], done, total)
                    with rt.db.connect() as db:
                        db.execute("UPDATE mailboxes SET done = ?, total = ? WHERE id = ?", (done, total, mailbox_id))
                watchdog.cancel()
                watchdog = threading.Timer(STALL, lambda: proc.kill())
                watchdog.daemon = True
                watchdog.start()
            elif word == "done":
                done = int(rest)
            elif word == "error":
                error = rest
        proc.wait()
    finally:
        watchdog.cancel()
    if error:
        _fail(rt, job, mailbox_id, error)
        return
    if proc.returncode != 0 or not dest.exists():
        log.warning("mailbox reader exited with %s: %s", proc.returncode, (stderr[-1] if stderr else "").strip()[:300])
        _fail(rt, job, mailbox_id, "Reading this mailbox stopped unexpectedly. It may be damaged. Try running Outlook's Inbox Repair Tool (scanpst) on a copy, then upload it again.")
        return
    with rt.db.transaction() as db:
        db.execute("UPDATE mailboxes SET status = 'ready', emails = ?, done = ?, total = ?, indexed_at = ? WHERE id = ?", (done, done, max(total, done), audit.now(), mailbox_id))
        jobs.finish(db, job["id"])
        audit.record(db, action="mailbox.index", user=None, username="worker", case_id=mb["case_id"], target=mb["name"], detail=f"{done} items")


_SAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def _file_name(subject: str, date: str | None) -> str:
    base = _SAFE.sub(" ", subject or "").strip(" .") or "(no subject)"
    day = (date or "")[:10]
    return f"{day + ' ' if day else ''}{base[:120]}.eml"


def run_add(rt: Runtime, job: dict) -> None:
    opts = job["options"]
    mailbox_id, ids, attachments = opts["mailbox"], [int(i) for i in opts["messages"]], bool(opts.get("attachments", True))
    with rt.db.connect() as db:
        mb = db.execute("SELECT * FROM mailboxes WHERE id = ?", (mailbox_id,)).fetchone()
        user = db.execute("SELECT * FROM users WHERE id = ?", (job["created_by"],)).fetchone() if job["created_by"] else None
        already = {r[0] for r in db.execute("SELECT message FROM mailbox_added WHERE mailbox_id = ?", (mailbox_id,))}
    if mb is None or mb["status"] != "ready":
        with rt.db.transaction() as db:
            jobs.finish(db, job["id"])
        return
    case_id = mb["case_id"]
    ids = [i for i in ids if i not in already]
    with closing(sqlite3.connect(f"file:{rt.storage.mailbox_db(case_id, mailbox_id)}?mode=ro", uri=True)) as mdb:
        locators = {r[0]: r[1] for r in mdb.execute(f"SELECT id, locator FROM messages WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
    ids = [i for i in ids if i in locators]
    if not ids:
        with rt.db.transaction() as db:
            jobs.finish(db, job["id"])
        return
    work = rt.storage.work_dir(case_id)
    try:
        args = ["export", str(rt.storage.mailbox(case_id, mailbox_id, mb["ext"])), mb["name"], str(work)]
        if not attachments:
            args.append("--no-attachments")
        proc = subprocess.run([sys.executable, "-m", "app.pst", *args, *(locators[i] for i in ids)], cwd=APP_ROOT, capture_output=True, timeout=3600 + 10 * len(ids), creationflags=FLAGS)
        manifest_file = work / "manifest.json"
        if proc.returncode != 0 or not manifest_file.exists():
            message = "Adding these emails stopped unexpectedly. Try adding fewer at a time."
            with rt.db.transaction() as db:
                jobs.fail(db, job["id"], message)
            return
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        label = mb["name"].rsplit(".", 1)[0]
        added = 0
        with rt.db.transaction() as db:
            for msg_id, entry in zip(ids, manifest):
                if "file" not in entry:
                    continue
                data = (work / entry["file"]).read_bytes()
                sha = hashlib.sha256(data).hexdigest()
                existing = db.execute("SELECT id FROM files WHERE case_id = ? AND sha256 = ?", (case_id, sha)).fetchone()
                folder = "/".join(p for p in ("Email", _SAFE.sub(" ", label).strip() or "Mailbox", *(_SAFE.sub(" ", x).strip() for x in (entry.get("folder") or "").split("/"))) if p)
                name = _file_name(entry.get("subject", ""), entry.get("date"))
                if existing:
                    file_id = existing["id"]
                else:
                    file_id = uuid.uuid4().hex
                    db.execute(
                        "INSERT INTO files (id, case_id, name, folder, ext, source_type, size, sha256, status, added_at, added_by) VALUES (?, ?, ?, ?, '.eml', 'email', ?, ?, 'queued', ?, ?)",
                        (file_id, case_id, name, folder, len(data), sha, audit.now(), job["created_by"]),
                    )
                    rt.storage.keep_bytes(case_id, file_id, ".eml", data)
                    jobs.enqueue(db, "convert", case_id, file_id)
                    audit.record(db, action="file.from_mailbox", user=dict(user) if user else None, username=None if user else "worker", case_id=case_id,
                                 target=f"{folder}/{name}", detail=f"from {mb['name']}; sha256 {sha}; attachments {'included' if attachments else 'left in mailbox'}")
                    added += 1
                db.execute("INSERT OR IGNORE INTO mailbox_added (mailbox_id, message, file_id) VALUES (?, ?, ?)", (mailbox_id, msg_id, file_id))
            jobs.finish(db, job["id"])
        log.info("added %d emails to a case", added)
    finally:
        shutil.rmtree(work, ignore_errors=True)
