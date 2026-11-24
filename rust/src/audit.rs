//! Audit log: who did what, to which case, and when.
//!
//! Entries are hash-chained: each entry's hash covers its content and the
//! previous entry's hash, so an edited or deleted entry breaks the chain. The
//! hash is computed exactly as the Python version did, so one chain can mix
//! entries from both. Entries are written in the same transaction as the
//! change they describe.

use rusqlite::{Connection, OptionalExtension};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::accounts::User;
use crate::util;

pub const GENESIS: &str = "0000000000000000000000000000000000000000000000000000000000000000";

#[derive(Default)]
pub struct Entry<'a> {
    pub action: &'a str,
    pub user: Option<&'a User>,
    pub username: Option<&'a str>,
    pub ip: &'a str,
    pub outcome: Option<&'a str>,
    pub case_id: Option<&'a str>,
    pub target: &'a str,
    pub detail: &'a str,
}

fn hash(prev: &str, fields: &Value) -> String {
    // Python: json.dumps(list, ensure_ascii=False, separators=(",", ":"))
    let body = serde_json::to_string(fields).unwrap();
    hex::encode(Sha256::digest(format!("{prev}{body}").as_bytes()))
}

/// Append one entry. Call inside a write transaction.
pub fn record(db: &Connection, e: Entry) -> rusqlite::Result<()> {
    let at = util::now();
    let user_id = e.user.map(|u| u.id);
    let username = match (e.username, e.user) {
        (Some(n), _) => n.to_string(),
        (None, Some(u)) => u.username.clone(),
        (None, None) => "anonymous".to_string(),
    };
    let outcome = e.outcome.unwrap_or("ok");
    let fields = json!([at, user_id, username, e.action, outcome, e.case_id, e.target, e.detail, e.ip]);
    let prev: String = db
        .query_row("SELECT hash FROM audit ORDER BY id DESC LIMIT 1", [], |r| r.get(0))
        .optional()?
        .unwrap_or_else(|| GENESIS.to_string());
    let h = hash(&prev, &fields);
    db.execute(
        "INSERT INTO audit (at, user_id, username, action, outcome, case_id, target, detail, ip, prev_hash, hash)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rusqlite::params![at, user_id, username, e.action, outcome, e.case_id, e.target, e.detail, e.ip, prev, h],
    )?;
    Ok(())
}

/// Walk the chain. Returns (entries checked, id of the first bad entry).
pub fn verify(db: &Connection) -> rusqlite::Result<(i64, Option<i64>)> {
    let mut stmt = db.prepare(
        "SELECT id, at, user_id, username, action, outcome, case_id, target, detail, ip, prev_hash, hash FROM audit ORDER BY id",
    )?;
    let mut rows = stmt.query([])?;
    let mut prev = GENESIS.to_string();
    let mut count = 0;
    while let Some(r) = rows.next()? {
        count += 1;
        let id: i64 = r.get(0)?;
        let fields = json!([
            r.get::<_, String>(1)?,
            r.get::<_, Option<i64>>(2)?,
            r.get::<_, String>(3)?,
            r.get::<_, String>(4)?,
            r.get::<_, String>(5)?,
            r.get::<_, Option<String>>(6)?,
            r.get::<_, String>(7)?,
            r.get::<_, String>(8)?,
            r.get::<_, String>(9)?,
        ]);
        let prev_hash: String = r.get(10)?;
        let h: String = r.get(11)?;
        if prev_hash != prev || hash(&prev, &fields) != h {
            return Ok((count, Some(id)));
        }
        prev = h;
    }
    Ok((count, None))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Same input as a Python entry; the hash must match what
    /// app/audit.py computes, so mixed chains verify.
    #[test]
    fn hash_matches_python() {
        let fields = json!(["2026-10-10T12:00:00.000Z", 1, "fred", "case.create", "ok", "abc", "Smith v Jönes", "line\nbreak\u{1}", "127.0.0.1"]);
        // Computed with app/audit.py's formula in Python.
        assert_eq!(hash(GENESIS, &fields), "ab083657cf2a06dc1ad72707ea06683db7780015f85886cf10c7691980b5b3c2");
    }
}
