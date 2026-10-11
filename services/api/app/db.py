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
    # 2: conversion to Markdown and the background job queue.
    """
    ALTER TABLE files ADD COLUMN parent_id TEXT REFERENCES files(id) ON DELETE SET NULL;
    ALTER TABLE files ADD COLUMN page_count INTEGER;
    ALTER TABLE files ADD COLUMN ocr INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE files ADD COLUMN warnings TEXT NOT NULL DEFAULT '[]';
    ALTER TABLE files ADD COLUMN error TEXT NOT NULL DEFAULT '';
    ALTER TABLE files ADD COLUMN scanned_pages TEXT NOT NULL DEFAULT '[]';
    ALTER TABLE files ADD COLUMN meta TEXT NOT NULL DEFAULT '{}';
    ALTER TABLE files ADD COLUMN doc_date TEXT;
    ALTER TABLE files ADD COLUMN title TEXT;
    ALTER TABLE files ADD COLUMN converted_at TEXT;
    ALTER TABLE files ADD COLUMN markdown_sha256 TEXT;
    ALTER TABLE files ADD COLUMN previews INTEGER NOT NULL DEFAULT 0;
    CREATE INDEX files_parent ON files(parent_id);
    CREATE INDEX files_case_status ON files(case_id, status);
    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,
        case_id TEXT REFERENCES cases(id) ON DELETE CASCADE,
        file_id TEXT,
        state TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'running', 'done', 'failed', 'cancelled')),
        options TEXT NOT NULL DEFAULT '{}',
        attempts INTEGER NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '',
        done INTEGER NOT NULL DEFAULT 0,
        total INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL
    );
    CREATE INDEX jobs_queue ON jobs(state, kind, id);
    CREATE INDEX jobs_case ON jobs(case_id, state);
    CREATE INDEX jobs_file ON jobs(file_id, kind, state);
    -- Files uploaded before conversion existed are converted now.
    UPDATE files SET status = 'queued' WHERE status = 'stored';
    INSERT INTO jobs (kind, case_id, file_id, created_at)
        SELECT 'convert', case_id, id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM files WHERE status = 'queued' ORDER BY added_at;
    """,
    # 3: search. index_state: '' not yet, 'indexed', 'keyword_only' (the
    # local model wasn't available; meaning search catches up later), 'failed'.
    """
    ALTER TABLE files ADD COLUMN index_state TEXT NOT NULL DEFAULT '';
    ALTER TABLE files ADD COLUMN passages INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE files ADD COLUMN indexed_at TEXT;
    CREATE INDEX files_case_index ON files(case_id, index_state);
    INSERT INTO jobs (kind, case_id, file_id, created_at)
        SELECT 'index', case_id, id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM files WHERE status IN ('converted', 'needs_ocr') ORDER BY added_at;
    """,
    # 4: Outlook mailboxes (PST/OST), each belonging to one case.
    """
    CREATE TABLE mailboxes (
        id TEXT PRIMARY KEY,
        case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        ext TEXT NOT NULL,
        size INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'indexing', 'ready', 'failed')),
        error TEXT NOT NULL DEFAULT '',
        emails INTEGER NOT NULL DEFAULT 0,
        done INTEGER NOT NULL DEFAULT 0,
        total INTEGER NOT NULL DEFAULT 0,
        added_at TEXT NOT NULL,
        added_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        indexed_at TEXT
    );
    CREATE INDEX mailboxes_case ON mailboxes(case_id);
    CREATE TABLE mailbox_added (
        mailbox_id TEXT NOT NULL REFERENCES mailboxes(id) ON DELETE CASCADE,
        message INTEGER NOT NULL,
        file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        PRIMARY KEY (mailbox_id, message)
    );
    """,
    # 5: AI services (Ask and Review). API keys are stored encrypted
    # (ai/secrets.py). ai_calls keeps each request: what was sent, to whom,
    # and the answer, so reviews can be read again with their sources.
    """
    CREATE TABLE ai_providers (
        id TEXT PRIMARY KEY,
        enabled INTEGER NOT NULL DEFAULT 0,
        base_url TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        api_key BLOB,
        key_hint TEXT NOT NULL DEFAULT '',
        tested_at TEXT,
        test_ok INTEGER,
        updated_at TEXT,
        updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL
    );
    CREATE TABLE ai_settings (
        name TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE ai_calls (
        id TEXT PRIMARY KEY,
        case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
        user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        username TEXT NOT NULL,
        kind TEXT NOT NULL,
        scope TEXT NOT NULL DEFAULT '{}',
        question TEXT NOT NULL DEFAULT '',
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        external INTEGER NOT NULL,
        sources TEXT NOT NULL DEFAULT '[]',
        docs TEXT NOT NULL DEFAULT '[]',
        note TEXT NOT NULL DEFAULT '',
        state TEXT NOT NULL CHECK (state IN ('prepared', 'sending', 'done', 'failed', 'cancelled')),
        answer TEXT NOT NULL DEFAULT '',
        result TEXT NOT NULL DEFAULT '{}',
        error TEXT NOT NULL DEFAULT '',
        served_model TEXT,
        input_tokens INTEGER,
        output_tokens INTEGER,
        created_at TEXT NOT NULL,
        sent_at TEXT,
        finished_at TEXT
    );
    CREATE INDEX ai_calls_case ON ai_calls(case_id, kind, created_at);
    -- Ollama ships with the app and runs on this computer, so it is on from the
    -- start; it needs a chat model before it can be used.
    INSERT INTO ai_providers (id, enabled) VALUES ('ollama', 1);
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
