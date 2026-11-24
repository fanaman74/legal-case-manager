"""Splitting a converter's messages into notes and warnings.

A note tells you something about the document that nobody needs to act on
("this document has tracked changes", "these pages were read with OCR").
A warning means some of the content may be missing from the Markdown
("page 4 couldn't be read"). The file list flags only warnings, so a file
with nothing wrong doesn't look like it needs attention.

Messages about scanned pages are notes too: the worker reads those pages
with OCR by itself, and the file's OCR status says how far it has got."""

import json

NOTE_MARKERS = (
    "read with OCR",           # pdf: pages read with OCR, check names and figures
    "with no text. Run OCR",   # pdf: some pages are scans (before 0.2: OCR was run by hand)
    "read with OCR by itself", # pdf: some or all pages are scans
    "scan with no text layer", # pdf: the whole file is a scan (OCR runs by itself)
    "tracked changes",         # word
    "has comments",            # word
    "page positions",          # word: no page numbers to cite
)


def is_note(message: str) -> bool:
    return any(m in message for m in NOTE_MARKERS)


def split(messages: list[str]) -> tuple[list[str], list[str]]:
    """(warnings, notes)."""
    return [m for m in messages if not is_note(m)], [m for m in messages if is_note(m)]


def migrate(db) -> None:
    """Move the notes out of the warnings of files converted before notes
    existed (database migration 7)."""
    rows = db.execute("SELECT id, warnings FROM files WHERE warnings != '[]'").fetchall()
    for r in rows:
        warnings, found = split(json.loads(r[1] or "[]"))
        if found:
            db.execute("UPDATE files SET warnings = ?, notes = ? WHERE id = ?", (json.dumps(warnings), json.dumps(found), r[0]))
