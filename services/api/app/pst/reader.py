"""Read an Outlook data file with libpff (pypff)."""

import email.utils
import struct
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import timezone
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.parser import HeaderParser
from email.policy import compat32

# MAPI property tags.
MESSAGE_CLASS = 0x001A
DISPLAY_NAME = 0x3001
EMAIL_ADDRESS = 0x3003
SMTP_ADDRESS = 0x39FE
RECIPIENT_TYPE = 0x0C15
SENDER_EMAIL = 0x0C1F
SENDER_SMTP = 0x5D01
INTERNET_MESSAGE_ID = 0x1035
ATTACH_METHOD = 0x3705
ATTACH_LONG_FILENAME = 0x3707
ATTACH_FILENAME = 0x3704
EMBEDDED_MESSAGE = 5

MAX_BODY = 200_000  # characters kept for search and preview


class MailboxError(Exception):
    """A mailbox that can't be read, with a message for people."""


@dataclass
class Mail:
    locator: str  # where it is in the file: folder indices and message index
    folder: str
    kind: str = "email"
    subject: str = ""
    sender: str = ""
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    date: str | None = None
    message_id: str = ""
    body: str = ""
    html: str = ""
    attachments: list[dict] = field(default_factory=list)  # {name, size, embedded}


def open_file(path: str):
    import pypff

    f = pypff.file()
    try:
        f.open(path)
    except OSError as exc:
        raise MailboxError("This mailbox can't be opened. It may be damaged, password-protected, or not an Outlook data file (.pst or .ost).") from exc
    return f


def _entries(item) -> dict:
    out = {}
    try:
        sets = item.record_sets
    except OSError:
        return out
    for rs in sets:
        for e in rs.entries:
            out.setdefault(e.entry_type, e)
        break
    return out


def _str(entries: dict, tag: int) -> str:
    e = entries.get(tag)
    if e is None:
        return ""
    try:
        return (e.data_as_string or "").strip("\x00").strip()
    except (OSError, UnicodeDecodeError, ValueError):
        return ""


def _int(entries: dict, tag: int) -> int | None:
    e = entries.get(tag)
    if e is None or not e.data or len(e.data) < 4:
        return None
    return struct.unpack("<i", e.data[:4])[0]


def _clean(s: str | None) -> str:
    return " ".join((s or "").replace("\x00", "").split())


def _decoded(value) -> str:
    try:
        return _clean(str(make_header(decode_header(str(value)))))
    except (ValueError, LookupError, UnicodeError):
        return _clean(str(value))


def _folder_name(folder) -> str:
    try:
        return _clean(folder.name) or "Folder"
    except OSError:
        return "Folder"


def walk(f) -> Iterator[tuple[str, str, object]]:
    """(locator, folder path, item) for every item, folder by folder."""

    def visit(folder, indices: list[int], names: list[str]):
        try:
            count = folder.number_of_sub_messages
        except OSError:
            count = 0
        shown = "/".join(n for i, n in enumerate(names) if not (i == 0 and n.lower().startswith(("top of ", "root - "))))
        for m in range(count):
            try:
                item = folder.get_sub_message(m)
            except OSError:
                continue
            yield f"{'.'.join(map(str, indices))}:{m}", shown, item
        try:
            subs = folder.number_of_sub_folders
        except OSError:
            subs = 0
        for j in range(subs):
            try:
                sub = folder.get_sub_folder(j)
            except OSError:
                continue
            yield from visit(sub, indices + [j], names + [_folder_name(sub)])

    yield from visit(f.root_folder, [], [])


def count(f) -> int:
    def visit(folder) -> int:
        try:
            n = folder.number_of_sub_messages
            for j in range(folder.number_of_sub_folders):
                n += visit(folder.get_sub_folder(j))
            return n
        except OSError:
            return 0

    return visit(f.root_folder)


def find(f, locator: str):
    path, _, m = locator.partition(":")
    folder = f.root_folder
    for j in filter(None, path.split(".")):
        folder = folder.get_sub_folder(int(j))
    return folder.get_sub_message(int(m))


def folder_path(f, locator: str) -> str:
    names = []
    folder = f.root_folder
    for j in filter(None, locator.partition(":")[0].split(".")):
        folder = folder.get_sub_folder(int(j))
        names.append(_folder_name(folder))
    return "/".join(n for i, n in enumerate(names) if not (i == 0 and n.lower().startswith(("top of ", "root - "))))


def _kind(cls: str) -> str:
    cls = cls.upper()
    if cls.startswith("IPM.SCHEDULE"):
        return "meeting"
    if cls.startswith("IPM.APPOINTMENT"):
        return "appointment"
    if cls.startswith("IPM.CONTACT") or cls.startswith("IPM.DISTLIST"):
        return "contact"
    if cls.startswith("IPM.TASK"):
        return "task"
    if cls.startswith("IPM.STICKYNOTE"):
        return "note"
    return "email"


