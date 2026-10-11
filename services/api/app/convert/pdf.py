"""PDF to Markdown.

Text comes from pdfminer's layout analysis (through pdfplumber), which groups
characters into blocks in reading order, so two-column pages read column by
column. Ruled tables become Markdown tables. Running headers, footers and
page numbers (the same line at the top or bottom of most pages) are dropped
from the text. Each page starts with a page marker so passages can be cited
by page.

Pages that are pictures of text (scans) are detected. They are read with OCR
only when asked (opts.ocr), because OCR is slow; until then the file is
flagged so someone can choose to run it."""

import re
import statistics
from collections import Counter
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from pdfminer.layout import LTChar, LTTextBox, LTTextLine
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfminer.psparser import PSException

from . import ocr, previews
from .common import ConversionError, Options, Result, page_list
from .markdown import escape, join_lines, page_marker, table, tidy

MAX_PAGES = 5000
LAPARAMS = {"line_margin": 0.5, "char_margin": 2.0, "boxes_flow": 0.5}
_PAGE_NUMBER = re.compile(r"^(page\s*)?[-–—(]?\s*([0-9]{1,4}|[ivxlcdm]{1,6})\s*[-–—)]?(\s*(of|/)\s*[0-9]{1,4})?$", re.I)
_BULLET = re.compile(r"^[•●▪■◦‣∙·–]\s*")


def _norm(text: str) -> str:
    return re.sub(r"\d+", "#", " ".join(text.lower().split()))


def _chars(box):
    for line in box:
        if isinstance(line, LTTextLine):
            for c in line:
                if isinstance(c, LTChar):
                    yield c


def _text_boxes(page) -> list[dict]:
    """The page's text lines in reading order (pdfminer's blocks, so columns
    stay apart), with position and font details."""
    out = []
    for block, obj in enumerate(o for o in page.layout if isinstance(o, LTTextBox)):
        for line in obj:
            if not isinstance(line, LTTextLine):
                continue
            text = " ".join(line.get_text().split())  # justified text has wide gaps
            if not text:
                continue
            chars = [c for c in line if isinstance(c, LTChar) and c.get_text().strip()]
            sizes = [round(c.size * 2) / 2 for c in chars]
            out.append({
                "text": text,
                "block": block,
                "top": page.height - line.y1,
                "bottom": page.height - line.y0,
                "x0": line.x0,
                "x1": line.x1,
                "size": statistics.median(sizes) if sizes else 0,
                "sizes": sizes,
                "bold": bool(chars) and all("bold" in (c.fontname or "").lower() for c in chars),
            })
    return out


def _paragraphs(lines: list[dict]) -> list[list[dict]]:
    """Group lines into paragraphs. A new paragraph starts at a new block
    unless the previous line ran to the column's edge (the sentence carries
    on), and at any change of font size, a wide gap or a short last line."""
    if not lines:
        return []
    right = max(l["x1"] for l in lines)
    left = min(l["x0"] for l in lines)
    width = max(right - left, 1)
    paras: list[list[dict]] = [[lines[0]]]
    for prev, line in zip(lines, lines[1:]):
        height = max(prev["bottom"] - prev["top"], 1)
        gap = line["top"] - prev["bottom"]
        full = prev["x1"] >= right - width * 0.12
        same_font = abs(line["size"] - prev["size"]) < 0.6 and line["bold"] == prev["bold"]
        joined = (
            same_font
            and -height < gap <= height * 1.2
            and abs(line["x0"] - prev["x0"]) < 20
            and (line["block"] == prev["block"] or (full and not prev["text"].endswith(":")))
        )
        if joined:
            paras[-1].append(line)
        else:
            paras.append([line])
    return paras


def _inside(box: dict, bbox) -> bool:
    x0, top, x1, bottom = bbox
    cx, cy = (box["x0"] + box["x1"]) / 2, (box["top"] + box["bottom"]) / 2
    return x0 - 2 <= cx <= x1 + 2 and top - 2 <= cy <= bottom + 2


def _unreadable(text: str) -> bool:
    """Text layers from fonts without a character map come out as (cid:12)."""
    return text.count("(cid:") * 6 > max(len(text), 1) * 0.3


