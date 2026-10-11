"""Reading text from page images with Tesseract (installed by the launcher).

Tesseract is run as a program with a fixed command line; only the image path
changes. Its plain-text output separates paragraphs with blank lines."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

from .common import ConversionError, Options
from .markdown import escape, join_lines

PAGE_TIMEOUT = 180
OCR_DPI = 300


def _no_window() -> int:
    return 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW


def read_image(img: Image.Image, opts: Options) -> str:
    """OCR one page image; returns Markdown paragraphs."""
    with tempfile.TemporaryDirectory(prefix="ocr-") as tmp:
        path = Path(tmp) / "page.png"
        img.convert("L").save(path)
        try:
            proc = subprocess.run(
                [opts.tesseract, str(path), "stdout", "-l", opts.ocr_lang, "--psm", "3"],
                capture_output=True,
                timeout=PAGE_TIMEOUT,
                creationflags=_no_window(),
                env={**os.environ, "OMP_THREAD_LIMIT": "1"},
            )
        except FileNotFoundError as exc:
            raise ConversionError("OCR isn't installed, so scanned pages can't be read. The Admin can install it from Components in the Control Center.") from exc
        except subprocess.TimeoutExpired as exc:
            raise ConversionError("Reading a scanned page took too long. Try again; if it keeps happening, the scan may be damaged.") from exc
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ConversionError("OCR couldn't read a scanned page" + (f" ({err[-1][:200]})" if err else "") + ".")
    return paragraphs(proc.stdout.decode("utf-8", "replace"))


def paragraphs(text: str) -> str:
    blocks, current = [], []
    for line in text.replace("\f", "\n").splitlines():
        if line.strip():
            current.append(line)
        elif current:
            blocks.append(escape(join_lines(current)))
            current = []
    if current:
        blocks.append(escape(join_lines(current)))
    return "\n\n".join(blocks)
