"""Plain text files."""

import re
from pathlib import Path

from .common import Result
from .markdown import escape, tidy

MAX_BYTES = 200 * 2**20


def decode(data: bytes) -> tuple[str, str]:
    """(text, encoding). Byte-order marks first, then UTF-8, then Windows'
    Western European code page, which can decode any byte."""
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if data.startswith(bom):
            return data.decode(enc, "replace"), enc
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace"), "cp1252"


def convert(path: Path, _opts) -> Result:
    data = path.read_bytes()[:MAX_BYTES]
    text, enc = decode(data)
    warnings = [] if enc.startswith("utf") else ["The text wasn't UTF-8, so it was read as Windows Western European text. Check accented characters."]
    if path.stat().st_size > MAX_BYTES:
        warnings.append("Only the first 200 MB of this file were converted.")
    # Line breaks are kept (they matter in letters, addresses and lists) and
    # nothing in the text is read as Markdown.
    paras = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    body = "\n\n".join("  \n".join(escape(l.rstrip()) for l in p.strip("\n").split("\n")) for p in paras if p.strip())
    return Result(body=tidy(body), warnings=warnings)
