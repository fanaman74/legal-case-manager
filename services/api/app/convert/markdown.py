"""Small Markdown helpers shared by the converters: escaping, tables, page
markers, paragraph clean-up and the YAML front matter."""

import json
import re

PAGE_MARKER = "<!-- page: {} -->"
PAGE_MARKER_RE = re.compile(r"^<!-- page: (\d+) -->$", re.M)

_INLINE_SPECIAL = re.compile(r"([\\`*_\[\]<>|])")
_LINE_START = re.compile(r"^(\s*)([#>+-]|\d+[.)])(\s)")


def escape(text: str) -> str:
    """Escape characters that would turn plain text into Markdown syntax."""
    text = _INLINE_SPECIAL.sub(r"\\\1", text)
    return _LINE_START.sub(lambda m: f"{m.group(1)}\\{m.group(2)}{m.group(3)}", text)


def page_marker(n: int) -> str:
    return PAGE_MARKER.format(n)


def table(rows: list[list[str]]) -> str:
    """A GitHub-style table. The first row is the header. Cells keep their
    line breaks as <br> so a row stays on one line."""
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)

    def cell(c: str) -> str:
        return "<br>".join(" ".join(p.split()) for p in c.splitlines() if p.strip()).replace("\\|", "|").replace("|", "\\|")

    lines = []
    for i, r in enumerate(rows):
        r = [cell(c) for c in r] + [""] * (width - len(r))
        lines.append("| " + " | ".join(r) + " |")
        if i == 0:
            lines.append("|" + "|".join(["---"] * width) + "|")
    return "\n".join(lines)


_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
_SENTENCE_END = re.compile(r"[.!?:;\"')\]]$")


def join_lines(lines: list[str]) -> str:
    """Join the lines of one paragraph, mending words split with a hyphen at
    a line end."""
    text = "\n".join(l.strip() for l in lines if l.strip())
    text = _HYPHEN_BREAK.sub(lambda m: m.group(1) + m.group(2) if m.group(2).islower() else m.group(0), text)
    return text.replace("\n", " ")


def tidy(md: str) -> str:
    """Collapse runs of blank lines and trailing spaces (keeping the two
    spaces that mark a line break)."""
    lines = []
    for line in md.split("\n"):
        keep_break = line.endswith("  ") and line.strip()
        lines.append(line.rstrip() + ("  " if keep_break else ""))
    md = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    # A hard break before a blank line means nothing.
    md = re.sub(r"  \n\n", "\n\n", md)
    return md.strip() + "\n"


def front_matter(fields: dict) -> str:
    """YAML front matter. Every scalar is written as a JSON string or number,
    which is valid YAML, so names with colons or quotes can't break it."""
    lines = ["---"]
    for key, value in fields.items():
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, list):
            lines.append(f"{key}:")
            for v in value:
                if isinstance(v, dict):
                    first = True
                    for k2, v2 in v.items():
                        lines.append(f"  {'- ' if first else '  '}{k2}: {json.dumps(v2, ensure_ascii=False)}")
                        first = False
                else:
                    lines.append(f"  - {json.dumps(v, ensure_ascii=False)}")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, (int, float)):
            lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {json.dumps(str(value), ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def split_front_matter(md: str) -> tuple[str, str]:
    """(front matter without the --- lines, body)."""
    if md.startswith("---\n"):
        end = md.find("\n---\n", 4)
        if end != -1:
            return md[4:end], md[end + 5 :].lstrip("\n")
    return "", md
