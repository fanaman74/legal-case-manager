ALTER TABLE files ADD COLUMN near_dup_of TEXT REFERENCES files(id) ON DELETE SET NULL;
    ALTER TABLE files ADD COLUMN near_dup_score REAL;
    CREATE TABLE near_sigs (
        file_id TEXT PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
        case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
        sig BLOB
    );
    CREATE TABLE near_bands (
        case_id TEXT NOT NULL,
        band INTEGER NOT NULL,
        hash INTEGER NOT NULL,
        file_id TEXT NOT NULL REFERENCES near_sigs(file_id) ON DELETE CASCADE
    );
    CREATE INDEX near_bands_lookup ON near_bands(case_id, band, hash);
    CREATE INDEX near_bands_file ON near_bands(file_id);
    CREATE TABLE exports (
        id TEXT PRIMARY KEY,
        case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
        state TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'building', 'ready', 'failed', 'expired')),
        error TEXT NOT NULL DEFAULT '',
        size INTEGER,
        sha256 TEXT,
        files INTEGER,
        created_at TEXT NOT NULL,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        username TEXT NOT NULL,
        finished_at TEXT
    );
    CREATE INDEX exports_case ON exports(case_id, created_at);
    -- Look for near-duplicates among files converted before this version.
    INSERT INTO jobs (kind, case_id, created_at)
        SELECT DISTINCT 'dups', case_id, strftime('%Y-%m-%dT%H:%M:%fZ', 'now') FROM files WHERE status IN ('converted', 'needs_ocr');
