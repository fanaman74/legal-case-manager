"""Child process for mailboxes, run by the worker:

  python -m app.pst index <mailbox> <database>
      Reads every item into a new search database. Prints "progress N TOTAL"
      lines as it goes and "done N" at the end.
  python -m app.pst export <mailbox> <name> <out-dir> [--no-attachments] <locator>...
      Writes each chosen email as out-dir/N.eml and a manifest.json.

A mailbox that can't be read prints "error <message>" and exits 0; a crash
exits non-zero."""

import json
import sqlite3
import sys
from pathlib import Path

from . import reader
from .db import SCHEMA


def index(src: str, dest: str) -> None:
    f = reader.open_file(src)
    total = reader.count(f)
    print(f"progress 0 {total}", flush=True)
    tmp = Path(dest).with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript(SCHEMA)
    n = 0
    folders: dict[str, int] = {}
    for locator, folder, item in reader.walk(f):
        try:
            m = reader.read(item, locator, folder)
        except Exception as exc:  # one unreadable item never stops the rest
            print(f"skip {locator} {type(exc).__name__}", flush=True)
            continue
        db.execute(
            """INSERT INTO messages (locator, folder, kind, subject, sender, recipients, cc, date, message_id, body, attachments, attachment_names)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (m.locator, m.folder, m.kind, m.subject, m.sender, "; ".join(m.to), "; ".join(m.cc), m.date, m.message_id, m.body,
             json.dumps([{"name": a["name"], "size": a["size"], "embedded": a["embedded"]} for a in m.attachments]), " ".join(a["name"] for a in m.attachments)),
        )
        folders[folder] = folders.get(folder, 0) + 1
        n += 1
        if n % 250 == 0:
            db.commit()
            print(f"progress {n} {total}", flush=True)
    db.executemany("INSERT INTO folders (path, messages) VALUES (?, ?)", sorted(folders.items()))
    db.execute("INSERT INTO messages_fts(messages_fts) VALUES ('optimize')")
    db.commit()
    db.close()
    tmp.replace(dest)
    print(f"done {n}", flush=True)


def export(src: str, name: str, out: str, attachments: bool, locators: list[str]) -> None:
    f = reader.open_file(src)
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for i, loc in enumerate(locators):
        try:
            item = reader.find(f, loc)
            mail = reader.read(item, loc, reader.folder_path(f, loc))
            data = reader.to_eml(item, mail, name, attachments)
        except Exception as exc:
            manifest.append({"locator": loc, "error": type(exc).__name__})
            continue
        (out_dir / f"{i}.eml").write_bytes(data)
        manifest.append({"locator": loc, "file": f"{i}.eml", "subject": mail.subject, "folder": mail.folder, "date": mail.date})
    (out_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def main(argv: list[str]) -> int:
    try:
        if argv[0] == "index":
            index(argv[1], argv[2])
        elif argv[0] == "export":
            rest = argv[4:]
            attachments = "--no-attachments" not in rest
            export(argv[1], argv[2], argv[3], attachments, [r for r in rest if r != "--no-attachments"])
        else:
            return 2
    except reader.MailboxError as exc:
        print(f"error {exc}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
