"""Emails saved as .eml (RFC 5322), read with the standard library."""

from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

from .common import Attachment, ConversionError, Options, Result, safe_name
from .emailmd import Email, iso, render

MAX_BYTES = 500 * 2**20
CONVERTIBLE = {".docx", ".pdf", ".msg", ".eml", ".txt", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def _addresses(values) -> list[str]:
    out = []
    for name, addr in getaddresses([str(v) for v in values if v]):
        if name and addr:
            out.append(f"{name} <{addr}>")
        elif addr or name:
            out.append(addr or name)
    return out


def _text(part) -> str | None:
    try:
        return part.get_content()
    except (LookupError, UnicodeError, AssertionError):
        payload = part.get_payload(decode=True) or b""
        return payload.decode("utf-8", "replace")


def parse(msg: EmailMessage, attachments: list[Attachment], depth: int = 0) -> Email:
    """One message. Converted attachments are added to `attachments`;
    attached emails are rendered inside this one."""
    e = Email()
    e.subject = str(msg.get("subject", "") or "").strip()
    sender = _addresses([msg.get("from")])
    e.sender = sender[0] if sender else ""
    e.to = _addresses(msg.get_all("to", []))
    e.cc = _addresses(msg.get_all("cc", []))
    e.bcc = _addresses(msg.get_all("bcc", []))
    try:
        e.date = parsedate_to_datetime(str(msg.get("date"))) if msg.get("date") else None
    except (TypeError, ValueError, IndexError):
        e.date = None
    e.message_id = str(msg.get("message-id", "") or "").strip()
    e.in_reply_to = str(msg.get("in-reply-to", "") or "").strip()
    e.thread = str(msg.get("thread-topic", "") or "").strip()

    body = msg.get_body(preferencelist=("plain", "html")) if msg.is_multipart() else msg
    if body is not None and body.get_content_maintype() == "text":
        content = _text(body)
        if body.get_content_subtype() == "html":
            e.body_html = content
        else:
            e.body_text = content
            # Some mailers send a plain part that only says "view in HTML".
            html = msg.get_body(preferencelist=("html",)) if msg.is_multipart() else None
            if html is not None and len((content or "").strip()) < 20:
                e.body_html, e.body_text = _text(html), None

    if msg.is_multipart():
        for part in msg.iter_attachments():
            name = part.get_filename()
            ctype = part.get_content_type()
            if ctype == "message/rfc822" and depth < 3:
                inner = part.get_payload(0) if part.is_multipart() else None
                if isinstance(inner, EmailMessage):
                    e.embedded.append(parse(inner, attachments, depth + 1))
                    e.attachments.append(safe_name(name or (str(inner.get("subject", "")) + ".eml"), "attached email.eml"))
                continue
            if part.get("content-id") and part.get_content_maintype() == "image" and (part.get_content_disposition() or "inline") == "inline":
                continue  # logos and pictures in the body, not attachments
            data = part.get_payload(decode=True)
            if data is None:
                continue
            fname = safe_name(name or "attachment")
            e.attachments.append(fname)
            ext = "." + fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
            if ext in CONVERTIBLE:
                attachments.append(Attachment(fname, data))
            else:
                e.skipped.append(f"{fname} ({ext or 'no extension'} files aren't converted; it stays inside the original email)")
    return e


def convert(path: Path, _opts: Options) -> Result:
    if path.stat().st_size > MAX_BYTES:
        raise ConversionError("This email is larger than 500 MB, which is too large to convert.")
    with open(path, "rb") as f:
        msg = BytesParser(policy=policy.default).parse(f)
    if not msg.keys():
        raise ConversionError("This file doesn't look like an email. Open it on your computer to check it.")
    attachments: list[Attachment] = []
    e = parse(msg, attachments)
    warnings = [] if (e.body_text or e.body_html) else ["This email has no text body."]
    return Result(body=render(e), meta=e.meta(), doc_date=iso(e.date), title=e.subject or None, attachments=attachments, warnings=warnings)
