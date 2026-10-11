"""Word documents (.docx) to Markdown.

Walks the document body in order, so headings, numbered clauses, lists,
tables, footnotes and text boxes keep their place. Headers and footers are
not part of the body, so they never reach the text. Clause numbers that Word
generates ("4.2", "(b)") are worked out and written into the text, because
lawyers cite them.

Word saves where it last broke pages; those become page markers, so passages
can be cited by page as Word showed them."""

import re
import zipfile
from pathlib import Path

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from lxml import etree

from .common import ConversionError, Options, Result
from .markdown import escape, page_marker, table, tidy

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
FOOTNOTES = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes"
ENDNOTES = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/endnotes"
COMMENTS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"


def _on(el) -> bool:
    """A Word on/off property (<w:b/>, <w:b w:val="0"/>)."""
    if el is None:
        return False
    return el.get(qn("w:val"), "true").lower() not in ("0", "false", "off", "none")


# --- Numbering -----------------------------------------------------------------


def _roman(n: int) -> str:
    out = ""
    for v, s in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= v:
            out, n = out + s, n - v
    return out


def _letters(n: int) -> str:
    # Word repeats letters after z: a..z, aa..zz
    return chr(ord("a") + (n - 1) % 26) * ((n - 1) // 26 + 1)


def _fmt(n: int, kind: str) -> str:
    return {
        "lowerLetter": _letters(n),
        "upperLetter": _letters(n).upper(),
        "lowerRoman": _roman(n),
        "upperRoman": _roman(n).upper(),
        "decimalZero": f"{n:02d}",
        "none": "",
    }.get(kind, str(n))


class Numbering:
    """Works out list labels ("4.2", "(b)", "•") the way Word displays them."""

    def __init__(self, doc):
        self.levels: dict[str, dict[int, dict]] = {}  # numId -> ilvl -> {fmt, text, start}
        self.counters: dict[str, dict[int, int]] = {}
        try:
            root = doc.part.numbering_part.element
        except (NotImplementedError, KeyError, AttributeError):
            return
        abstract = {}
        for a in root.findall(f"{W}abstractNum"):
            abstract[a.get(qn("w:abstractNumId"))] = self._levels(a)
        for num in root.findall(f"{W}num"):
            ref = num.find(f"{W}abstractNumId")
            if ref is None:
                continue
            levels = {k: dict(v) for k, v in abstract.get(ref.get(qn("w:val")), {}).items()}
            for ov in num.findall(f"{W}lvlOverride"):
                ilvl = int(ov.get(qn("w:ilvl"), "0"))
                start = ov.find(f"{W}startOverride")
                lvl = ov.find(f"{W}lvl")
                if lvl is not None:
                    levels[ilvl] = self._levels_one(lvl)
                if start is not None and ilvl in levels:
                    levels[ilvl]["start"] = int(start.get(qn("w:val"), "1"))
            self.levels[num.get(qn("w:numId"))] = levels

    def _levels(self, abstract) -> dict[int, dict]:
        return {int(l.get(qn("w:ilvl"), "0")): self._levels_one(l) for l in abstract.findall(f"{W}lvl")}

    @staticmethod
    def _levels_one(lvl) -> dict:
        def val(tag, default):
            el = lvl.find(f"{W}{tag}")
            return el.get(qn("w:val"), default) if el is not None else default

        return {"fmt": val("numFmt", "decimal"), "text": val("lvlText", "%1."), "start": int(val("start", "1") or 1)}

    def label(self, num_id: str, ilvl: int) -> tuple[str, bool] | None:
        """(label, is_bullet) for the next item of this list, or None."""
        levels = self.levels.get(num_id)
        if not levels or ilvl not in levels or num_id == "0":
            return None
        counters = self.counters.setdefault(num_id, {})
        lvl = levels[ilvl]
        counters[ilvl] = counters.get(ilvl, lvl["start"] - 1) + 1
        for deeper in [k for k in counters if k > ilvl]:
            del counters[deeper]
        if lvl["fmt"] == "bullet":
            return "", True

        def sub(m):
            k = int(m.group(1)) - 1
            if k not in levels:
                return ""
            return _fmt(counters.get(k, levels[k]["start"]), levels[k]["fmt"])

        return re.sub(r"%(\d)", sub, lvl["text"]).strip(), False


# --- Styles --------------------------------------------------------------------


class Styles:
    def __init__(self, doc):
        self.by_id: dict[str, dict] = {}
        for s in doc.styles.element.findall(f"{W}style"):
            sid = s.get(qn("w:styleId"))
            name_el, based, ppr = s.find(f"{W}name"), s.find(f"{W}basedOn"), s.find(f"{W}pPr")
            info = {"name": (name_el.get(qn("w:val")) if name_el is not None else sid) or "", "based": based.get(qn("w:val")) if based is not None else None, "outline": None, "num": None}
            if ppr is not None:
                ol = ppr.find(f"{W}outlineLvl")
                if ol is not None:
                    info["outline"] = int(ol.get(qn("w:val"), "9"))
                info["num"] = _num_pr(ppr)
            self.by_id[sid] = info

    def heading_level(self, style_id: str | None) -> int | None:
        seen = 0
        while style_id and seen < 10:
            info = self.by_id.get(style_id)
            if not info:
                return None
            name = info["name"].lower()
            if name == "title":
                return 1
            m = re.match(r"heading\s*(\d)$", name)
            if m:
                return min(int(m.group(1)) + 1, 6)
            if info["outline"] is not None and info["outline"] < 9:
                return min(info["outline"] + 2, 6)
            style_id, seen = info["based"], seen + 1
        return None

    def numbering(self, style_id: str | None) -> tuple[str, int] | None:
        seen = 0
        while style_id and seen < 10:
            info = self.by_id.get(style_id)
            if not info:
                return None
            if info["num"]:
                return info["num"]
            style_id, seen = info["based"], seen + 1
        return None

    def name(self, style_id: str | None) -> str:
        return self.by_id.get(style_id or "", {}).get("name", "")


def _num_pr(ppr) -> tuple[str, int] | None:
    num = ppr.find(f"{W}numPr")
    if num is None:
        return None
    nid, ilvl = num.find(f"{W}numId"), num.find(f"{W}ilvl")
    if nid is None:
        return None
    return nid.get(qn("w:val")), int(ilvl.get(qn("w:val"), "0")) if ilvl is not None else 0


# --- Body ----------------------------------------------------------------------


class _Writer:
    def __init__(self, doc, styles: Styles, numbering: Numbering, notes: dict[str, str]):
        self.doc, self.styles, self.numbering, self.notes = doc, styles, numbering, notes
        self.blocks: list[str] = []
        self.page = 1
        self.page_breaks = 0
        self.text_since_break = False
        self.tracked = False
        self.used_notes: list[str] = []

    # A page break becomes a marker on its own line.
    def new_page(self, current: list[str]) -> list[str]:
        if not self.text_since_break:
            return current
        if current and "".join(current).strip():
            self.blocks.append("".join(current).strip())
        self.page += 1
        self.page_breaks += 1
        self.blocks.append(page_marker(self.page))
        self.text_since_break = False
        return []

    def runs(self, el, out: list[str], extra: list[str], in_table: bool) -> list[str]:
        """Inline content of a paragraph (or hyperlink, insertion, field)."""
        for child in el:
            tag = child.tag
            if tag == f"{W}r":
                out = self.run(child, out, extra, in_table)
            elif tag == f"{W}hyperlink":
                inner = self.runs(child, [], extra, in_table)
                text = "".join(inner)
                rid = child.get(qn("r:id"))
                url = None
                if rid and rid in self.doc.part.rels and self.doc.part.rels[rid].is_external:
                    url = self.doc.part.rels[rid].target_ref
                if url and url.startswith(("http://", "https://", "mailto:")) and text.strip():
                    out.append(f"[{text}]({url.replace(')', '%29').replace(' ', '%20')})")
                else:
                    out.append(text)
            elif tag in (f"{W}ins", f"{W}moveTo"):
                self.tracked = True
                out = self.runs(child, out, extra, in_table)
            elif tag in (f"{W}del", f"{W}moveFrom"):
                self.tracked = True
            elif tag in (f"{W}smartTag", f"{W}sdt", f"{W}sdtContent", f"{W}fldSimple", f"{W}customXml", f"{W}bdo", f"{W}dir"):
                out = self.runs(child, out, extra, in_table)
        return out

    def run(self, r, out: list[str], extra: list[str], in_table: bool) -> list[str]:
        rpr = r.find(f"{W}rPr")
        if rpr is not None and _on(rpr.find(f"{W}vanish")):
            return out
        bold = rpr is not None and _on(rpr.find(f"{W}b"))
        italic = rpr is not None and _on(rpr.find(f"{W}i"))
        text: list[str] = []

        def flush():
            s = "".join(text)
            text.clear()
            if not s:
                return
            if s.strip() and (bold or italic):
                lead, core, trail = s[: len(s) - len(s.lstrip())], s.strip(), s[len(s.rstrip()) :]
                mark = "***" if bold and italic else "**" if bold else "*"
                out.append(f"{lead}{mark}{escape(core)}{mark}{trail}")
            else:
                out.append(escape(s))
            if s.strip():
                self.text_since_break = True

        for c in r:
            tag = c.tag
            if tag == f"{W}t":
                text.append(c.text or "")
            elif tag == f"{W}tab":
                text.append(" ")
            elif tag in (f"{W}br", f"{W}cr"):
                if c.get(qn("w:type")) == "page" and not in_table:
                    flush()
                    out = self.new_page(out)
                else:
                    text.append("\n")
            elif tag == f"{W}lastRenderedPageBreak" and not in_table:
                flush()
                out = self.new_page(out)
            elif tag == f"{W}noBreakHyphen":
                text.append("-")
            elif tag == f"{W}softHyphen":
                pass
            elif tag == f"{W}sym":
                text.append("•")
            elif tag in (f"{W}footnoteReference", f"{W}endnoteReference"):
                flush()
                key = ("f" if tag == f"{W}footnoteReference" else "e") + (c.get(qn("w:id")) or "")
                if key in self.notes:
                    if key not in self.used_notes:
                        self.used_notes.append(key)
                    out.append(f"[^{self.used_notes.index(key) + 1}]")
            elif tag in (f"{W}drawing", f"{W}pict", f"{W}object") or tag.endswith("}AlternateContent"):
                # Text boxes: their paragraphs follow the one that holds them.
                for box in c.iter(f"{W}txbxContent"):
                    for p in box.iter(f"{W}p"):
                        t = "".join(self.runs(p, [], [], True)).strip()
                        if t:
                            extra.append(t)
                    break  # AlternateContent repeats the same box as a fallback
        flush()
        return out

    def paragraph(self, p, in_table: bool = False) -> str | None:
        ppr = p.find(f"{W}pPr")
        style_id = None
        num = None
        if ppr is not None:
            ps = ppr.find(f"{W}pStyle")
            style_id = ps.get(qn("w:val")) if ps is not None else None
            num = _num_pr(ppr)
        extra: list[str] = []
        content = self.runs(p, [], extra, in_table)
        text = "".join(content)
        # Lines inside one paragraph keep their breaks.
        text = "  \n".join(line.strip() for line in text.split("\n")).strip()
        level = None if in_table else self.styles.heading_level(style_id)
        num = num or self.styles.numbering(style_id)
        label, bullet, ilvl = "", False, 0
        if num and text:
            got = self.numbering.label(*num)
            if got:
                label, bullet = got
                ilvl = num[1]
        if not text:
            for e in extra:
                self.blocks.append(e)
            return None
        if level and len(text) <= 300 and "\n" not in text:
            plain = text.replace("**", "").replace("*", "") if text.startswith("**") else text
            line = "#" * level + " " + (escape(label) + " " if label else "") + plain
        elif bullet:
            line = "  " * ilvl + "- " + text
        elif label:
            line = "  " * ilvl + escape(label) + " " + text
        elif "list bullet" in self.styles.name(style_id).lower():
            line = "- " + text
        else:
            line = text
        if in_table:
            return line
        self.blocks.append(line)
        self.blocks.extend(extra)
        return line

    def table(self, tbl) -> None:
        rows = []
        for tr in tbl.findall(f"{W}tr"):
            row = []
            for tc in tr.findall(f"{W}tc"):
                tcpr = tc.find(f"{W}tcPr")
                span, merged = 1, False
                if tcpr is not None:
                    gs = tcpr.find(f"{W}gridSpan")
                    span = int(gs.get(qn("w:val"), "1")) if gs is not None else 1
                    vm = tcpr.find(f"{W}vMerge")
                    merged = vm is not None and vm.get(qn("w:val"), "continue") == "continue"
                parts = []
                if not merged:
                    for p in tc.iter(f"{W}p"):
                        t = self.paragraph(p, in_table=True)
                        if t:
                            parts.append(t)
                cell = "\n".join(t.replace("  \n", "\n") for t in parts)
                row.append(cell)
                row += [""] * (span - 1)
            rows.append(row)
        rendered = table(rows)
        if rendered:
            self.blocks.append(rendered)
            self.text_since_break = True

    def body(self, body) -> None:
        for child in body:
            if child.tag == f"{W}p":
                self.paragraph(child)
            elif child.tag == f"{W}tbl":
                self.table(child)
            elif child.tag == f"{W}sdt":
                content = child.find(f"{W}sdtContent")
                if content is not None:
                    self.body(content)
            elif child.tag in (f"{W}ins", f"{W}customXml"):
                self.tracked = self.tracked or child.tag == f"{W}ins"
                self.body(child)


def _notes(doc) -> tuple[dict[str, str], bool]:
    """Footnote and endnote text by id ('f2', 'e1'), and whether the
    document has comments."""
    notes, comments = {}, False
    for rel in doc.part.rels.values():
        if rel.is_external:
            continue
        if rel.reltype == COMMENTS:
            comments = True
            continue
        prefix = {FOOTNOTES: "f", ENDNOTES: "e"}.get(rel.reltype)
        if not prefix:
            continue
        try:
            root = etree.fromstring(rel.target_part.blob)
        except etree.XMLSyntaxError:
            continue
        for note in root:
            nid = note.get(qn("w:id"))
            if note.get(qn("w:type")) in ("separator", "continuationSeparator", "continuationNotice"):
                continue
            text = " ".join("".join(t.text or "" for t in p.iter(f"{W}t")).strip() for p in note.iter(f"{W}p")).strip()
            if text:
                notes[prefix + str(nid)] = text
    return notes, comments


_LIST_LINE = re.compile(r"^\s*(- |\d+\. )")


def _join(blocks: list[str]) -> str:
    """Blank lines between blocks, but list items stay together."""
    out: list[str] = []
    for b in blocks:
        if out and _LIST_LINE.match(b) and _LIST_LINE.match(out[-1].rsplit("\n", 1)[-1]):
            out[-1] += "\n" + b
        else:
            out.append(b)
    return "\n\n".join(out)


def _saved_pages(path: Path) -> int | None:
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("docProps/app.xml")
        m = re.search(rb"<Pages>(\d+)</Pages>", xml)
        return int(m.group(1)) if m else None
    except (KeyError, zipfile.BadZipFile, OSError, ValueError):
        return None


def convert(path: Path, _opts: Options) -> Result:
    try:
        doc = Document(str(path))
    except (PackageNotFoundError, KeyError, zipfile.BadZipFile, ValueError, etree.XMLSyntaxError) as exc:
        raise ConversionError(
            "This Word document can't be opened. It may be password-protected or damaged. "
            "If it has a password, open it in Word, remove the password, save a copy and upload that."
        ) from exc
    notes, comments = _notes(doc)
    w = _Writer(doc, Styles(doc), Numbering(doc), notes)
    w.blocks.append(page_marker(1))
    w.body(doc.element.body)
    warnings = []
    saved_pages = _saved_pages(path)
    if w.page_breaks == 0 and (saved_pages or 1) > 1:
        # No page positions saved: one marker for page 1 would mislead.
        w.blocks = [b for b in w.blocks if b != page_marker(1)]
        warnings.append("Word didn't save page positions in this document, so passages can't be cited by page number.")
        page_count = saved_pages
    else:
        page_count = max(w.page, saved_pages or 0) if w.page_breaks else (saved_pages or 1)
    if w.tracked:
        warnings.append("This document has tracked changes. The Markdown shows the text with all changes accepted.")
    if comments:
        warnings.append("This document has comments. They aren't included in the Markdown; open the original to read them.")
    if w.used_notes:
        w.blocks.append("")
        w.blocks.extend(f"[^{i}]: {escape(notes[k])}" for i, k in enumerate(w.used_notes, start=1))
    body = _join(w.blocks)
    if not body.replace(page_marker(1), "").strip():
        warnings.append("This document has no text.")
    props = doc.core_properties
    date = props.created or props.modified
    return Result(
        body=tidy(body),
        page_count=page_count,
        warnings=warnings,
        doc_date=date.isoformat() if date else None,
        title=(props.title or "").strip() or None,
    )
