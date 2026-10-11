"""Shared by the .eml, .msg and PST converters: one email as Markdown with a
header block, the new message, and earlier quoted messages marked off."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .markdown import escape, tidy

_REPLY_STARTS = [
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}\s*$", re.I),
    re.compile(r"^-{2,}\s*Forwarded message\s*-{2,}\s*$", re.I),
    re.compile(r"^Begin forwarded message:\s*$", re.I),
    re.compile(r"^_{10,}\s*$"),
]
_ON_WROTE = re.compile(r"^(On|Le|Am|El|Op)\b.{4,200}(wrote|a écrit|schrieb|escribió|schreef)\s*:\s*$", re.I)
_HEADER_LINE = re.compile(r"^\**(From|Von|De|Van)\s*:\**\s", re.I)
_HEADER_FOLLOW = re.compile(r"^\**(Sent|Date|Gesendet|Envoyé|Enviado|Verzonden|To|Subject|Cc)\s*:", re.I)
_SUBJECT_PREFIX = re.compile(r"^\s*((re|fw|fwd|aw|wg|tr|sv|vs|rv)\s*(\[\d+\])?\s*:\s*)+", re.I)


@dataclass
class Email:
    subject: str = ""
    sender: str = ""
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    date: datetime | None = None
    message_id: str = ""
    in_reply_to: str = ""
    thread: str = ""
    body_text: str | None = None
    body_html: str | None = None
    attachments: list[str] = field(default_factory=list)  # names, for the header block
    skipped: list[str] = field(default_factory=list)  # attachments not converted, with the reason
    embedded: list["Email"] = field(default_factory=list)  # emails attached inside this one

    def meta(self) -> dict:
        return {
            "from": self.sender,
            "to": self.to,
            "cc": self.cc,
            "date": iso(self.date),
            "subject": self.subject,
            "message_id": self.message_id,
            "thread": self.thread or thread_of(self.subject),
        }


def iso(d: datetime | None) -> str | None:
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.isoformat(timespec="seconds")


def thread_of(subject: str) -> str:
    return _SUBJECT_PREFIX.sub("", subject or "").strip()


def split_quoted(text: str) -> tuple[str, str]:
    """(new message, earlier quoted messages). Recognises the usual reply and
    forward separators from Outlook, Gmail and Apple Mail, and '>' quoting."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for i, line in enumerate(lines):
        s = line.strip()
        if any(p.match(s) for p in _REPLY_STARTS) or _ON_WROTE.match(s):
            return "\n".join(lines[:i]), "\n".join(lines[i:])
        # "On Tue, 5 Mar 2024 at 14:22, Jane Doe <jane@x.com>" + "wrote:" on the next line
        if i + 1 < len(lines) and re.match(r"^(On|Le|Am)\b", s) and lines[i + 1].strip().lower().startswith(("wrote:", "a écrit", "schrieb")):
            return "\n".join(lines[:i]), "\n".join(lines[i:])
        if _HEADER_LINE.match(s) and i > 0:
            follow = [l.strip() for l in lines[i + 1 : i + 5]]
            if sum(bool(_HEADER_FOLLOW.match(f)) for f in follow) >= 2:
                return "\n".join(lines[:i]), "\n".join(lines[i:])
        if s.startswith(">") and i > 0:
            rest = [l for l in lines[i:] if l.strip()]
            if rest and sum(l.lstrip().startswith(">") for l in rest) >= 0.8 * len(rest):
                return "\n".join(lines[:i]), "\n".join(lines[i:])
    return text, ""


def _quote(text: str) -> str:
    out = []
    for line in text.strip("\n").split("\n"):
        # Keep a trailing two-space hard break: it holds header lines apart.
        out.append("> " + line if line.strip() else ">")
    return "\n".join(out)


def _text_to_md(text: str) -> str:
    """Plain text email body: keep line breaks (they matter in signatures and
    addresses) and escape Markdown syntax."""
    lines = [escape(l.rstrip()) for l in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    out = []
    for i, l in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        out.append(l + ("  " if l and nxt else ""))
    return "\n".join(out)


def body_markdown(e: Email) -> tuple[str, str]:
    """(new message, quoted earlier messages) as Markdown."""
    from .html import to_markdown

    if e.body_text and e.body_text.strip():
        new, quoted = split_quoted(e.body_text)
        return _text_to_md(new), _quote(_text_to_md(quoted)) if quoted.strip() else ""
    if e.body_html:
        md = to_markdown(e.body_html)
        new, quoted = split_quoted(md)
        return new, _quote(quoted) if quoted.strip() else ""
    return "", ""


def _names(addrs: list[str]) -> str:
    return "; ".join(escape(a) for a in addrs if a)


def render(e: Email, level: int = 1) -> str:
    h = "#" * level
    parts = [f"{h} {escape(e.subject) or '(no subject)'}", ""]
    rows = [
        ("From", escape(e.sender)),
        ("To", _names(e.to)),
        ("Cc", _names(e.cc)),
        ("Bcc", _names(e.bcc)),
        ("Date", e.date.strftime("%a, %d %b %Y %H:%M %Z").strip() if e.date else ""),
        ("Subject", escape(e.subject)),
        ("Attachments", "; ".join(escape(a) for a in e.attachments)),
    ]
    parts.append("  \n".join(f"**{k}:** {v}" for k, v in rows if v))
    parts += ["", "---", ""]
    new, quoted = body_markdown(e)
    parts.append(new.strip() or "*(This email has no text.)*")
    if quoted:
        parts += ["", f"{h}# Earlier messages in this thread (quoted)", "", quoted]
    if e.skipped:
        parts += ["", f"{h}# Attachments not converted", ""]
        parts += [f"- {escape(s)}" for s in e.skipped]
    for sub in e.embedded:
        parts += ["", f"{h}# Attached email", "", render(sub, min(level + 2, 6))]
    return tidy("\n".join(parts))
