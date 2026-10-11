"""Search one case: keyword (exact words, with operators) and meaning
(passages about the same thing, in other words), merged by reciprocal rank
fusion. Ask (phase 6) uses the same search to find the passages it cites.

A search only ever opens the one case's index, and filters are applied
against the case's own file list, so results can't come from elsewhere."""

import re
import sqlite3
from dataclasses import dataclass

from .index import query as q
from .index.embed import EmbedUnavailable
from .index.store import END, HIT
from .runtime import Runtime

RRF_K = 60
POOL = 200  # passages taken from each method before merging
MAX_IN = 5000  # above this many files, filters are applied after searching


@dataclass
class Filters:
    type: str | None = None
    folder: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    tag: str | None = None

    def active(self) -> bool:
        return any((self.type, self.folder and self.folder.strip("/"), self.date_from, self.date_to, self.tag))


def _files(db: sqlite3.Connection, case_id: str, f: Filters) -> dict[str, sqlite3.Row]:
    where, args = ["case_id = ?", "status IN ('converted', 'needs_ocr')"], [case_id]
    if f.type:
        where.append("source_type = ?")
        args.append(f.type)
    if f.folder and f.folder.strip("/"):
        folder = f.folder.strip("/")
        where.append("(folder = ? OR substr(folder, 1, ?) = ?)")
        args += [folder, len(folder) + 1, folder + "/"]
    # Documents without a date are left out once a date range is chosen.
    if f.date_from:
        where.append("substr(doc_date, 1, 10) >= ?")
        args.append(f.date_from)
    if f.date_to:
        where.append("substr(doc_date, 1, 10) <= ?")
        args.append(f.date_to)
    if f.tag:
        where.append("EXISTS (SELECT 1 FROM json_each(tags) WHERE value = ?)")
        args.append(f.tag)
    rows = db.execute(f"SELECT id, name, folder, source_type, doc_date, title, page_count, meta FROM files WHERE {' AND '.join(where)}", args)
    return {r["id"]: r for r in rows}


def _highlight(text: str, terms: list[str], width: int = 260) -> str:
    """A snippet around the first query word in the passage, with the words
    marked, for passages found by meaning."""
    words = [w for t in terms for w in t.split() if len(w) > 2]
    pattern = re.compile(r"\b(" + "|".join(re.escape(w.rstrip("*")) for w in words) + r")\w*", re.IGNORECASE) if words else None
    start = 0
    if pattern and (m := pattern.search(text)):
        start = max(0, m.start() - width // 3)
        start = text.rfind(" ", 0, start) + 1 if start else 0
    piece = text[start : start + width]
    if start + width < len(text):
        piece = piece[: piece.rfind(" ")] if " " in piece else piece
        piece += "…"
    if start:
        piece = "…" + piece
    piece = " ".join(piece.split())
    return pattern.sub(lambda m: HIT + m.group(0) + END, piece) if pattern else piece


def run(rt: Runtime, db: sqlite3.Connection, case_id: str, raw: str, mode: str = "hybrid", filters: Filters | None = None, limit: int = 25) -> dict:
    """Search a case. Raises query.QueryError for a search that can't run."""
    filters = filters or Filters()
    parsed = q.parse(raw)
    files = _files(db, case_id, filters)
    ids = list(files) if filters.active() and len(files) <= MAX_IN else None
    index = rt.index(case_id)

    keyword: list[dict] = []
    if mode in ("hybrid", "keyword"):
        try:
            keyword = index.keyword(parsed.fts if mode == "keyword" else parsed.fts_any, POOL, ids)
        except sqlite3.OperationalError as exc:
            if "fts5" in str(exc).lower() or "syntax" in str(exc).lower():
                raise q.QueryError("That search couldn't be read. Check the quotes and try again.") from exc
            raise

    semantic: list[dict] = []
    meaning = "off"
    if mode in ("hybrid", "semantic"):
        try:
            vector = rt.embedder.embed([parsed.text])[0]
            semantic = index.semantic(rt.cfg.embed_model, vector, POOL, ids)
            meaning = "on"
        except EmbedUnavailable:
            meaning = "unavailable"

    # Reciprocal rank fusion: each list votes 1 / (60 + rank) for a passage.
    merged: dict[int, dict] = {}
    for how, hits in (("words", keyword), ("meaning", semantic)):
        for rank, h in enumerate(hits):
            if h["file_id"] not in files:
                continue
            m = merged.setdefault(h["id"], {**h, "score": 0.0, "found_by": []})
            m["score"] += 1.0 / (RRF_K + rank + 1)
            m["found_by"].append(how)
            if how == "words":
                m["snippet"] = h["snippet"]

    # Group by document: best passages first, up to three per document.
    docs: dict[str, dict] = {}
    for h in sorted(merged.values(), key=lambda h: -h["score"]):
        d = docs.setdefault(h["file_id"], {"score": h["score"], "hits": []})
        if len(d["hits"]) < 3:
            d["hits"].append(h)
        else:
            d.setdefault("more", 0)
            d["more"] += 1
    results = []
    for file_id, d in sorted(docs.items(), key=lambda kv: -kv[1]["score"])[:limit]:
        f = files[file_id]
        results.append({
            "file": {"id": file_id, "name": f["name"], "folder": f["folder"], "type": f["source_type"], "title": f["title"], "doc_date": f["doc_date"], "page_count": f["page_count"]},
            "hits": [
                {
                    "passage": h["id"],
                    "page": h["page"] if h["page"] is not None and h["page"] >= 1 else None,
                    "heading": h["heading"],
                    "snippet": h.get("snippet") or _highlight(h["text"], parsed.terms),
                    "found_by": h["found_by"],
                }
                for h in d["hits"]
            ],
            "more": d.get("more", 0),
        })
    return {"results": results, "documents": len(docs), "terms": parsed.terms, "meaning": meaning, "mode": mode}
