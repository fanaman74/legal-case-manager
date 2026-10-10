"""Case storage on disk. Each case has its own folder:

    cases/<case-id>/original/<file-id><ext>   uploaded files, byte for byte

Files are stored under their ID, not their uploaded name, so long paths,
reserved Windows names and odd characters in uploads can't cause trouble; the
uploaded name and folder are kept in the database. Originals are made
read-only once stored. Later phases add markdown/ and vectors/ beside
original/."""

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
