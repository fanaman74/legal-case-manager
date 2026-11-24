//! Background work as people see it: the status line in the top bar (for
//! everyone, limited to their cases) and the job list with failures and
//! Retry (Admin).

use axum::extract::{Path, State};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Map, Value};

use super::{in_range, Params};
use crate::accounts::{AdminUser, CurrentUser};
use crate::audit::{self, Entry};
use crate::db::write_tx;
use crate::jobs;
use crate::state::AppState;
use crate::util::{shown_path, ApiError, ApiResult};

/// How the status banner and Control Center name each kind of job.
pub fn label(kind: &str) -> &str {
    match kind {
        "convert" => "Conversion",
        "ocr" => "Reading scanned pages (OCR)",
        "index" => "Indexing",
        "dups" => "Finding near-duplicates",
        "pst" => "Reading mailbox",
        "pst_add" => "Adding emails",
        "export" => "Export",
        "model" => "Downloading AI model",
        other => other,
    }
}

/// Work in progress across the cases this person can see, for the banner
/// ("Converting 12 of 80 files").
pub async fn status(State(st): State<AppState>, CurrentUser(user, _): CurrentUser) -> ApiResult<Json<Value>> {
    st.run(move |st| {
        let db = st.db.connect()?;
        let allowed: Option<Vec<String>> = if user.is_admin() {
            None
        } else {
            let mut stmt = db.prepare("SELECT case_id FROM case_members WHERE user_id = ?")?;
            let ids = stmt.query_map([user.id], |r| r.get(0))?.collect::<rusqlite::Result<_>>()?;
            Some(ids)
        };
        let counts = jobs::counts(&db, allowed.as_deref())?;
        let (where_, args) = match &allowed {
            None => (String::new(), vec![]),
            Some(ids) if ids.is_empty() => return Ok(Json(json!({"jobs": {}, "files": {}}))),
            Some(ids) => (format!(" WHERE case_id IN ({})", vec!["?"; ids.len()].join(",")), ids.clone()),
        };
        let mut stmt = db.prepare(&format!("SELECT status, COUNT(*) FROM files{where_} GROUP BY status"))?;
        let mut files = Map::new();
        let mut rows = stmt.query(rusqlite::params_from_iter(args))?;
        while let Some(r) = rows.next()? {
            files.insert(r.get(0)?, json!(r.get::<_, i64>(1)?));
        }
        Ok(Json(json!({"jobs": counts, "files": files})))
    })
    .await
}

#[derive(Deserialize)]
pub struct JobsQuery {
    state: Option<String>,
    limit: Option<i64>,
}

pub async fn list_jobs(State(st): State<AppState>, _: AdminUser, Params(q): Params<JobsQuery>) -> ApiResult<Json<Value>> {
    if let Some(s) = &q.state {
        if !["queued", "running", "done", "failed"].contains(&s.as_str()) {
            return Err(ApiError::invalid("state: queued, running, done or failed"));
        }
    }
    let limit = q.limit.unwrap_or(200);
    in_range("limit", limit, 1, 1000)?;
    st.run(move |st| {
        let db = st.db.connect()?;
        let (where_, mut args): (&str, Vec<rusqlite::types::Value>) = match q.state {
            Some(s) => ("WHERE j.state = ?", vec![s.into()]),
            None => ("", vec![]),
        };
        args.push(limit.into());
        let mut stmt = db.prepare(&format!(
            "SELECT j.id, j.kind, j.state, j.case_id, c.name, j.file_id, f.name, f.folder, m.name, j.options, j.error, j.attempts,
                    j.done, j.total, j.created_at, j.started_at, j.finished_at
             FROM jobs j LEFT JOIN cases c ON c.id = j.case_id LEFT JOIN files f ON f.id = j.file_id
             LEFT JOIN mailboxes m ON m.id = json_extract(j.options, '$.mailbox')
             {where_} ORDER BY CASE j.state WHEN 'running' THEN 0 WHEN 'failed' THEN 1 WHEN 'queued' THEN 2 ELSE 3 END, j.id DESC LIMIT ?"
        ))?;
        let items = stmt
            .query_map(rusqlite::params_from_iter(args), |r| {
                let kind: String = r.get(1)?;
                let file_name: Option<String> = r.get(6)?;
                let file = match file_name {
                    Some(n) => json!(shown_path(&r.get::<_, Option<String>>(7)?.unwrap_or_default(), &n)),
                    None => match r.get::<_, Option<String>>(8)? {
                        Some(m) if !m.is_empty() => json!(m),
                        _ => {
                            let o: Value = serde_json::from_str(&r.get::<_, Option<String>>(9)?.unwrap_or_default()).unwrap_or(json!({}));
                            o.get("model").cloned().unwrap_or(Value::Null)
                        }
                    },
                };
                Ok(json!({
                    "id": r.get::<_, i64>(0)?, "kind": kind, "label": label(&kind), "state": r.get::<_, String>(2)?,
                    "case_id": r.get::<_, Option<String>>(3)?, "case_name": r.get::<_, Option<String>>(4)?,
                    "file_id": r.get::<_, Option<String>>(5)?, "file": file, "error": r.get::<_, String>(10)?,
                    "attempts": r.get::<_, i64>(11)?, "done": r.get::<_, i64>(12)?, "total": r.get::<_, i64>(13)?,
                    "created_at": r.get::<_, String>(14)?, "started_at": r.get::<_, Option<String>>(15)?,
                    "finished_at": r.get::<_, Option<String>>(16)?,
                }))
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        let counts = jobs::counts(&db, None)?;
        Ok(Json(json!({"items": items, "counts": counts})))
    })
    .await
}

pub async fn retry(State(st): State<AppState>, AdminUser(user, ip): AdminUser, Path(job_id): Path<i64>) -> ApiResult<Json<Value>> {
    st.run(move |st| {
        let mut db = st.db.connect()?;
        let tx = write_tx(&mut db)?;
        let row: Option<(String, String, Option<String>, Option<String>, String)> = rusqlite::OptionalExtension::optional(tx.query_row(
            "SELECT kind, state, case_id, file_id, options FROM jobs WHERE id = ?",
            [job_id],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get::<_, Option<String>>(4)?.unwrap_or_default())),
        ))?;
        let Some((kind, state, case_id, file_id, options)) = row else {
            return Err(ApiError::new(404, "That job no longer exists."));
        };
        if state != "failed" {
            return Err(ApiError::new(409, "Only failed jobs can be retried."));
        }
        tx.execute("UPDATE jobs SET state = 'queued', error = '', attempts = 0, started_at = NULL, finished_at = NULL WHERE id = ?", [job_id])?;
        if let Some(fid) = &file_id {
            if kind == "convert" {
                tx.execute("UPDATE files SET status = 'queued', error = '' WHERE id = ?", [fid])?;
            }
            if kind == "ocr" {
                tx.execute("UPDATE files SET ocr_state = 'waiting', ocr_error = '' WHERE id = ?", [fid])?;
            }
        }
        if kind == "pst" {
            let o: Value = serde_json::from_str(&options).unwrap_or(json!({}));
            tx.execute("UPDATE mailboxes SET status = 'queued', error = '' WHERE id = ?", [o.get("mailbox").and_then(|m| m.as_str())])?;
        }
        let detail = format!("job {job_id}");
        audit::record(&tx, Entry { action: "job.retry", user: Some(&user), case_id: case_id.as_deref(), target: label(&kind), detail: &detail, ip: &ip, ..Default::default() })?;
        tx.commit()?;
        Ok(Json(json!({"ok": true})))
    })
    .await
}
