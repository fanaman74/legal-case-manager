"""Case storage on disk. Each case has its own folder:

    cases/<case-id>/original/<file-id><ext>   uploaded files, byte for byte
    cases/<case-id>/markdown/<file-id>.md     the converted Markdown
    cases/<case-id>/preview/<file-id>/        page images for the viewer

Files are stored under their ID, not their uploaded name, so long paths,
reserved Windows names and odd characters in uploads can't cause trouble; the
uploaded name and folder are kept in the database. Originals are made
read-only once stored and are never changed; everything else is made from
them and can be made again."""

import os
import re
import secrets
import shutil
import stat
from pathlib import Path

_ID = re.compile(r"^[0-9a-f]{32}$")
_EXT = re.compile(r"^(\.[a-z0-9]{1,10})?$")


def _check_id(value: str) -> str:
    if not _ID.match(value):
        raise ValueError("bad id")
    return value


class Storage:
    def __init__(self, root: Path):
        self.root = root

    def case_dir(self, case_id: str) -> Path:
        return self.root / _check_id(case_id)

    def originals(self, case_id: str) -> Path:
        return self.case_dir(case_id) / "original"

    def original(self, case_id: str, file_id: str, ext: str) -> Path:
        if not _EXT.match(ext):
            raise ValueError("bad extension")
        return self.originals(case_id) / f"{_check_id(file_id)}{ext}"

    def markdown(self, case_id: str, file_id: str) -> Path:
        return self.case_dir(case_id) / "markdown" / f"{_check_id(file_id)}.md"

    def previews(self, case_id: str, file_id: str) -> Path:
        return self.case_dir(case_id) / "preview" / _check_id(file_id)

    def preview(self, case_id: str, file_id: str, page: int) -> Path:
        return self.previews(case_id, file_id) / f"p{int(page):04d}.jpg"

    def mailbox(self, case_id: str, mailbox_id: str, ext: str) -> Path:
        return self.case_dir(case_id) / "mailboxes" / f"{_check_id(mailbox_id)}{ext}"

    def mailbox_db(self, case_id: str, mailbox_id: str) -> Path:
        return self.case_dir(case_id) / "mailboxes" / f"{_check_id(mailbox_id)}.db"

    def incoming_mailbox(self, case_id: str) -> Path:
        folder = self.case_dir(case_id) / "mailboxes"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f".incoming-{secrets.token_hex(8)}"

    def work_dir(self, case_id: str) -> Path:
        """A scratch folder for one conversion, on the case's disk."""
        folder = self.case_dir(case_id) / f".work-{secrets.token_hex(8)}"
        folder.mkdir(parents=True)
        return folder

    def write_markdown(self, case_id: str, file_id: str, text: str) -> Path:
        dest = self.markdown(case_id, file_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(f".tmp-{secrets.token_hex(4)}")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, dest)
        return dest

    def replace_previews(self, case_id: str, file_id: str, src: Path | None) -> None:
        """Swap in a new set of page images (or remove them)."""
        dest = self.previews(case_id, file_id)
        if dest.exists():
            old = dest.with_name(f".old-{dest.name}-{secrets.token_hex(4)}")
            os.replace(dest, old)
            shutil.rmtree(old, ignore_errors=True)
        if src is not None and src.is_dir():
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dest)

    def keep_bytes(self, case_id: str, file_id: str, ext: str, data: bytes) -> Path:
        """Store a new original from bytes (an email attachment)."""
        tmp = self.incoming(case_id)
        tmp.write_bytes(data)
        return self.keep(tmp, case_id, file_id, ext)

    def create_case(self, case_id: str) -> None:
        self.originals(case_id).mkdir(parents=True, exist_ok=True)

    def incoming(self, case_id: str) -> Path:
        """A temporary path for an upload in progress, on the same disk as its
        final place so storing it is a rename."""
        folder = self.originals(case_id)
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f".incoming-{secrets.token_hex(8)}"

    def keep(self, tmp: Path, case_id: str, file_id: str, ext: str) -> Path:
        dest = self.original(case_id, file_id, ext)
        os.replace(tmp, dest)
        dest.chmod(stat.S_IREAD)
        return dest

    def trash_case(self, case_id: str) -> None:
        """Move a case's folder aside in one step, so a delete either takes
        the whole folder or nothing (Windows refuses if a file is open)."""
        folder = self.case_dir(case_id)
        if folder.exists():
            os.replace(folder, self.root / f".deleted-{case_id}")

    def empty_trash(self) -> None:
        """Remove folders of deleted cases. Anything still locked is retried
        next time."""
        if not self.root.exists():
            return
        for folder in self.root.glob(".deleted-*"):
            try:
                shutil.rmtree(folder, onexc=_make_writable)
            except OSError:
                pass


def _make_writable(func, path, _exc) -> None:
    """Originals are read-only; clear that so they can be removed."""
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    func(path)
