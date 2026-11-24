//! Splitting a converter's messages into notes and warnings.
//!
//! A note tells you something about the document that nobody needs to act on
//! ("this document has tracked changes"). A warning means some of the content
//! may be missing from the Markdown. The file list flags only warnings.

use rusqlite::Transaction;

const NOTE_MARKERS: &[&str] = &[
    "read with OCR",
    "with no text. Run OCR",
    "read with OCR by itself",
    "scan with no text layer",
    "tracked changes",
    "has comments",
    "page positions",
];

pub fn is_note(message: &str) -> bool {
    NOTE_MARKERS.iter().any(|m| message.contains(m))
}

/// (warnings, notes)
pub fn split(messages: Vec<String>) -> (Vec<String>, Vec<String>) {
    messages.into_iter().partition(|m| !is_note(m))
}

/// Database migration 8: move the notes out of the warnings of files
/// converted before notes existed.
pub fn migrate(tx: &Transaction) -> rusqlite::Result<()> {
    let rows: Vec<(String, String)> = {
        let mut stmt = tx.prepare("SELECT id, warnings FROM files WHERE warnings != '[]'")?;
        let it = stmt.query_map([], |r| Ok((r.get(0)?, r.get::<_, Option<String>>(1)?.unwrap_or_default())))?;
        it.collect::<rusqlite::Result<_>>()?
    };
    for (id, warnings) in rows {
        let list: Vec<String> = serde_json::from_str(if warnings.is_empty() { "[]" } else { &warnings }).unwrap_or_default();
        let (warnings, found) = split(list);
        if !found.is_empty() {
            tx.execute(
                "UPDATE files SET warnings = ?, notes = ? WHERE id = ?",
                (crate::util::py_json(&warnings), crate::util::py_json(&found), id),
            )?;
        }
    }
    Ok(())
}
