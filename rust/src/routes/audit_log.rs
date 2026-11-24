//! The audit log (Admin only), with a tamper check of the hash chain.

use axum::extract::State;
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use super::{in_range, max_len, Params};
use crate::accounts::AdminUser;
use crate::audit;
use crate::state::AppState;
use crate::util::ApiResult;

#[derive(Deserialize)]
pub struct AuditQuery {
    username: Option<String>,
    action: Option<String>,
    case_id: Option<String>,
    since: Option<String>,
    until: Option<String>,
    before: Option<i64>,
    limit: Option<i64>,
}

pub async fn audit_log(State(st): State<AppState>, _: AdminUser, Params(q): Params<AuditQuery>) -> ApiResult<Json<Value>> {
    for (f, v, m) in [("username", &q.username, 64), ("action", &q.action, 64), ("case_id", &q.case_id, 32), ("since", &q.since, 40), ("until", &q.until, 40)] {
        if let Some(v) = v {
            max_len(f, v, m)?;
        }
    }
    if let Some(b) = q.before {
        in_range("before", b, 1, i64::MAX)?;
    }
    let limit = q.limit.unwrap_or(200);
    in_range("limit", limit, 1, 1000)?;
    st.run(move |st| {
        let mut where_ = vec!["a.id < ?"];
        let mut args: Vec<rusqlite::types::Value> = vec![q.before.unwrap_or(1 << 62).into()];
        if let Some(u) = q.username.filter(|v| !v.is_empty()) {
            where_.push("a.username = ? COLLATE NOCASE");
            args.push(u.into());
        }
        if let Some(a) = q.action.filter(|v| !v.is_empty()) {
            if a.ends_with('.') {
                where_.push("substr(a.action, 1, ?) = ?");
                args.push((a.chars().count() as i64).into());
                args.push(a.into());
            } else {
                where_.push("a.action = ?");
                args.push(a.into());
            }
        }
        for (col, v) in [("a.case_id = ?", q.case_id), ("a.at >= ?", q.since), ("a.at < ?", q.until)] {
            if let Some(v) = v.filter(|v| !v.is_empty()) {
                where_.push(col);
                args.push(v.into());
            }
        }
        args.push(limit.into());
        let db = st.db.connect()?;
        let mut stmt = db.prepare(&format!(
            "SELECT a.id, a.at, a.username, a.action, a.outcome, a.case_id, c.name, a.target, a.detail, a.ip
             FROM audit a LEFT JOIN cases c ON c.id = a.case_id WHERE {} ORDER BY a.id DESC LIMIT ?",
            where_.join(" AND ")
        ))?;
        let items = stmt
            .query_map(rusqlite::params_from_iter(args), |r| {
                Ok(json!({
                    "id": r.get::<_, i64>(0)?, "at": r.get::<_, String>(1)?, "username": r.get::<_, String>(2)?,
                    "action": r.get::<_, String>(3)?, "outcome": r.get::<_, String>(4)?, "case_id": r.get::<_, Option<String>>(5)?,
                    "case_name": r.get::<_, Option<String>>(6)?, "target": r.get::<_, String>(7)?, "detail": r.get::<_, String>(8)?,
                    "ip": r.get::<_, String>(9)?,
                }))
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        let (checked, broken) = audit::verify(&db)?;
        Ok(Json(json!({"items": items, "tamper_check": {"entries": checked, "intact": broken.is_none(), "first_bad_entry": broken}})))
    })
    .await
}
