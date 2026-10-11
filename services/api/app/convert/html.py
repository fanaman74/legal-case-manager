"""HTML email bodies to Markdown, with the standard library's parser.

Keeps what matters for reading and citing: paragraphs, headings, lists,
tables, quotes and links. Drops styles, scripts, images and layout. Never
fetches anything (remote images and links are text only)."""

import re
from html.parser import HTMLParser

from .markdown import escape, table, tidy

_BLOCK = {"p", "div", "section", "article", "header", "footer", "main", "center", "address", "pre", "form", "fieldset", "dl", "dt", "dd", "hr"}
_SKIP = {"style", "script", "head", "title", "noscript", "template", "svg", "object", "iframe", "xml", "o:p"}
_LIST_ITEM = re.compile(r"^ +(- |\d+\. )")
_VOID = {"br", "img", "hr", "meta", "link", "input", "wbr", "col", "area", "base", "source"}


class _Converter(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []  # finished blocks and inline text
        self.skip = 0
        self.lists: list[list] = []  # [kind, counter]
        self.quote = 0
        self.link: list[str | None] = []
        self.pre = 0
        self.tables: list[dict] = []  # {"rows": [[...]], "cell": [parts] or None}
        self.bold = 0
        self.italic = 0

    # Text goes to the current table cell if there is one, else the output.
    def _emit(self, text: str) -> None:
        if self.tables and self.tables[-1]["cell"] is not None:
            self.tables[-1]["cell"].append(text)
        elif not self.tables:
            self.out.append(text)

    def _break(self, blank: bool = True) -> None:
        self._emit("\n\n" if blank else "\n")

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        a = dict(attrs)
        if tag in _BLOCK or tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._break()
            if tag.startswith("h") and len(tag) == 2 and not self.tables:
                self._emit("#" * int(tag[1]) + " ")
            if tag == "pre":
                self.pre += 1
            if tag == "hr":
                self._emit("---\n\n")
        elif tag == "br":
            self._emit("  \n" if not self.tables else "\n")
        elif tag in ("ul", "ol"):
            if not self.lists:
                self._break()
            self.lists.append([tag, 0])
        elif tag == "li":
            self._emit("\n")
            depth = max(len(self.lists) - 1, 0)
            if self.lists and self.lists[-1][0] == "ol":
                self.lists[-1][1] += 1
                self._emit("  " * depth + f"{self.lists[-1][1]}. ")
            else:
                self._emit("  " * depth + "- ")
        elif tag == "blockquote":
            self._break()
            self.quote += 1
            self._emit("\x00QUOTE+\x00")
        elif tag == "a":
            href = a.get("href") or ""
            self.link.append(href if href.startswith(("http://", "https://", "mailto:")) else None)
            if self.link[-1]:
                self._emit("[")
        elif tag in ("b", "strong"):
            self.bold += 1
            if self.bold == 1:
                self._emit("**")
        elif tag in ("i", "em"):
            self.italic += 1
            if self.italic == 1:
                self._emit("*")
        elif tag == "table":
            self._break()
            self.tables.append({"rows": [], "cell": None})
        elif tag == "tr" and self.tables:
            self.tables[-1]["rows"].append([])
        elif tag in ("td", "th") and self.tables:
            t = self.tables[-1]
            if not t["rows"]:
                t["rows"].append([])
            t["cell"] = []

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self.skip = max(self.skip - 1, 0)
            return
        if self.skip:
            return
        if tag in _BLOCK or tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            if tag == "pre":
                self.pre = max(self.pre - 1, 0)
            self._break()
        elif tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
            if not self.lists:
                self._break()
        elif tag == "blockquote":
            self._emit("\x00QUOTE-\x00")
            self.quote = max(self.quote - 1, 0)
            self._break()
        elif tag == "a":
            href = self.link.pop() if self.link else None
            if href:
                self._emit(f"]({href.replace(')', '%29').replace(' ', '%20')})")
        elif tag in ("b", "strong"):
            self.bold = max(self.bold - 1, 0)
            if self.bold == 0:
                self._emit("**")
        elif tag in ("i", "em"):
            self.italic = max(self.italic - 1, 0)
            if self.italic == 0:
                self._emit("*")
        elif tag in ("td", "th") and self.tables:
            t = self.tables[-1]
            if t["cell"] is not None:
                t["rows"][-1].append("".join(t["cell"]).strip())
                t["cell"] = None
        elif tag == "table" and self.tables:
            t = self.tables.pop()
            rows = [r for r in t["rows"] if r]
            # Layout tables (one column, or one row) read better as text.
            if rows and max(len(r) for r in rows) > 1 and len(rows) > 1:
                rendered = "\n\n" + table(rows) + "\n\n"
            else:
                rendered = "\n\n" + "\n\n".join(c for r in rows for c in r if c) + "\n\n"
            self._emit(rendered)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if self.skip:
            return
        if self.pre:
            self._emit(escape(data))
            return
        text = " ".join(data.split())
        if not text:
            if data and self.out and not self.out[-1].endswith((" ", "\n")):
                self._emit(" ")
            return
        if data[:1].isspace():
            text = " " + text
        if data[-1:].isspace():
            text += " "
        self._emit(escape(text))


def to_markdown(html: str) -> str:
    conv = _Converter()
    try:
        conv.feed(html)
        conv.close()
    except Exception:  # a broken document still gives what was parsed so far
        pass
    while conv.tables:  # unclosed tables
        conv.handle_endtag("table")
    raw = "".join(conv.out)
    # Apply blockquote prefixes line by line.
    lines, depth = [], 0
    for line in raw.split("\n"):
        line_depth = depth
        while "\x00QUOTE" in line:
            i = line.index("\x00QUOTE")
            depth = max(depth + (1 if line[i + 6] == "+" else -1), 0)
            line_depth = max(line_depth, depth)
            line = line[:i] + line[i + 8 :]
        hard_break = line.endswith("  ") and line.strip()
        stripped = line if _LIST_ITEM.match(line) else line.lstrip(" ")
        stripped = stripped.rstrip(" ") + ("  " if hard_break else "")
        lines.append(("> " * line_depth + stripped) if stripped and line_depth else (">" if depth else stripped))
    return tidy("\n".join(_tidy_quotes(lines)))


def _tidy_quotes(lines: list[str]) -> list[str]:
    """Keep one bare ">" between quoted paragraphs and none at a quote's edges."""
    out: list[str] = []
    for i, line in enumerate(lines):
        if line and set(line) == {">"}:
            nxt = next((l for l in lines[i + 1 :] if not (l and set(l) == {">"})), "")
            if out and out[-1].startswith(">") and set(out[-1]) != {">"} and nxt.startswith(">"):
                out.append(line)
            continue
        out.append(line)
    return out
