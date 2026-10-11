"""Outlook messages saved as .msg.

A .msg file is an OLE compound file holding MAPI properties ([MS-OXMSG]).
This reads the properties that matter (sender, recipients, dates, subject,
body, attachments) with olefile. Bodies stored only as compressed RTF are
decompressed and unwrapped (see rtf.py)."""

import struct
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import HeaderParser
from email.utils import getaddresses
from pathlib import Path

import compressed_rtf
import olefile

from . import rtf
from .common import Attachment, ConversionError, Options, Result, safe_name
from .emailmd import Email, iso, render
from .eml import CONVERTIBLE

PT_UNICODE, PT_STRING8, PT_BINARY, PT_LONG, PT_SYSTIME, PT_BOOLEAN = 0x001F, 0x001E, 0x0102, 0x0003, 0x0040, 0x000B

SUBJECT, SENDER_NAME, SENDER_EMAIL, SENDER_SMTP = 0x0037, 0x0C1A, 0x0C1F, 0x5D01
DISPLAY_TO, DISPLAY_CC, DISPLAY_BCC = 0x0E04, 0x0E03, 0x0E02
BODY, HTML, RTF_COMPRESSED = 0x1000, 0x1013, 0x1009
MESSAGE_ID, IN_REPLY_TO, HEADERS, TOPIC = 0x1035, 0x1042, 0x007D, 0x0070
SUBMIT_TIME, DELIVERY_TIME, CODEPAGE, INTERNET_CPID = 0x0039, 0x0E06, 0x3FFD, 0x3FDE
RECIP_NAME, RECIP_EMAIL, RECIP_SMTP, RECIP_TYPE = 0x3001, 0x3003, 0x39FE, 0x0C15
ATTACH_LONG_NAME, ATTACH_NAME, ATTACH_DISPLAY, ATTACH_DATA, ATTACH_METHOD, ATTACH_CID, ATTACH_HIDDEN = 0x3707, 0x3704, 0x3001, 0x3701, 0x3705, 0x3712, 0x7FFE
ATTACH_EMBEDDED = 5


def _filetime(value: int) -> datetime | None:
    if not value:
        return None
    try:
        return datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=value // 10)
    except OverflowError:
        return None


class _Store:
    """The properties of one message, recipient or attachment: a storage
    path inside the compound file."""

    def __init__(self, ole: olefile.OleFileIO, prefix: list[str], header: int, codepage: str = "cp1252"):
        self.ole, self.prefix, self.codepage = ole, prefix, codepage
        self.fixed = self._fixed(header)

    def _stream(self, name: str) -> bytes | None:
        path = self.prefix + [name]
        if self.ole.exists("/".join(path)) and self.ole.get_type("/".join(path)) == olefile.STGTY_STREAM:
            return self.ole.openstream(path).read()
        return None

    def _fixed(self, header: int) -> dict[int, tuple[int, bytes]]:
        """Fixed-size properties (numbers, dates, flags) from the properties
        stream: 16 bytes each after a header."""
        data = self._stream("__properties_version1.0") or b""
        out = {}
        for off in range(header, len(data) - 15, 16):
            tag, _flags = struct.unpack_from("<II", data, off)
            out[tag >> 16] = (tag & 0xFFFF, data[off + 8 : off + 16])
        return out

    def string(self, prop: int) -> str:
        raw = self._stream(f"__substg1.0_{prop:04X}{PT_UNICODE:04X}")
        if raw is not None:
            return raw.decode("utf-16-le", "replace").rstrip("\x00")
        raw = self._stream(f"__substg1.0_{prop:04X}{PT_STRING8:04X}")
        if raw is not None:
            return raw.decode(self.codepage, "replace").rstrip("\x00")
        return ""

    def binary(self, prop: int) -> bytes | None:
        return self._stream(f"__substg1.0_{prop:04X}{PT_BINARY:04X}")

    def number(self, prop: int) -> int | None:
        got = self.fixed.get(prop)
        if not got:
            return None
        kind, value = got
        if kind in (PT_LONG, PT_BOOLEAN):
            return struct.unpack_from("<i", value)[0]
        if kind == PT_SYSTIME:
            return struct.unpack_from("<Q", value)[0]
        return None

    def date(self, prop: int) -> datetime | None:
        got = self.fixed.get(prop)
        if not got or got[0] != PT_SYSTIME:
            return None
        return _filetime(struct.unpack_from("<Q", got[1])[0])

    def children(self, kind: str) -> list[list[str]]:
        """Storages for recipients (__recip_) or attachments (__attach_)."""
        found = set()
        depth = len(self.prefix)
        for entry in self.ole.listdir(streams=True, storages=True):
            if len(entry) > depth and entry[:depth] == self.prefix and entry[depth].startswith(kind):
                found.add(entry[depth])
        return [self.prefix + [name] for name in sorted(found)]


def _codepage(store: _Store) -> str:
    for prop in (INTERNET_CPID, CODEPAGE):
        cp = store.number(prop)
        if cp:
            name = {65001: "utf-8", 1200: "utf-16-le", 20127: "ascii", 28591: "latin-1"}.get(cp, f"cp{cp}")
            try:
                "".encode(name)
                return name
            except LookupError:
                continue
    return "cp1252"


