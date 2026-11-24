//! People (Admin only): add, change role, switch off, reset password, unlock,
//! and which cases each person is assigned to.

use std::collections::BTreeSet;

use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::Json;
use rusqlite::Connection;
use serde::Deserialize;
use serde_json::{json, Value};

use super::{max_items, max_len, min_len, Body};
use crate::accounts::{self, AdminUser, User};
use crate::audit::{self, Entry};
use crate::db::write_tx;
use crate::state::AppState;
use crate::util::{self, ApiError, ApiResult};

#[derive(Deserialize, Clone, Copy, PartialEq)]
#[serde(rename_all = "lowercase")]
enum Role {
    Editor,
    Readonly,
}

impl Role {
    fn as_str(self) -> &'static str {
        match self {
            Role::Editor => "editor",
            Role::Readonly => "readonly",
        }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NewUser {
    username: String,
    display_name: String,
    role: Role,
    #[serde(default)]
    case_ids: Vec<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UserEdit {
    display_name: Option<String>,
    role: Option<Role>,
    active: Option<bool>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CaseList {
    case_ids: Vec<String>,
}

fn view(db: &Connection, st: &AppState, u: &User) -> rusqlite::Result<Value> {
    let mut stmt = db.prepare("SELECT case_id FROM case_members WHERE user_id = ? ORDER BY case_id")?;
    let cases: Vec<String> = stmt.query_map([u.id], |r| r.get(0))?.collect::<rusqlite::Result<_>>()?;
    let mut v = u.view();
    let m = v.as_object_mut().unwrap();
    m.insert("active".into(), json!(u.active));
    m.insert("locked".into(), json!(st.throttle.locked(&[&format!("user|{}", u.username.to_lowercase())])));
    m.insert("last_login_at".into(), json!(u.last_login_at));
    m.insert("created_at".into(), json!(u.created_at));
    m.insert("case_ids".into(), json!(cases));
    Ok(v)
}

fn target(db: &Connection, user_id: i64, not_admin: Option<&str>) -> ApiResult<User> {
    let u = accounts::user_by_id(db, user_id)?.ok_or_else(|| ApiError::new(404, "That person doesn't exist. Reload the page."))?;
    if let (Some(msg), true) = (not_admin, u.is_admin()) {
        return Err(ApiError::new(409, msg));
    }
    Ok(u)
}

fn check_cases(db: &Connection, case_ids: &[String]) -> ApiResult<Vec<String>> {
    let ids: Vec<String> = case_ids.iter().cloned().collect::<BTreeSet<_>>().into_iter().collect();
    if !ids.is_empty() {
        let marks = vec!["?"; ids.len()].join(",");
        let n: i64 = db.query_row(&format!("SELECT COUNT(*) FROM cases WHERE id IN ({marks})"), rusqlite::params_from_iter(&ids), |r| r.get(0))?;
        if n as usize != ids.len() {
            return Err(ApiError::new(400, "One of those cases doesn't exist. Reload the page and try again."));
        }
    }
    Ok(ids)
}

pub async fn list_users(State(st): State<AppState>, _: AdminUser) -> ApiResult<Json<Value>> {
    st.run(|st| {
        let db = st.db.connect()?;
        let mut stmt = db.prepare(&format!(
            "SELECT {} FROM users u ORDER BY u.role = 'admin' DESC, u.display_name COLLATE NOCASE",
            User::COLUMNS
        ))?;
        let users: Vec<User> = stmt.query_map([], |r| User::from_row(r, 0))?.collect::<rusqlite::Result<_>>()?;
        let items = users.iter().map(|u| view(&db, st, u)).collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(Json(json!({"items": items})))
    })
    .await
}

/// Creates the account with a temporary password, shown once. The person
/// chooses their own password the first time they sign in.
pub async fn add_user(State(st): State<AppState>, AdminUser(admin, ip): AdminUser, Body(body): Body<NewUser>) -> ApiResult<(StatusCode, Json<Value>)> {
    max_len("username", &body.username, 64)?;
    min_len("display_name", &body.display_name, 1)?;
    max_len("display_name", &body.display_name, 100)?;
    max_items("case_ids", &body.case_ids, 1000)?;
    if !accounts::valid_username(&body.username) {
        return Err(ApiError::new(400, "Use 2 to 64 letters, numbers, dots, dashes, underscores or @ for the username."));
    }
    let display = util::strip(&body.display_name);
    if display.is_empty() {
        return Err(ApiError::new(400, "Enter the person's name."));
    }
    st.run(move |st| {
        let temp = accounts::temporary_password();
        let hash = accounts::hash_password(&temp);
        let mut db = st.db.connect()?;
        let tx = write_tx(&mut db)?;
        if accounts::user_by_name(&tx, &body.username)?.is_some() {
            return Err(ApiError::new(409, "Someone already has that username. Choose another."));
        }
        let cases = check_cases(&tx, &body.case_ids)?;
        tx.execute(
            "INSERT INTO users (username, display_name, role, password_hash, must_change_password, created_at) VALUES (?, ?, ?, ?, 1, ?)",
            (&body.username, &display, body.role.as_str(), &hash, util::now()),
        )?;
        let uid = tx.last_insert_rowid();
        for c in &cases {
            tx.execute("INSERT INTO case_members (case_id, user_id) VALUES (?, ?)", (c, uid))?;
        }
        let detail = format!("role {}; {} cases", body.role.as_str(), cases.len());
        audit::record(&tx, Entry { action: "user.create", user: Some(&admin), target: &body.username, detail: &detail, ip: &ip, ..Default::default() })?;
        let u = accounts::user_by_id(&tx, uid)?.unwrap();
        let v = view(&tx, st, &u)?;
        tx.commit()?;
        Ok((StatusCode::CREATED, Json(json!({"user": v, "temporary_password": temp}))))
    })
    .await
}

pub async fn edit_user(State(st): State<AppState>, AdminUser(admin, ip): AdminUser, Path(user_id): Path<i64>, Body(body): Body<UserEdit>) -> ApiResult<Json<Value>> {
    if let Some(d) = &body.display_name {
        min_len("display_name", d, 1)?;
        max_len("display_name", d, 100)?;
    }
    st.run(move |st| {
        let mut db = st.db.connect()?;
        let tx = write_tx(&mut db)?;
        let row = target(&tx, user_id, None)?;
        if row.is_admin() && (body.role.is_some() || body.active == Some(false)) {
            return Err(ApiError::new(409, "The Admin account can't be switched off or given another role."));
        }
        // (column, new value, shown value), in the order the Python version reported them.
        let mut changed: Vec<(&str, Value)> = Vec::new();
        if let Some(d) = &body.display_name {
            let d = util::strip(d);
            if d.is_empty() {
                return Err(ApiError::new(400, "Enter the person's name."));
            }
            if d != row.display_name {
                changed.push(("display_name", json!(d)));
            }
        }
        if let Some(r) = body.role {
            if r.as_str() != row.role {
                changed.push(("role", json!(r.as_str())));
            }
        }
        if let Some(a) = body.active {
            if a != row.active {
                changed.push(("active", json!(a as i64)));
            }
        }
        if !changed.is_empty() {
            let sets = changed.iter().map(|(k, _)| format!("{k} = ?")).collect::<Vec<_>>().join(", ");
            let mut args: Vec<rusqlite::types::Value> = changed
                .iter()
                .map(|(_, v)| match v {
                    Value::String(s) => rusqlite::types::Value::Text(s.clone()),
                    Value::Number(n) => rusqlite::types::Value::Integer(n.as_i64().unwrap()),
                    _ => unreachable!(),
                })
                .collect();
            args.push(rusqlite::types::Value::Integer(user_id));
            tx.execute(&format!("UPDATE users SET {sets} WHERE id = ?"), rusqlite::params_from_iter(args))?;
            if changed.iter().any(|(k, v)| (*k == "active" && v == &json!(0)) || *k == "role") {
                tx.execute("DELETE FROM sessions WHERE user_id = ?", [user_id])?;
            }
            let detail = changed
                .iter()
                .map(|(k, v)| format!("{k} {}", match v { Value::String(s) => s.clone(), other => other.to_string() }))
                .collect::<Vec<_>>()
                .join("; ");
            audit::record(&tx, Entry { action: "user.edit", user: Some(&admin), target: &row.username, detail: &detail, ip: &ip, ..Default::default() })?;
        }
        let u = accounts::user_by_id(&tx, user_id)?.unwrap();
        let v = view(&tx, st, &u)?;
        tx.commit()?;
        Ok(Json(v))
    })
    .await
}

pub async fn reset_password(State(st): State<AppState>, AdminUser(admin, ip): AdminUser, Path(user_id): Path<i64>) -> ApiResult<Json<Value>> {
    st.run(move |st| {
        let temp = accounts::temporary_password();
        let hash = accounts::hash_password(&temp);
        let mut db = st.db.connect()?;
        let tx = write_tx(&mut db)?;
        let row = target(&tx, user_id, Some("The Admin password is changed in the Control Center on the host computer."))?;
        tx.execute("UPDATE users SET password_hash = ?, must_change_password = 1 WHERE id = ?", (&hash, user_id))?;
        tx.execute("DELETE FROM sessions WHERE user_id = ?", [user_id])?;
        audit::record(&tx, Entry { action: "user.reset_password", user: Some(&admin), target: &row.username, ip: &ip, ..Default::default() })?;
        st.throttle.clear(&[&format!("user|{}", row.username.to_lowercase())]);
        // The view shows the person as they were before the reset, like the Python version.
        let v = view(&tx, st, &row)?;
        tx.commit()?;
        Ok(Json(json!({"user": v, "temporary_password": temp})))
    })
    .await
}

pub async fn unlock(State(st): State<AppState>, AdminUser(admin, ip): AdminUser, Path(user_id): Path<i64>) -> ApiResult<Json<Value>> {
    st.run(move |st| {
        let mut db = st.db.connect()?;
        let tx = write_tx(&mut db)?;
        let row = target(&tx, user_id, None)?;
        st.throttle.clear(&[&format!("user|{}", row.username.to_lowercase())]);
        audit::record(&tx, Entry { action: "user.unlock", user: Some(&admin), target: &row.username, ip: &ip, ..Default::default() })?;
        let v = view(&tx, st, &row)?;
        tx.commit()?;
        Ok(Json(v))
    })
    .await
}

pub async fn set_cases(State(st): State<AppState>, AdminUser(admin, ip): AdminUser, Path(user_id): Path<i64>, Body(body): Body<CaseList>) -> ApiResult<Json<Value>> {
    max_items("case_ids", &body.case_ids, 1000)?;
    st.run(move |st| {
        let mut db = st.db.connect()?;
        let tx = write_tx(&mut db)?;
        let row = target(&tx, user_id, Some("The Admin already sees every case."))?;
        let cases: BTreeSet<String> = check_cases(&tx, &body.case_ids)?.into_iter().collect();
        let before: BTreeSet<String> = {
            let mut stmt = tx.prepare("SELECT case_id FROM case_members WHERE user_id = ?")?;
            let it = stmt.query_map([user_id], |r| r.get(0))?;
            it.collect::<rusqlite::Result<_>>()?
        };
        for c in before.difference(&cases) {
            tx.execute("DELETE FROM case_members WHERE case_id = ? AND user_id = ?", (c, user_id))?;
        }
        for c in cases.difference(&before) {
            tx.execute("INSERT INTO case_members (case_id, user_id) VALUES (?, ?)", (c, user_id))?;
        }
        let name = |c: &str| -> rusqlite::Result<String> { tx.query_row("SELECT name FROM cases WHERE id = ?", [c], |r| r.get(0)) };
        for (list, verb) in [(cases.difference(&before), "added"), (before.difference(&cases), "removed")] {
            for c in list {
                let detail = format!("{verb} {}", row.username);
                audit::record(&tx, Entry { action: "case.members", user: Some(&admin), case_id: Some(c), target: &name(c)?, detail: &detail, ip: &ip, ..Default::default() })?;
            }
        }
        let v = view(&tx, st, &row)?;
        tx.commit()?;
        Ok(Json(v))
    })
    .await
}
