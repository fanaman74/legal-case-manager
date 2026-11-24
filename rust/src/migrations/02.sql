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
