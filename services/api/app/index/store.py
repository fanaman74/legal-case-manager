"""A case's search index, in the case's own folder:

  index/search.db    passages and their keyword index (SQLite FTS5)
  index/vectors/     one vector per passage (LanceDB), one table per model

Only the worker writes it; the web app opens it read-only. Every index
belongs to one case, so a search can never reach another case's files."""

import re
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

from .chunk import Chunk

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    file_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    page INTEGER,
    heading TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_file ON chunks(file_id, seq);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, heading, content='chunks', content_rowid='id',
    tokenize='porter unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text, heading) VALUES (new.id, new.text, new.heading);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text, heading) VALUES ('delete', old.id, old.text, old.heading);
END;
"""
HIT, END = "\x02", "\x03"  # highlight markers in snippets
_ID = re.compile(r"^[0-9a-f]{32}$")


def table_name(model: str) -> str:
    return "v_" + re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")


def _open(conn, model: str):
    """The model's vector table, or None if nothing has been written yet."""
    try:
        return conn.open_table(table_name(model))
    except ValueError:
        return None


class CaseIndex:
    def __init__(self, case_dir: Path):
        self.dir = case_dir / "index"
        self.db_path = self.dir / "search.db"
        self.vec_path = self.dir / "vectors"

    # --- writing (worker) -----------------------------------------------------------

    def _write(self) -> sqlite3.Connection:
        self.dir.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(SCHEMA)
        return db

    def replace_file(self, file_id: str, chunks: list[Chunk]) -> list[int]:
        """Replace a file's passages; returns their ids, in order."""
        assert _ID.match(file_id)
        with closing(self._write()) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
            ids = [
                db.execute("INSERT INTO chunks (file_id, seq, page, heading, text) VALUES (?, ?, ?, ?, ?)", (file_id, c.seq, c.page, c.heading, c.text)).lastrowid
                for c in chunks
            ]
            db.execute("COMMIT")
        return ids

    def replace_vectors(self, model: str, file_id: str, ids: list[int], pages: list[int | None], vectors: list[list[float]]) -> None:
        import lancedb
        import pyarrow as pa

        assert _ID.match(file_id)
        self.vec_path.mkdir(parents=True, exist_ok=True)
        conn = lancedb.connect(self.vec_path)
        dim = len(vectors[0]) if vectors else 0
        table = _open(conn, model)
        if table is None:
            if not vectors:
                return
            schema = pa.schema([
                pa.field("id", pa.int64()),
                pa.field("file_id", pa.string()),
                pa.field("page", pa.int32()),
                pa.field("vector", pa.list_(pa.float32(), dim)),
            ])
            table = conn.create_table(table_name(model), schema=schema)
        else:
            table.delete(f"file_id = '{file_id}'")
        if vectors:
            table.add([{"id": i, "file_id": file_id, "page": p if p is not None else -1, "vector": v} for i, p, v in zip(ids, pages, vectors)])

    def optimise(self, model: str) -> None:
        """Merge the many small writes into a few files and drop old versions
        (which would otherwise keep removed passages on disk)."""
        if not self.vec_path.exists():
            return
        import lancedb

        table = _open(lancedb.connect(self.vec_path), model)
        if table is not None:
            table.optimize(cleanup_older_than=timedelta(minutes=5))

    # --- reading (web app) ----------------------------------------------------------

    def _read(self) -> sqlite3.Connection | None:
        if not self.db_path.exists():
            return None
        db = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def keyword(self, fts: str, limit: int, file_ids: list[str] | None) -> list[dict]:
        db = self._read()
        if db is None:
            return []
        where, args = "", []
        if file_ids is not None:
            where = f" AND c.file_id IN ({','.join('?' * len(file_ids))})"
            args = file_ids
        with closing(db):
            rows = db.execute(
                f"""SELECT c.id, c.file_id, c.page, c.heading, c.text,
                           snippet(chunks_fts, 0, '{HIT}', '{END}', '…', 32) AS snippet, bm25(chunks_fts, 1.0, 0.5) AS rank
                    FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid
                    WHERE chunks_fts MATCH ?{where} ORDER BY rank LIMIT ?""",
                [fts, *args, limit],
            ).fetchall()
        return [dict(r) for r in rows]

    def semantic(self, model: str, vector: list[float], limit: int, file_ids: list[str] | None) -> list[dict]:
        if not self.vec_path.exists():
            return []
        import lancedb

        table = _open(lancedb.connect(self.vec_path), model)
        if table is None:
            return []
        q = table.search(vector).distance_type("cosine").limit(limit).select(["id", "file_id", "page"])
        if file_ids is not None:
            if not file_ids:
                return []
            q = q.where("file_id IN (" + ",".join(f"'{f}'" for f in file_ids if _ID.match(f)) + ")", prefilter=True)
        hits = q.to_list()
        if not hits:
            return []
        db = self._read()
        if db is None:
            return []
        with closing(db):
            ids = [h["id"] for h in hits]
            rows = {r["id"]: dict(r) for r in db.execute(f"SELECT id, file_id, page, heading, text FROM chunks WHERE id IN ({','.join('?' * len(ids))})", ids)}
        out = []
        for h in hits:
            r = rows.get(h["id"])
            if r is not None:  # gone since (re-indexed meanwhile)
                r["distance"] = h["_distance"]
                out.append(r)
        return out

    def passages(self, file_id: str) -> list[dict]:
        db = self._read()
        if db is None:
            return []
        with closing(db):
            return [dict(r) for r in db.execute("SELECT id, seq, page, heading, text FROM chunks WHERE file_id = ? ORDER BY seq", (file_id,))]

    def stats(self) -> dict:
        db = self._read()
        if db is None:
            return {"passages": 0}
        with closing(db):
            return {"passages": db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]}
