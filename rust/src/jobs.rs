//! The background job queue: a table in app.db, so there is no separate
//! queue service. The web app adds jobs; the worker takes them.

use rusqlite::{Connection, OptionalExtension};
use serde_json::{json, Map, Value};

use crate::accounts::User;
use crate::util;

/// Add a job, unless the same job is already waiting. Call inside a
/// transaction. Returns the job id, or None if one was already queued.
pub fn enqueue(db: &Connection, kind: &str, case_id: Option<&str>, file_id: Option<&str>, options: Option<Value>, user: Option<&User>) -> rusqlite::Result<Option<i64>> {
    if let Some(fid) = file_id {
        let exists: Option<i64> = db
            .query_row("SELECT id FROM jobs WHERE kind = ? AND file_id = ? AND state = 'queued'", (kind, fid), |r| r.get(0))
            .optional()?;
        if let Some(id) = exists {
            if let Some(o) = options.as_ref().filter(|o| o.as_object().is_some_and(|m| !m.is_empty())) {
                db.execute("UPDATE jobs SET options = ? WHERE id = ?", (util::py_json(o), id))?;
            }
            return Ok(None);
        }
    }
    db.execute(
        "INSERT INTO jobs (kind, case_id, file_id, options, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?)",
        (kind, case_id, file_id, util::py_json(&options.unwrap_or_else(|| json!({}))), util::now(), user.map(|u| u.id)),
    )?;
    Ok(Some(db.last_insert_rowid()))
}

/// Waiting, running and failed jobs per kind, for cases the caller may see
/// (None means every case).
pub fn counts(db: &Connection, case_ids: Option<&[String]>) -> rusqlite::Result<Value> {
    let (where_, args): (String, Vec<String>) = match case_ids {
        None => (String::new(), vec![]),
        Some([]) => return Ok(json!({})),
        Some(ids) => (format!(" AND case_id IN ({})", vec!["?"; ids.len()].join(",")), ids.to_vec()),
    };
    let mut stmt = db.prepare(&format!(
        "SELECT kind, state, COUNT(*), SUM(done), SUM(total) FROM jobs WHERE state IN ('queued', 'running', 'failed'){where_} GROUP BY kind, state"
    ))?;
    let mut out = Map::new();
    let mut rows = stmt.query(rusqlite::params_from_iter(args))?;
    while let Some(r) = rows.next()? {
        let kind: String = r.get(0)?;
        let state: String = r.get(1)?;
        let k = out
            .entry(kind)
            .or_insert_with(|| json!({"queued": 0, "running": 0, "failed": 0, "done": 0, "total": 0}))
            .as_object_mut()
            .unwrap();
        k.insert(state.clone(), json!(r.get::<_, i64>(2)?));
        if state == "running" {
            k.insert("done".into(), json!(r.get::<_, Option<i64>>(3)?.unwrap_or(0)));
            k.insert("total".into(), json!(r.get::<_, Option<i64>>(4)?.unwrap_or(0)));
        }
    }
    Ok(Value::Object(out))
}
