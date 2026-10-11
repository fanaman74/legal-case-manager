"""A mailbox's search database: one row per item, with a keyword index.
Written once by the child process; read-only afterwards."""

SCHEMA = """
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    locator TEXT NOT NULL,
    folder TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    sender TEXT NOT NULL,
    recipients TEXT NOT NULL,
    cc TEXT NOT NULL,
    date TEXT,
    message_id TEXT NOT NULL,
    body TEXT NOT NULL,
    attachments TEXT NOT NULL,
    attachment_names TEXT NOT NULL
);
CREATE INDEX messages_date ON messages(date);
CREATE INDEX messages_folder ON messages(folder);
CREATE TABLE folders (path TEXT PRIMARY KEY, messages INTEGER NOT NULL);
CREATE VIRTUAL TABLE messages_fts USING fts5(
    subject, sender, recipients, body, attachment_names,
    content='messages', content_rowid='id', tokenize='porter unicode61 remove_diacritics 2'
);
CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, subject, sender, recipients, body, attachment_names)
    VALUES (new.id, new.subject, new.sender, new.recipients || ' ' || new.cc, new.body, new.attachment_names);
END;
"""
