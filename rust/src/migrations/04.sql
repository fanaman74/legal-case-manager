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
