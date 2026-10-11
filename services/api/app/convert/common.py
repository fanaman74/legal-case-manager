"""What every converter returns, and the error it raises for files it can't
read. Converters run in a separate process (see convert/__main__.py), so a
crash or hang in a parser never takes the worker down."""

import re
from dataclasses import dataclass, field
from pathlib import Path

# Bump when the Markdown a converter produces changes, so conversions can be
# traced to the code that made them.
CONVERTER_VERSION = 1
PREVIEW_WIDTH = 1100


class ConversionError(Exception):
    """A file that can't be converted, with a plain-language reason and next
    step. Shown to the user as is."""


@dataclass
class Attachment:
    name: str
    data: bytes


@dataclass
class Options:
    ocr: bool = False  # read pages with no text layer with OCR
    ocr_lang: str = "eng"
    tesseract: str = "tesseract"
    previews: Path | None = None  # where to write page images, if wanted


@dataclass
class Result:
    body: str
    page_count: int | None = None
    ocr: bool = False
    warnings: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    doc_date: str | None = None
    title: str | None = None
    # Pages that are images with no text layer, not read with OCR yet.
    scanned_pages: list[int] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)
    previews: int = 0


def safe_name(name: str, default: str = "attachment") -> str:
    """A file name from an email attachment: no folders, no control
    characters, not too long."""
    name = re.split(r"[\\/]", name or "")[-1]
    name = "".join(c for c in name if ord(c) >= 32 and c not in '<>:"|?*').strip(" .")
    if not name:
        name = default
    if len(name) > 150:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: 150 - len(ext) - 1] + "." + ext) if dot and len(ext) <= 10 else name[:150]
    return name


def page_list(pages: list[int]) -> str:
    """[1, 2, 3, 7] -> '1-3, 7'"""
    out, start, prev = [], None, None
    for p in sorted(pages):
        if start is None:
            start = prev = p
        elif p == prev + 1:
            prev = p
        else:
            out.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = p
    if start is not None:
        out.append(f"{start}-{prev}" if start != prev else str(start))
    return ", ".join(out)
