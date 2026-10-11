"""What the worker does with an index job: split a converted document into
passages, add them to the case's keyword index, and turn each into a vector
with the local embedding model for meaning search.

If the local model isn't running yet (it installs after the app on a new
computer), the file is still searchable by keyword, and meaning search
catches up by itself once the model is available."""

import logging
import threading

from . import audit, jobs
from .index import chunk
from .index.embed import EmbedUnavailable
from .runtime import Runtime

log = logging.getLogger("worker.index")

OPTIMISE_EVERY = 50
_writes: dict[str, int] = {}
_lock = threading.Lock()


def run_index(rt: Runtime, job: dict) -> None:
    case_id, file_id = job["case_id"], job["file_id"]
    with rt.db.connect() as db:
        row = db.execute("SELECT * FROM files WHERE id = ? AND case_id = ?", (file_id, case_id)).fetchone()
    if row is None or row["status"] not in ("converted", "needs_ocr"):
        # Deleted, or being converted again (which queues a fresh index job).
        with rt.db.transaction() as db:
            jobs.finish(db, job["id"])
        return

    path = rt.storage.markdown(case_id, file_id)
    markdown = path.read_text(encoding="utf-8") if path.exists() else ""
    passages = chunk.chunks(markdown)
    index = rt.index(case_id)
    with _lock:  # one writer per index at a time
        ids = index.replace_file(file_id, passages)
        state = "indexed"
        try:
            label = row["title"] or row["name"]
            vectors = rt.embedder.embed([f"{label}\n{p.heading}\n{p.text}" for p in passages]) if passages else []
        except EmbedUnavailable as exc:
            log.info("meaning search for a file waits for the local model: %s", exc)
            vectors, state = [], "keyword_only"
        index.replace_vectors(rt.cfg.embed_model, file_id, ids, [p.page for p in passages], vectors)
        _writes[case_id] = _writes.get(case_id, 0) + 1
        if _writes[case_id] >= OPTIMISE_EVERY:
            _writes[case_id] = 0
            index.optimise(rt.cfg.embed_model)

    with rt.db.transaction() as db:
        db.execute("UPDATE files SET index_state = ?, passages = ?, indexed_at = ? WHERE id = ?", (state, len(passages), audit.now(), file_id))
        jobs.finish(db, job["id"])


def catch_up(rt: Runtime) -> int:
    """Queue files that only have keyword search, once the local model
    answers. Returns how many were queued."""
    with rt.db.connect() as db:
        rows = db.execute("SELECT id, case_id FROM files WHERE index_state = 'keyword_only' LIMIT 5000").fetchall()
    if not rows:
        return 0
    try:
        rt.embedder.embed(["ready?"])
    except EmbedUnavailable:
        return 0
    with rt.db.transaction() as db:
        for r in rows:
            jobs.enqueue(db, "index", r["case_id"], r["id"])
    return len(rows)


def optimise_all(rt: Runtime) -> None:
    """Tidy every case's vectors (hourly, from the worker)."""
    with rt.db.connect() as db:
        cases = [r[0] for r in db.execute("SELECT DISTINCT case_id FROM files WHERE index_state = 'indexed'")]
    for case_id in cases:
        with _lock:
            try:
                rt.index(case_id).optimise(rt.cfg.embed_model)
            except Exception:
                log.exception("couldn't tidy a case's search index")
