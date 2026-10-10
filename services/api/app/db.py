"""SQLite database: users, sessions, cases, files and the audit log.

One file, app.db, in the data folder. Schema changes are numbered steps in
MIGRATIONS; the database records how many have run (PRAGMA user_version), so
an upgrade only runs the new ones."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

MIGRATIONS = [
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        display_name TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('admin', 'editor', 'readonly')),
        password_hash TEXT NOT NULL,
        must_change_password INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        provisioned INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        last_login_at TEXT
    );
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        csrf TEXT NOT NULL,
        created_at REAL NOT NULL,
        last_seen REAL NOT NULL
    );
    CREATE INDEX sessions_user ON sessions(user_id);
    CREATE TABLE cases (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        reference TEXT NOT NULL DEFAULT '',
        client TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
        created_at TEXT NOT NULL,
        created_by INTEGER REFERENCES users(id)
    );
    CREATE TABLE case_members (
        case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        PRIMARY KEY (case_id, user_id)
    );
    CREATE INDEX case_members_user ON case_members(user_id);
    CREATE TABLE files (
        id TEXT PRIMARY KEY,
        case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        folder TEXT NOT NULL DEFAULT '',
        ext TEXT NOT NULL,
        source_type TEXT NOT NULL,
        size INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'stored',
        tags TEXT NOT NULL DEFAULT '[]',
        added_at TEXT NOT NULL,
        added_by INTEGER REFERENCES users(id),
        UNIQUE (case_id, sha256)
    );
    CREATE INDEX files_case_folder ON files(case_id, folder);
    CREATE TABLE audit (
        id INTEGER PRIMARY KEY,
        at TEXT NOT NULL,
        user_id INTEGER,
        username TEXT NOT NULL,
        action TEXT NOT NULL,
        outcome TEXT NOT NULL,
        case_id TEXT,
        target TEXT NOT NULL DEFAULT '',
        detail TEXT NOT NULL DEFAULT '',
        ip TEXT NOT NULL DEFAULT '',
        prev_hash TEXT NOT NULL,
        hash TEXT NOT NULL
    );
    CREATE INDEX audit_case ON audit(case_id, id);
    """,
]


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            self._migrate(db)

    def _migrate(self, db: sqlite3.Connection) -> None:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
            db.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {i};\nCOMMIT;")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA busy_timeout = 30000")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        """A write transaction that commits on success and rolls back on error.
        BEGIN IMMEDIATE takes the write lock up front, so writers queue here
        and audit entries chain in order."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.execute("ROLLBACK")
                raise
            db.execute("COMMIT")
