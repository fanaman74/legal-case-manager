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
