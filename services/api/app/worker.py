"""Background worker: converts, indexes and reads mailboxes, taking jobs from
the queue in app.db (see jobs.py).

It writes a heartbeat file every few seconds, which the launcher reads for
the Control Center, and a status file with the queue's counts. Each kind of
work has its own threads, so a long mailbox doesn't hold up conversions."""

import json
import logging
import signal
import sys
import threading
import time
from collections.abc import Callable

from . import export, indexing, jobs, mailboxes, neardup, pipeline, settings
from .ai import calls, local
from .runtime import Runtime

INTERVAL = 5
IDLE_SLEEP = 1.0

log = logging.getLogger("worker")


def beat(cfg: settings.Settings) -> None:
    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    tmp = cfg.heartbeat_file.with_suffix(".tmp")
    tmp.write_text(f"{int(time.time())} {cfg.version}\n")
    tmp.replace(cfg.heartbeat_file)


def write_status(rt: Runtime) -> None:
    """Queue counts for the Control Center. Counts only: no case or file
    names, so nothing confidential reaches the launcher or its diagnostics."""
    with rt.db.connect() as db:
        counts = jobs.counts(db)
        files = {r["status"]: r["n"] for r in db.execute("SELECT status, COUNT(*) AS n FROM files GROUP BY status")}
    status = {"updated": int(time.time()), "jobs": counts, "files": files, **extra_status(rt)}
    tmp = rt.cfg.run_dir / "worker-status.tmp"
    tmp.write_text(json.dumps(status), encoding="utf-8")
    tmp.replace(rt.cfg.run_dir / "worker-status.json")


def extra_status(rt: Runtime) -> dict:
    """Search index counts, for the Control Center (counts only)."""
    with rt.db.connect() as db:
        r = db.execute(
            """SELECT COUNT(DISTINCT CASE WHEN passages > 0 THEN case_id END) AS cases, COALESCE(SUM(passages), 0) AS chunks,
                      SUM(index_state = 'indexed') AS indexed, SUM(index_state = 'keyword_only') AS keyword_only,
                      SUM(index_state = '') AS pending, SUM(index_state = 'failed') AS failures
               FROM files WHERE status IN ('converted', 'needs_ocr')"""
        ).fetchone()
    problem = ""
    if r["keyword_only"]:
        problem = (f"{r['keyword_only']:,} files can only be searched by keyword until the local AI model is running. "
                   "Check Local AI models in Components; meaning search catches up by itself.")
    return {"index": {"cases": r["cases"] or 0, "chunks": r["chunks"] or 0, "model": rt.cfg.embed_model, "problem": problem,
                      "indexed": r["indexed"] or 0, "pending": r["pending"] or 0, "failures": r["failures"] or 0}}


# Kinds of work and the function that does each job.
HANDLERS: dict[str, Callable[[Runtime, dict], None]] = {
    "convert": pipeline.run_convert,
    "index": indexing.run_index,
    "dups": neardup.run_case,
    "pst": mailboxes.run_index,
    "pst_add": mailboxes.run_add,
    "model": local.run_pull,
    "export": export.run_export,
}


def lanes(cfg: settings.Settings) -> list[tuple[str, ...]]:
    """Which job kinds each worker thread takes."""
    # Indexing has one thread: the local model works on one request at a
    # time anyway, and each case's index has one writer.
    # Mailboxes have their own thread, so a large PST never holds up uploads.
    # A local AI model download is one long job, so it has its own thread too,
    # and so has a case export.
    return [("convert",)] * cfg.convert_workers + [("index", "dups"), ("pst_add", "pst"), ("model",), ("export",)]


def run_lane(rt: Runtime, kinds: tuple[str, ...], stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            job = jobs.claim(rt.db, kinds)
        except Exception:
            log.exception("couldn't read the job queue")
            stop.wait(INTERVAL)
            continue
        if job is None:
            stop.wait(IDLE_SLEEP)
            continue
        try:
            HANDLERS[job["kind"]](rt, job)
        except Exception as exc:
            log.exception("%s job %s failed", job["kind"], job["id"])
            try:
                with rt.db.transaction() as db:
                    jobs.fail(db, job["id"], "Something went wrong in the background worker. Retry it; if it keeps failing, the Admin can check the worker's log in the Control Center.")
                    if job["kind"] == "export":
                        export.failed(db, job, "The export couldn't be finished. Try again; if it keeps failing, the Admin can check the worker's log in the Control Center.")
                    if job["kind"] == "pst":
                        db.execute("UPDATE mailboxes SET status = 'failed', error = ? WHERE id = ?", ("The worker couldn't read this mailbox. Retry it from the Jobs page; if it keeps failing, the Admin can check the worker's log in the Control Center.", job["options"].get("mailbox")))
                    if job["kind"] == "convert" and job["file_id"]:
                        db.execute("UPDATE files SET status = 'failed', error = ? WHERE id = ?", ("The worker couldn't convert this file. Retry it; if it keeps failing, the Admin can check the worker's log in the Control Center.", job["file_id"]))
            except Exception:
                log.exception("couldn't record the failure of job %s (%s)", job["id"], type(exc).__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per passage otherwise
    cfg = settings.load()
    rt = Runtime(cfg)
    stop = threading.Event()

    def _stop(*_):
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("worker started, version %s", cfg.version)
    healthy = None
    threads: list[threading.Thread] = []
    started = False
    last_tidy = 0.0
    last_catch_up = 0.0
    while not stop.is_set():
        try:
            beat(cfg)
            if not started:
                back = jobs.recover(rt.db)
                if back:
                    log.info("%d unfinished jobs put back in the queue", back)
                for i, kinds in enumerate(lanes(cfg)):
                    t = threading.Thread(target=run_lane, args=(rt, kinds, stop), name=f"{'-'.join(kinds)}-{i}", daemon=True)
                    t.start()
                    threads.append(t)
                started = True
                log.info("taking jobs: %s", ", ".join(t.name for t in threads))
            write_status(rt)
            if time.time() - last_catch_up > 300:
                last_catch_up = time.time()
                queued = indexing.catch_up(rt)
                if queued:
                    log.info("the local model is available: adding meaning search for %d files", queued)
            if time.time() - last_tidy > 3600:
                jobs.tidy(rt.db)
                calls.tidy(rt)
                export.tidy(rt)
                rt.storage.empty_trash()
                indexing.optimise_all(rt)
                last_tidy = time.time()
            if healthy is not True:
                log.info("writing heartbeats to the data folder")
            healthy = True
        except Exception as exc:  # the data folder or database is unavailable
            if healthy is not False:
                log.warning("can't use the data folder: %s", exc)
            healthy = False
        stop.wait(INTERVAL)
    log.info("worker stopping")
    for t in threads:
        t.join(timeout=10)
    return 0


def healthcheck(cfg: settings.Settings | None = None) -> int:
    """Exit 0 if the heartbeat is fresh."""
    cfg = cfg or settings.load()
    try:
        age = time.time() - int(cfg.heartbeat_file.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return 1
    return 0 if age < INTERVAL * 6 else 1


if __name__ == "__main__":
    sys.exit(healthcheck() if sys.argv[1:] == ["health"] else main())