def _text(raw) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        for enc in ("utf-8", "cp1252"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", "replace")
    return str(raw)


def read(item, locator: str, folder: str) -> Mail:
    entries = _entries(item)
    mail = Mail(locator=locator, folder=folder, kind=_kind(_str(entries, MESSAGE_CLASS)))
    try:
        headers = item.transport_headers or ""
    except OSError:
        headers = ""
    h = HeaderParser(policy=compat32).parsestr(headers) if headers.strip() else None

    def header(name: str) -> str:
        v = h.get(name) if h is not None else None
        return _decoded(v) if v else ""

    try:
        mail.subject = _clean(item.subject) or header("subject")
    except OSError:
        mail.subject = header("subject")
    mail.sender = next(iter(_addresses(header("from"))), "")
    if not mail.sender:
        name, addr = _clean(_safe(item, "sender_name")), _str(entries, SENDER_SMTP) or _str(entries, SENDER_EMAIL)
        mail.sender = f"{name} <{addr}>" if name and addr and "@" in addr and addr.lower() not in name.lower() else name or addr
    mail.to = _addresses(header("to"))
    mail.cc = _addresses(header("cc"))
    if not mail.to and not mail.cc:
        mail.to, mail.cc = _recipients(item)
    mail.message_id = header("message-id") or _str(entries, INTERNET_MESSAGE_ID)
    mail.date = _date(header("date"), item)

    body = _text(_safe(item, "plain_text_body"))
    html = _text(_safe(item, "html_body"))
    if not body.strip() and html.strip():
        body = html_text(html)
    if not body.strip():
        rtf = _safe(item, "rtf_body")
        if rtf:
            from ..convert import rtf as rtfmod

            kind, content = rtfmod.decode(_text(rtf))
            body = html_text_from(kind, content)
    mail.body = body.replace("\r\n", "\n")[:MAX_BODY]
    mail.html = html[:MAX_BODY * 5]

    try:
        n = item.number_of_attachments
    except OSError:
        n = 0
    for i in range(n):
        try:
            a = item.get_attachment(i)
        except OSError:
            continue
        ae = _entries(a)
        name = _clean(_str(ae, ATTACH_LONG_FILENAME) or _str(ae, ATTACH_FILENAME) or _str(ae, DISPLAY_NAME)) or f"attachment {i + 1}"
        embedded = _int(ae, ATTACH_METHOD) == EMBEDDED_MESSAGE
        try:
            size = a.size or 0
        except OSError:
            size = 0
        mail.attachments.append({"name": name, "size": size, "embedded": embedded, "index": i})
    return mail


def html_text(html: str) -> str:
    from ..convert.html import to_markdown
    from ..index.chunk import plain

    return plain(to_markdown(html))


def html_text_from(kind: str, content: str) -> str:
    return html_text(content) if kind == "html" else content


def _safe(item, attr: str):
    try:
        return getattr(item, attr)
    except (OSError, UnicodeDecodeError):
        return None


def _addresses(value: str) -> list[str]:
    if not value:
        return []
    out = []
    for name, addr in email.utils.getaddresses([value]):
        name, addr = _clean(name), _clean(addr)
        if addr.startswith("/O=") or addr.startswith("/o="):  # an Exchange address: the name says more
            addr = ""
        out.append(f"{name} <{addr}>" if name and addr and "@" in addr else name or addr)
    return [a for a in out if a]


def _recipients(item) -> tuple[list[str], list[str]]:
    to, cc = [], []
    try:
        recips = item.recipients
        sets = recips.record_sets if recips is not None else []
    except OSError:
        return to, cc
    for rs in sets:
        e = {x.entry_type: x for x in rs.entries}
        name = _str(e, DISPLAY_NAME)
        addr = _str(e, SMTP_ADDRESS) or _str(e, EMAIL_ADDRESS)
        if addr.upper().startswith("/O="):
            addr = ""
        shown = f"{name} <{addr}>" if name and addr and "@" in addr and addr.lower() != name.lower() else name or addr
        kind = _int(e, RECIPIENT_TYPE)
        if shown:
            (cc if kind == 2 else to if kind in (1, None) else []).append(shown)
    return to, cc


def _date(value: str, item) -> str | None:
    if value:
        try:
            d = email.utils.parsedate_to_datetime(value.split(" (")[0])
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d.isoformat()
        except (TypeError, ValueError, IndexError):
            pass
    for attr in ("delivery_time", "client_submit_time", "creation_time"):
        d = _safe(item, attr)
        if d is not None and d.year > 1601:
            return d.replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
    return None


def to_eml(item, mail: Mail, mailbox_name: str, include_attachments: bool) -> bytes:
    """The email as an .eml file, which the case converts like any other
    email (attachments become linked files)."""
    m = EmailMessage()
    for k, v in (("From", mail.sender), ("To", ", ".join(mail.to)), ("Cc", ", ".join(mail.cc)), ("Subject", mail.subject), ("Message-ID", mail.message_id)):
        if v:
            m[k] = v.replace("\r", " ").replace("\n", " ")
    if mail.date:
        from datetime import datetime

        m["Date"] = email.utils.format_datetime(datetime.fromisoformat(mail.date))
    m["X-Case-File-Source"] = f"{mailbox_name} / {mail.folder}".replace("\r", " ").replace("\n", " ")[:900]
    skipped = []
    body = mail.body or ""
    if not include_attachments and mail.attachments:
        skipped = [a["name"] for a in mail.attachments]
    elif mail.attachments:
        skipped = [a["name"] for a in mail.attachments if a["embedded"]]
    if skipped:
        body += "\n\n[Attachments left in the mailbox: " + "; ".join(skipped) + "]\n"
    m.set_content(body or " ")
    if mail.html.strip():
        m.add_alternative(mail.html, subtype="html")
    if include_attachments:
        for a in mail.attachments:
            if a["embedded"]:
                continue
            try:
                att = item.get_attachment(a["index"])
                data = att.read_buffer(att.size) if att.size else b""
            except OSError:
                continue
            m.add_attachment(data, maintype="application", subtype="octet-stream", filename=a["name"])
    return m.as_bytes()
