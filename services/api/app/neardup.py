"""Near-duplicates: documents in a case whose text is almost the same, such
as the Word and PDF versions of one letter, or a contract and its signed
scan. Exact copies are already refused on upload (same SHA-256); this finds
the ones that differ only a little.

Each converted document gets a fingerprint (MinHash over runs of five words)
and is filed under 16 band keys. Documents that share a band key are
compared properly, so a new document is never compared with every other
one. Two documents count as near-duplicates when about 80% or more of their
five-word runs are the same. The later one is flagged; nothing is removed.

No AI is involved, and nothing leaves this computer."""

import hashlib
import re
import sqlite3

import numpy as np

from . import jobs
from .runtime import Runtime

THRESHOLD = 0.8
MIN_WORDS = 50  # shorter texts (a cover email, a blank scan) match too easily
SHINGLE = 5
PERMS = 128
BANDS, ROWS = 16, 8
MAX_SHINGLES = 400_000

_rng = np.random.default_rng(20261011)  # fixed, so fingerprints stay comparable
_A = _rng.integers(1, 2**63, PERMS, dtype=np.uint64) | np.uint64(1)
_B = _rng.integers(0, 2**63, PERMS, dtype=np.uint64)
_WORD = re.compile(r"\w+", re.UNICODE)
_FRONT = re.compile(r"\A---\n.*?\n---\n", re.S)
_PAGE = re.compile(r"<!--\s*page:?\s*\d+\s*-->", re.I)


def words(markdown: str) -> list[str]:
    body = _PAGE.sub(" ", _FRONT.sub("", markdown, count=1))
    return _WORD.findall(body.lower())


def signature(markdown: str) -> np.ndarray | None:
    """The document's MinHash fingerprint, or None if it is too short."""
    w = words(markdown)
    if len(w) < MIN_WORDS:
        return None
    runs = {" ".join(w[i:i + SHINGLE]) for i in range(len(w) - SHINGLE + 1)}
    hashes = np.fromiter(
        (int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "little") for s in list(runs)[:MAX_SHINGLES]),
        dtype=np.uint64,
    )
    with np.errstate(over="ignore"):  # multiply-shift hashing wraps on purpose
        mixed = (hashes[None, :] * _A[:, None] + _B[:, None]) >> np.uint64(32)
    return mixed.min(axis=1).astype(np.uint32)


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.count_nonzero(a == b)) / PERMS


def _band_keys(sig: np.ndarray) -> list[int]:
    out = []
    for band in range(BANDS):
        digest = hashlib.blake2b(sig[band * ROWS:(band + 1) * ROWS].tobytes(), digest_size=8).digest()
        out.append(int.from_bytes(digest, "little", signed=True))
    return out


def _forget(db: sqlite3.Connection, file_id: str) -> None:
    db.execute("DELETE FROM near_bands WHERE file_id = ?", (file_id,))
    db.execute("DELETE FROM near_sigs WHERE file_id = ?", (file_id,))
    db.execute("UPDATE files SET near_dup_of = NULL, near_dup_score = NULL WHERE id = ?", (file_id,))
    # Files that were flagged against this one are checked again by their
    # own next index; until then they keep pointing at it.


def check(db: sqlite3.Connection, case_id: str, file_id: str, markdown: str) -> dict | None:
    """Fingerprint one document and flag it, or an earlier match, as a
    near-duplicate. Call inside a transaction. Returns the match, if any."""
    _forget(db, file_id)
    sig = signature(markdown)
    db.execute("INSERT INTO near_sigs (file_id, case_id, sig) VALUES (?, ?, ?)", (file_id, case_id, sig.tobytes() if sig is not None else None))
    if sig is None:
        return None
    keys = _band_keys(sig)
    db.executemany("INSERT INTO near_bands (case_id, band, hash, file_id) VALUES (?, ?, ?, ?)", [(case_id, i, k, file_id) for i, k in enumerate(keys)])
    marks = " OR ".join("(band = ? AND hash = ?)" for _ in keys)
    candidates = [r[0] for r in db.execute(
        f"SELECT DISTINCT file_id FROM near_bands WHERE case_id = ? AND file_id != ? AND ({marks})",
        (case_id, file_id, *[x for i, k in enumerate(keys) for x in (i, k)]),
    )]
    if not candidates:
        return None
    me = db.execute("SELECT added_at, id FROM files WHERE id = ?", (file_id,)).fetchone()
    best = None
    for row in db.execute(
        f"SELECT s.file_id, s.sig, f.added_at, f.near_dup_score FROM near_sigs s JOIN files f ON f.id = s.file_id WHERE s.file_id IN ({','.join('?' * len(candidates))})",
        candidates,
    ):
        score = similarity(sig, np.frombuffer(row["sig"], dtype=np.uint32))
        if score < THRESHOLD:
            continue
        earlier = (row["added_at"], row["file_id"]) < (me["added_at"], me["id"])
        if earlier:
            if best is None or score > best["score"]:
                best = {"file_id": row["file_id"], "score": score}
        elif row["near_dup_score"] is None or score > row["near_dup_score"]:
            # The other one came later: it is the near-duplicate of this one.
            db.execute("UPDATE files SET near_dup_of = ?, near_dup_score = ? WHERE id = ?", (file_id, score, row["file_id"]))
    if best:
        db.execute("UPDATE files SET near_dup_of = ?, near_dup_score = ? WHERE id = ?", (best["file_id"], best["score"], file_id))
    return best


def run_case(rt: Runtime, job: dict) -> None:
    """Fingerprint every converted document in a case that has none yet
    (files converted before near-duplicates were looked for)."""
    case_id = job["case_id"]
    with rt.db.connect() as db:
        rows = db.execute(
            """SELECT id FROM files WHERE case_id = ? AND status IN ('converted', 'needs_ocr')
               AND id NOT IN (SELECT file_id FROM near_sigs) ORDER BY added_at, id""",
            (case_id,),
        ).fetchall()
    for i, r in enumerate(rows):
        path = rt.storage.markdown(case_id, r["id"])
        if path.exists():
            text = path.read_text(encoding="utf-8")
            with rt.db.transaction() as db:
                if db.execute("SELECT 1 FROM files WHERE id = ?", (r["id"],)).fetchone():
                    check(db, case_id, r["id"], text)
        if i % 20 == 0:
            jobs.progress(rt.db, job["id"], i, len(rows))
    with rt.db.transaction() as db:
        jobs.finish(db, job["id"])
