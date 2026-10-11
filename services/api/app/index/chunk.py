"""Split a converted document into passages for search and AI answers.

Each passage stays inside one page, so every hit and every citation points
at a page. Passages follow the document's own paragraphs and headings and
carry the heading they sit under."""

import re
from dataclasses import dataclass

from ..convert.markdown import PAGE_MARKER_RE, split_front_matter

TARGET = 1200  # characters: about 250 words, a few paragraphs
MAX = 2000

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|<>~])")
_EMPHASIS = re.compile(r"(\*\*|__)(.+?)\1")
_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)]+)\)")
_FOOTREF = re.compile(r"\[\^(\d+)\]")


@dataclass
class Chunk:
    seq: int
    page: int | None
    heading: str
    text: str


def plain(md: str) -> str:
    """Markdown as readable text: no escapes, emphasis or link targets, so
    snippets read like the document."""
    out = []
    for line in md.split("\n"):
        line = line.rstrip()
        m = _HEADING.match(line)
        if m:
            line = m.group(2)
        if line.startswith(">"):
            line = line.lstrip("> ")
        if re.fullmatch(r"\|?[\s:|-]+\|?", line) and "-" in line:
            continue  # a table's divider row
        line = _LINK.sub(r"\1", line)
        line = _EMPHASIS.sub(r"\2", line)
        line = _FOOTREF.sub(r"[\1]", line)
        line = _ESCAPE.sub(r"\1", line)
        line = line.replace("<br>", " ")
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _pages(body: str) -> list[tuple[int | None, str]]:
    parts = PAGE_MARKER_RE.split(body)
    # split() with one group gives [before, n1, text1, n2, text2, ...]
    out: list[tuple[int | None, str]] = []
    if parts[0].strip():
        out.append((None, parts[0]))
    for i in range(1, len(parts), 2):
        out.append((int(parts[i]), parts[i + 1]))
    return out


def _blocks(text: str) -> list[str]:
    blocks, cur = [], []
    for line in text.split("\n"):
        if not line.strip():
            if cur:
                blocks.append("\n".join(cur))
                cur = []
            continue
        if _HEADING.match(line) and cur:
            blocks.append("\n".join(cur))
            cur = []
        cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    return blocks


def _split_long(block: str) -> list[str]:
    if len(block) <= MAX:
        return [block]
    pieces, cur = [], ""
    units = _SENTENCE.split(block) if "\n" not in block.strip() else block.split("\n")
    for u in units:
        while len(u) > MAX:  # one enormous sentence or table row
            if cur:
                pieces.append(cur)
                cur = ""
            cut = u.rfind(" ", 0, MAX)
            cut = cut if cut > MAX // 2 else MAX
            pieces.append(u[:cut])
            u = u[cut:].lstrip()
        if cur and len(cur) + len(u) + 1 > TARGET:
            pieces.append(cur)
            cur = u
        else:
            cur = f"{cur} {u}" if cur and "\n" not in block.strip() else (f"{cur}\n{u}" if cur else u)
    if cur:
        pieces.append(cur)
    return pieces


def chunks(markdown: str) -> list[Chunk]:
    """Passages of a converted document (front matter is skipped)."""
    _, body = split_front_matter(markdown)
    out: list[Chunk] = []
    headings: dict[int, str] = {}

    def heading() -> str:
        return " > ".join(headings[k] for k in sorted(headings))

    for page, text in _pages(body):
        cur: list[str] = []
        cur_heading = heading()
        size = 0
        body_seen = False  # the passage has more than headings

        def flush():
            nonlocal cur, size, body_seen
            txt = plain("\n\n".join(cur))
            if txt:
                out.append(Chunk(len(out), page, cur_heading, txt))
            cur, size, body_seen = [], 0, False

        for block in _blocks(text):
            m = _HEADING.match(block.split("\n", 1)[0])
            if m:
                level = len(m.group(1))
                if cur and size > TARGET // 3:
                    flush()
                for k in [k for k in headings if k >= level]:
                    del headings[k]
                headings[level] = plain(m.group(2))[:200]
                if not body_seen:
                    cur_heading = heading()
            for piece in _split_long(block):
                if cur and size + len(piece) > TARGET:
                    flush()
                    cur_heading = heading()
                cur.append(piece)
                size += len(piece) + 2
            if not m or "\n" in block:
                body_seen = True
        flush()
    return out
