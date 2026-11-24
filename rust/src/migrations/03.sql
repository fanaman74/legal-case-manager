ALTER TABLE files ADD COLUMN index_state TEXT NOT NULL DEFAULT '';
    ALTER TABLE files ADD COLUMN passages INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE files ADD COLUMN indexed_at TEXT;
    CREATE INDEX files_case_index ON files(case_id, index_state);
    INSERT INTO jobs (kind, case_id, file_id, created_at)
        SELECT 'index', case_id, id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM files WHERE status IN ('converted', 'needs_ocr') ORDER BY added_at;
