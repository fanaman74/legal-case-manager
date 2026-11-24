//! Cases: list, create, view, edit, archive, delete, and who is assigned.

use std::collections::{BTreeSet, HashMap};

use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use rusqlite::Connection;
use serde::Deserialize;
use serde_json::{json, Value};

use super::{in_range, max_items, max_len, min_len, Body, Params};
use crate::access::{self, Case};
use crate::accounts::{AdminUser, CurrentUser, User};
use crate::audit::{self, Entry};
use crate::db::write_tx;
use crate::state::AppState;
use crate::util::{self, ApiError, ApiResult};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NewCase {
    name: String,
    #[serde(default)]
    reference: String,
    #[serde(default)]
    client: String,
    #[serde(default)]
    description: String,
    #[serde(default)]
    member_ids: Vec<i64>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CaseEdit {
    name: Option<String>,
    reference: Option<String>,
    client: Option<String>,
    description: Option<String>,
    notes: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Members {
    user_ids: Vec<i64>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ConfirmDelete {
    confirm_name: String,
}

#[derive(Deserialize)]
pub struct ListQuery {
    #[serde(default)]
    archived: Option<String>,
}

#[derive(Deserialize)]
pub struct PageQuery {
    before: Option<i64>,
    limit: Option<i64>,
}

fn case_view(c: &Case, extra: Value) -> Value {
    let mut m = c.view();
    if let Value::Object(e) = extra {
        m.extend(e);
    }
    Value::Object(m)
}

fn check_members(db: &Connection, user_ids: &[i64]) -> ApiResult<Vec<i64>> {
    let ids: Vec<i64> = user_ids.iter().copied().collect::<BTreeSet<_>>().into_iter().collect();
    if ids.is_empty() {
        return Ok(ids);
    }
    let marks = vec!["?"; ids.len()].join(",");
    let mut stmt = db.prepare(&format!("SELECT role FROM users WHERE id IN ({marks})"))?;
    let roles: Vec<String> = stmt.query_map(rusqlite::params_from_iter(&ids), |r| r.get(0))?.collect::<rusqlite::Result<_>>()?;
    if roles.len() != ids.len() {
        return Err(ApiError::new(400, "One of those people doesn't exist. Reload the page and try again."));
    }
    if roles.iter().any(|r| r == "admin") {
        return Err(ApiError::new(400, "The Admin already sees every case, so doesn't need to be added."));
    }
    Ok(ids)
}

/// FastAPI's reading of a boolean query value.
pub fn query_bool(v: Option<&str>) -> ApiResult<bool> {
    match v.map(|s| s.to_lowercase()) {
        None => Ok(false),
        Some(s) if ["1", "true", "on", "yes", "y", "t"].contains(&s.as_str()) => Ok(true),
        Some(s) if ["0", "false", "off", "no", "n", "f"].contains(&s.as_str()) => Ok(false),
        Some(_) => Err(ApiError::invalid("expected true or false")),
    }
}

pub async fn list_cases(State(st): State<AppState>, CurrentUser(user, _): CurrentUser, Params(q): Params<ListQuery>) -> ApiResult<Json<Value>> {
    let archived = query_bool(q.archived.as_deref())?;
    st.run(move |st| {
        let db = st.db.connect()?;
        let mut where_ = Vec::new();
        let mut args: Vec<i64> = Vec::new();
        if !user.is_admin() {
            where_.push("c.id IN (SELECT case_id FROM case_members WHERE user_id = ?)");
            args.push(user.id);
        }
        if !archived {
            where_.push("c.status = 'active'");
        }
        let clause = if where_.is_empty() { String::new() } else { format!("WHERE {}", where_.join(" AND ")) };
        let sql = format!(
            "SELECT {}, (SELECT COUNT(*) FROM files f WHERE f.case_id = c.id), (SELECT COUNT(*) FROM case_members m WHERE m.case_id = c.id)
             FROM cases c {clause} ORDER BY c.created_at DESC",
            Case::COLUMNS
        );
        let mut stmt = db.prepare(&sql)?;
        let items = stmt
            .query_map(rusqlite::params_from_iter(args), |r| {
                let c = Case::from_row(r)?;
                Ok(case_view(&c, json!({"file_count": r.get::<_, i64>(9)?, "member_count": r.get::<_, i64>(10)?})))
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(Json(json!({"items": items})))
    })
    .await
}

pub async fn create_case(State(st): State<AppState>, AdminUser(user, ip): AdminUser, Body(body): Body<NewCase>) -> ApiResult<(StatusCode, Json<Value>)> {
    min_len("name", &body.name, 1)?;
    max_len("name", &body.name, 200)?;
    max_len("reference", &body.reference, 100)?;
    max_len("client", &body.client, 200)?;
    max_len("description", &body.description, 5000)?;
    max_items("member_ids", &body.member_ids, 500)?;
    let name = util::strip(&body.name);
    if name.is_empty() {
        return Err(ApiError::new(400, "Give the case a name."));
    }
    st.run(move |st| {
        let case_id = util::new_id();
        st.storage.create_case(&case_id)?;
        let mut db = st.db.connect()?;
        let tx = write_tx(&mut db)?;
        let members = check_members(&tx, &body.member_ids)?;
        tx.execute(
            "INSERT INTO cases (id, name, reference, client, description, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (&case_id, &name, util::strip(&body.reference), util::strip(&body.client), &body.description, util::now(), user.id),
        )?;
        for m in &members {
            tx.execute("INSERT INTO case_members (case_id, user_id) VALUES (?, ?)", (&case_id, m))?;
        }
        audit::record(&tx, Entry { action: "case.create", user: Some(&user), case_id: Some(&case_id), target: &name, ip: &ip, ..Default::default() })?;
        let c = access::load_case(&tx, &case_id)?.unwrap();
        tx.commit()?;
        Ok((StatusCode::CREATED, Json(case_view(&c, json!({"permissions": access::permissions(&user, &c)})))))
    })
    .await
}

pub async fn get_case(State(st): State<AppState>, CurrentUser(user, ip): CurrentUser, Path(case_id): Path<String>) -> ApiResult<Json<Value>> {
    st.run(move |st| {
        let mut db = st.db.connect()?;
        let tx = write_tx(&mut db)?;
        let c = access::visible_case(&tx, &user, &case_id)?;
        audit::record(&tx, Entry { action: "case.view", user: Some(&user), case_id: Some(&case_id), target: &c.name, ip: &ip, ..Default::default() })?;
        let creator: Option<String> = match c.created_by {
            Some(id) => crate::accounts::user_by_id(&tx, id)?.map(|u| u.display_name),
            None => None,
        };
        tx.commit()?;
        Ok(Json(case_view(&c, json!({"created_by": creator.unwrap_or_default(), "permissions": access::permissions(&user, &c)}))))
    })
    .await
}

pub async fn edit_case(State(st): State<AppState>, CurrentUser(user, ip): CurrentUser, Path(case_id): Path<String>, Body(body): Body<CaseEdit>) -> ApiResult<Json<Value>> {
    if let Some(n) = &body.name {
        min_len("name", n, 1)?;
        max_len("name", n, 200)?;
    }
    for (f, v, m) in [("reference", &body.reference, 100), ("client", &body.client, 200), ("description", &body.description, 5000), ("notes", &body.notes, 100_000)] {
        if let Some(v) = v {
            max_len(f, v, m)?;
        }
    }
    let name = body.name.as_deref().map(util::strip);
    if name.as_deref() == Some("") {
        return Err(ApiError::new(400, "Give the case a name."));
    }
    st.run(move |st| {
        let mut db = st.db.connect()?;
        let tx = write_tx(&mut db)?;
        let c = access::visible_case(&tx, &user, &case_id)?;
        access::require_edit(&user, &c)?;
        if let Some(n) = &name {
            if *n != c.name && !user.is_admin() {
                return Err(ApiError::new(403, "Only the Admin can rename a case."));
            }
        }
        let mut changed: Vec<(&str, String)> = Vec::new();
        for (k, new, old) in [
            ("name", &name, &c.name),
            ("reference", &body.reference, &c.reference),
            ("client", &body.client, &c.client),
            ("description", &body.description, &c.description),
            ("notes", &body.notes, &c.notes),
        ] {
            if let Some(v) = new {
                if v != old {
                    changed.push((k, v.clone()));
                }
            }
        }
        if !changed.is_empty() {
            let sets = changed.iter().map(|(k, _)| format!("{k} = ?")).collect::<Vec<_>>().join(", ");
            let mut args: Vec<String> = changed.iter().map(|(_, v)| v.clone()).collect();
            args.push(case_id.clone());
            tx.execute(&format!("UPDATE cases SET {sets} WHERE id = ?"), rusqlite::params_from_iter(args))?;
            let detail = if changed.iter().any(|(k, _)| *k == "name") { format!("renamed from {}", py_repr(&c.name)) } else { String::new() };
            let mut keys: Vec<&str> = changed.iter().map(|(k, _)| *k).collect();
            keys.sort();
            audit::record(&tx, Entry { action: "case.edit", user: Some(&user), case_id: Some(&case_id), target: &keys.join(", "), detail: &detail, ip: &ip, ..Default::default() })?;
        }
        let c = access::load_case(&tx, &case_id)?.unwrap();
        tx.commit()?;
        Ok(Json(case_view(&c, json!({"permissions": access::permissions(&user, &c)}))))
    })
    .await
}

/// Python's repr() of a string, as used in the "renamed from" audit detail.
fn py_repr(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') { '"' } else { '\'' };
    let mut out = String::from(quote);
    for ch in s.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => out.push_str(&format!("\\x{:02x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

fn set_status(st: &AppState, user: &User, ip: &str, case_id: &str, status: &str) -> ApiResult<Json<Value>> {
    let mut db = st.db.connect()?;
    let tx = write_tx(&mut db)?;
    let c = access::visible_case(&tx, user, case_id)?;
    if c.status != status {
        tx.execute("UPDATE cases SET status = ? WHERE id = ?", (status, case_id))?;
        let action = if status == "archived" { "case.archive" } else { "case.restore" };
        audit::record(&tx, Entry { action, user: Some(user), case_id: Some(case_id), target: &c.name, ip, ..Default::default() })?;
    }
    let c = access::load_case(&tx, case_id)?.unwrap();
    tx.commit()?;
    Ok(Json(case_view(&c, json!({"permissions": access::permissions(user, &c)}))))
}

pub async fn archive_case(State(st): State<AppState>, AdminUser(user, ip): AdminUser, Path(case_id): Path<String>) -> ApiResult<Json<Value>> {
    st.run(move |st| set_status(st, &user, &ip, &case_id, "archived")).await
}

pub async fn restore_case(State(st): State<AppState>, AdminUser(user, ip): AdminUser, Path(case_id): Path<String>) -> ApiResult<Json<Value>> {
    st.run(move |st| set_status(st, &user, &ip, &case_id, "active")).await
}

/// Deletes the case, its files on disk and its records. The audit log keeps
/// its entries for the case.
pub async fn delete_case(State(st): State<AppState>, AdminUser(user, ip): AdminUser, Path(case_id): Path<String>, Body(body): Body<ConfirmDelete>) -> ApiResult<Response> {
    max_len("confirm_name", &body.confirm_name, 200)?;
    st.run(move |st| {
        let mut db = st.db.connect()?;
        let tx = write_tx(&mut db)?;
        let c = access::visible_case(&tx, &user, &case_id)?;
        if util::strip(&body.confirm_name) != c.name {
            return Err(ApiError::new(400, "Type the case name exactly to confirm."));
        }
        let files: i64 = tx.query_row("SELECT COUNT(*) FROM files WHERE case_id = ?", [&case_id], |r| r.get(0))?;
        tx.execute("DELETE FROM cases WHERE id = ?", [&case_id])?;
        let detail = format!("{files} files deleted");
        audit::record(&tx, Entry { action: "case.delete", user: Some(&user), case_id: Some(&case_id), target: &c.name, detail: &detail, ip: &ip, ..Default::default() })?;
        if st.storage.trash_case(&case_id).is_err() {
            return Err(ApiError::new(409, "The case's files are in use, so the case was kept. Close any program using them and try again."));
        }
        tx.commit()?;
        st.storage.empty_trash();
        Ok(StatusCode::NO_CONTENT.into_response())
    })
    .await
}

fn members(db: &Connection, case_id: &str) -> rusqlite::Result<Value> {
    let mut stmt = db.prepare(
        "SELECT u.id, u.display_name, u.username, u.role, u.active FROM users u JOIN case_members m ON m.user_id = u.id
         WHERE m.case_id = ? ORDER BY u.display_name COLLATE NOCASE",
    )?;
    let items = stmt
        .query_map([case_id], |r| {
            Ok(json!({"id": r.get::<_, i64>(0)?, "display_name": r.get::<_, String>(1)?, "username": r.get::<_, String>(2)?,
                      "role": r.get::<_, String>(3)?, "active": r.get::<_, i64>(4)? != 0}))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    Ok(json!({"items": items}))
}

pub async fn list_members(State(st): State<AppState>, CurrentUser(user, _): CurrentUser, Path(case_id): Path<String>) -> ApiResult<Json<Value>> {
    st.run(move |st| {
        let db = st.db.connect()?;
        access::visible_case(&db, &user, &case_id)?;
        Ok(Json(members(&db, &case_id)?))
    })
    .await
}

pub async fn set_members(State(st): State<AppState>, AdminUser(user, ip): AdminUser, Path(case_id): Path<String>, Body(body): Body<Members>) -> ApiResult<Json<Value>> {
    max_items("user_ids", &body.user_ids, 500)?;
    st.run(move |st| {
        let mut db = st.db.connect()?;
        let tx = write_tx(&mut db)?;
        let c = access::visible_case(&tx, &user, &case_id)?;
        let ids: BTreeSet<i64> = check_members(&tx, &body.user_ids)?.into_iter().collect();
        let before: BTreeSet<i64> = {
            let mut stmt = tx.prepare("SELECT user_id FROM case_members WHERE case_id = ?")?;
            let it = stmt.query_map([&case_id], |r| r.get(0))?;
            it.collect::<rusqlite::Result<_>>()?
        };
        let added: Vec<i64> = ids.difference(&before).copied().collect();
        let removed: Vec<i64> = before.difference(&ids).copied().collect();
        for u in &removed {
            tx.execute("DELETE FROM case_members WHERE case_id = ? AND user_id = ?", (&case_id, u))?;
        }
        for u in &added {
            tx.execute("INSERT INTO case_members (case_id, user_id) VALUES (?, ?)", (&case_id, u))?;
        }
        if !added.is_empty() || !removed.is_empty() {
            let names: HashMap<i64, String> = {
                let mut stmt = tx.prepare("SELECT id, username FROM users")?;
                let it = stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?;
                it.collect::<rusqlite::Result<_>>()?
            };
            let list = |ids: &[i64]| {
                let mut n: Vec<String> = ids.iter().map(|u| names[u].clone()).collect();
                n.sort();
                n.join(", ")
            };
            let mut parts = Vec::new();
            if !added.is_empty() {
                parts.push(format!("added {}", list(&added)));
            }
            if !removed.is_empty() {
                parts.push(format!("removed {}", list(&removed)));
            }
            audit::record(&tx, Entry { action: "case.members", user: Some(&user), case_id: Some(&case_id), target: &c.name, detail: &parts.join("; "), ip: &ip, ..Default::default() })?;
        }
        let v = members(&tx, &case_id)?;
        tx.commit()?;
        Ok(Json(v))
    })
    .await
}

pub async fn case_activity(State(st): State<AppState>, CurrentUser(user, _): CurrentUser, Path(case_id): Path<String>, Params(q): Params<PageQuery>) -> ApiResult<Json<Value>> {
    if let Some(b) = q.before {
        in_range("before", b, 1, i64::MAX)?;
    }
    let limit = q.limit.unwrap_or(100);
    in_range("limit", limit, 1, 500)?;
    st.run(move |st| {
        let db = st.db.connect()?;
        access::visible_case(&db, &user, &case_id)?;
        let mut stmt = db.prepare(
            "SELECT id, at, username, action, outcome, target, detail FROM audit WHERE case_id = ? AND id < ? ORDER BY id DESC LIMIT ?",
        )?;
        let items = stmt
            .query_map((&case_id, q.before.unwrap_or(1 << 62), limit), |r| {
                Ok(json!({"id": r.get::<_, i64>(0)?, "at": r.get::<_, String>(1)?, "username": r.get::<_, String>(2)?,
                          "action": r.get::<_, String>(3)?, "outcome": r.get::<_, String>(4)?, "target": r.get::<_, String>(5)?,
                          "detail": r.get::<_, String>(6)?}))
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(Json(json!({"items": items})))
    })
    .await
}

#[cfg(test)]
mod tests {
    #[test]
    fn repr_like_python() {
        assert_eq!(super::py_repr("Smith v Jones"), "'Smith v Jones'");
        assert_eq!(super::py_repr("O'Brien"), "\"O'Brien\"");
    }
}