def convert(path: Path, opts: Options) -> Result:
    try:
        pdf = pdfplumber.open(path, laparams=LAPARAMS)
    except Exception as exc:  # pdfminer raises many kinds for broken or locked files
        cause = getattr(exc, "args", [None])[0] if exc.args else None
        if isinstance(exc, PDFPasswordIncorrect) or isinstance(cause, PDFPasswordIncorrect) or "password" in str(exc).lower():
            raise ConversionError("This PDF is password-protected. Open it, save a copy without the password, and upload that.") from exc
        raise ConversionError("This PDF can't be opened. It may be damaged or not really a PDF. Open it on your computer to check it.") from exc

    warnings: list[str] = []
    scanned: list[int] = []
    pages: list[dict] = []  # per page: boxes, tables, ocr text
    with pdf:
        total = len(pdf.pages)
        if total > MAX_PAGES:
            warnings.append(f"Only the first {MAX_PAGES} of {total} pages were converted.")
        render = None
        try:
            render = pdfium.PdfDocument(str(path))
        except pdfium.PdfiumError:
            render = None  # previews and OCR need it; text still works
        for i, page in enumerate(pdf.pages[:MAX_PAGES], start=1):
            try:
                boxes = _text_boxes(page)
                found = page.find_tables()
                tables = [{"top": t.bbox[1], "rows": [[c or "" for c in row] for row in t.extract()], "bbox": t.bbox} for t in found]
            except (PSException, ValueError, KeyError, TypeError, AttributeError) as exc:
                warnings.append(f"Page {i} couldn't be read ({type(exc).__name__}).")
                boxes, tables = [], []
            boxes = [b for b in boxes if not any(_inside(b, t["bbox"]) for t in tables)]
            text = " ".join(b["text"] for b in boxes)
            no_text = len(re.sub(r"\s", "", text)) < 10 and not tables
            unreadable = not no_text and _unreadable(text)
            entry = {"boxes": boxes, "tables": tables, "ocr": None, "scan": False}
            if (no_text and page.images) or unreadable:
                entry["scan"] = True
                if opts.ocr and render is not None:
                    img = render[i - 1].render(scale=ocr.OCR_DPI / 72).to_pil()
                    entry["ocr"] = ocr.read_image(img, opts)
                    entry["boxes"], entry["tables"] = [], []
                else:
                    scanned.append(i)
            if render is not None and opts.previews is not None:
                pg = render[i - 1]
                width = pg.get_width() or 612
                previews.save(pg.render(scale=min(previews.PREVIEW_WIDTH / width, 3)).to_pil(), opts, i)
            pages.append(entry)
            page.flush_cache()  # keep memory flat on long documents
        if render is not None:
            render.close()
        meta = pdf.metadata or {}

    # Running headers and footers: the same first or last block on most pages.
    edge = Counter()
    for p in pages:
        if p["boxes"]:
            by_top = sorted(p["boxes"], key=lambda b: b["top"])
            edge.update({_norm(b["text"]) for b in by_top[:2] + by_top[-2:]})
    threshold = max(3, len(pages) * 0.5)
    repeated = {k for k, n in edge.items() if n >= threshold and len(k) <= 200}

    all_sizes = Counter(s for p in pages for b in p["boxes"] for s in b["sizes"])
    body_size = all_sizes.most_common(1)[0][0] if all_sizes else 0
    heading_sizes = sorted({b["size"] for p in pages for b in p["boxes"] if body_size and b["size"] >= body_size * 1.15 and len(b["text"]) <= 200}, reverse=True)

    parts: list[str] = []
    for n, p in enumerate(pages, start=1):
        parts.append(page_marker(n))
        if p["ocr"] is not None:
            parts.append(p["ocr"] or "*(No text found on this page.)*")
            continue
        top_edge = min((b["top"] for b in p["boxes"]), default=0)
        bottom_edge = max((b["bottom"] for b in p["boxes"]), default=0)
        pending = sorted(p["tables"], key=lambda t: t["top"])
        lines = []
        for b in p["boxes"]:
            at_edge = b["top"] <= top_edge + 1 or b["bottom"] >= bottom_edge - 1
            if at_edge and (_norm(b["text"]) in repeated or _PAGE_NUMBER.match(b["text"])):
                continue
            lines.append(b)
        for para in _paragraphs(lines):
            while pending and pending[0]["top"] < para[0]["top"]:
                parts.append(table(pending.pop(0)["rows"]))
            first = para[0]
            joined = " ".join(l["text"] for l in para)
            text = escape(join_lines([l["text"] for l in para]))
            if first["size"] in heading_sizes and len(joined) <= 200:
                level = min(heading_sizes.index(first["size"]) + 1, 3)
                parts.append("#" * level + " " + text)
            elif first["bold"] and len(para) <= 2 and len(joined) <= 120 and not joined.rstrip().endswith((".", ",", ";")):
                parts.append("### " + text)
            elif _BULLET.match(joined):
                parts.append("- " + escape(join_lines([_BULLET.sub("", para[0]["text"])] + [l["text"] for l in para[1:]])))
            else:
                parts.append(text)
        for t in pending:
            parts.append(table(t["rows"]))

    if scanned:
        if len(scanned) == len(pages):
            warnings.append("This PDF is a scan with no text layer. Run OCR to read it.")
        else:
            warnings.append(f"Page{'s' if len(scanned) > 1 else ''} {page_list(scanned)} {'are' if len(scanned) > 1 else 'is a'} scanned image{'s' if len(scanned) > 1 else ''} with no text. Run OCR to read {'them' if len(scanned) > 1 else 'it'}.")
    ocred = [n for n, p in enumerate(pages, start=1) if p["ocr"] is not None]
    if ocred:
        warnings.append(f"Page{'s' if len(ocred) > 1 else ''} {page_list(ocred)} {'were' if len(ocred) > 1 else 'was'} read with OCR. Check names and figures against the original.")
    title = meta.get("Title")
    title = title.strip() if isinstance(title, str) and title.strip() else None
    return Result(
        body=tidy("\n\n".join(x for x in parts if x)),
        page_count=len(pages),
        ocr=bool(ocred),
        warnings=warnings,
        scanned_pages=scanned,
        title=title,
        doc_date=_pdf_date(meta.get("CreationDate")),
        previews=len(pages) if opts.previews is not None and pages else 0,
    )


def _pdf_date(value) -> str | None:
    """D:20240305142200+00'00' -> 2024-03-05T14:22:00+00:00"""
    if isinstance(value, bytes):
        value = value.decode("latin-1", "replace")
    if not isinstance(value, str):
        return None
    m = re.match(r"^D?:?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?([Zz+-])?(\d{2})?'?(\d{2})?", value.strip())
    if not m:
        return None
    y, mo, d, h, mi, s, sign, th, tm = m.groups()
    try:
        out = f"{y}-{mo or '01'}-{d or '01'}T{h or '00'}:{mi or '00'}:{s or '00'}"
        if sign in ("+", "-") and th:
            out += f"{sign}{th}:{tm or '00'}"
        elif sign in ("Z", "z"):
            out += "+00:00"
        from datetime import datetime

        datetime.fromisoformat(out)
        return out
    except ValueError:
        return None