def _addr(name: str, address: str) -> str:
    if name and address and name != address:
        return f"{name} <{address}>"
    return address or name


def _read(ole: olefile.OleFileIO, prefix: list[str], header: int, attachments: list[Attachment], depth: int) -> Email:
    store = _Store(ole, prefix, header)
    store.codepage = _codepage(store)
    e = Email()
    e.subject = store.string(SUBJECT).strip()
    e.date = store.date(SUBMIT_TIME) or store.date(DELIVERY_TIME)
    e.message_id = store.string(MESSAGE_ID).strip()
    e.in_reply_to = store.string(IN_REPLY_TO).strip()
    e.thread = store.string(TOPIC).strip()

    sender_addr = store.string(SENDER_SMTP) or store.string(SENDER_EMAIL)
    if sender_addr.startswith("/O=") or sender_addr.startswith("/o="):
        sender_addr = ""  # an Exchange internal address, not useful to a reader
    headers = store.string(HEADERS)
    parsed = HeaderParser(policy=policy.default).parsestr(headers) if headers else None
    if parsed is not None and parsed.get("from") and not sender_addr:
        e.sender = str(parsed.get("from"))
    else:
        e.sender = _addr(store.string(SENDER_NAME), sender_addr)
    if parsed is not None and not e.message_id:
        e.message_id = str(parsed.get("message-id", "") or "").strip()

    for rpath in store.children("__recip_version1.0_"):
        r = _Store(ole, rpath, 8, store.codepage)
        addr = r.string(RECIP_SMTP) or r.string(RECIP_EMAIL)
        if addr.lower().startswith("/o="):
            addr = ""
        value = _addr(r.string(RECIP_NAME), addr)
        kind = r.number(RECIP_TYPE) or 1
        if value:
            (e.to if kind == 1 else e.cc if kind == 2 else e.bcc).append(value)
    if not e.to and not e.cc:
        # Messages without recipient storages still have the display lines.
        e.to = [s.strip() for s in store.string(DISPLAY_TO).split(";") if s.strip()]
        e.cc = [s.strip() for s in store.string(DISPLAY_CC).split(";") if s.strip()]
        if parsed is not None and not e.to:
            e.to = [f"{n} <{a}>" if n else a for n, a in getaddresses([str(v) for v in parsed.get_all("to", [])])]

    body = store.string(BODY)
    html = store.binary(HTML) or store.string(HTML).encode("utf-8")
    if body.strip():
        e.body_text = body
    elif html:
        e.body_html = html.decode("utf-8" if html[:3] == b"\xef\xbb\xbf" else store.codepage, "replace")
    else:
        compressed = store.binary(RTF_COMPRESSED)
        if compressed:
            try:
                raw = compressed_rtf.decompress(compressed).decode("latin-1")
                kind, content = rtf.decode(raw)
                if kind == "html":
                    e.body_html = content
                else:
                    e.body_text = content
            except Exception:  # damaged RTF: carry on without a body
                pass

    for apath in store.children("__attach_version1.0_"):
        a = _Store(ole, apath, 8, store.codepage)
        name = safe_name(a.string(ATTACH_LONG_NAME) or a.string(ATTACH_NAME) or a.string(ATTACH_DISPLAY) or "attachment")
        method = a.number(ATTACH_METHOD)
        if method == ATTACH_EMBEDDED or ole.exists("/".join(apath + ["__substg1.0_3701000D"])):
            if depth < 3:
                inner = _read(ole, apath + ["__substg1.0_3701000D"], 24, attachments, depth + 1)
                e.embedded.append(inner)
                e.attachments.append(name if name != "attachment" else (inner.subject or "attached email") + ".msg")
            continue
        data = a.binary(ATTACH_DATA)
        if data is None:
            continue
        if a.string(ATTACH_CID) and name.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp")) and a.number(ATTACH_HIDDEN):
            continue  # a picture in the body (a logo), not an attachment
        e.attachments.append(name)
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext in CONVERTIBLE:
            attachments.append(Attachment(name, data))
        else:
            e.skipped.append(f"{name} ({ext or 'no extension'} files aren't converted; it stays inside the original email)")
    return e


def convert(path: Path, _opts: Options) -> Result:
    if not olefile.isOleFile(str(path)):
        raise ConversionError("This file isn't an Outlook message, even though it ends in .msg. Open it on your computer to check it.")
    try:
        with olefile.OleFileIO(str(path)) as ole:
            attachments: list[Attachment] = []
            e = _read(ole, [], 32, attachments, 0)
    except (OSError, struct.error, ValueError, IndexError) as exc:
        raise ConversionError("This Outlook message is damaged and can't be read. Open it in Outlook and save it again.") from exc
    warnings = [] if (e.body_text or e.body_html) else ["This email has no text body."]
    return Result(body=render(e), meta=e.meta(), doc_date=iso(e.date), title=e.subject or None, attachments=attachments, warnings=warnings)
