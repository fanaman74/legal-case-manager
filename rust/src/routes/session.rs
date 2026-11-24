//! Sign in, sign out, and change your own password.

use axum::extract::State;
use axum::http::{header, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use super::{max_len, Body};
use crate::accounts::{self, ClientIp, Session, User};
use crate::audit::{self, Entry};
use crate::db::write_tx;
use crate::state::AppState;
use crate::util::{self, ApiError, ApiResult};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Credentials {
    username: String,
    password: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PasswordChange {
    current_password: String,
    new_password: String,
}

fn session_view(user: &User, csrf: &str) -> Value {
    json!({"user": user.view(), "csrf": csrf})
}

fn cookie(token: &str, max_age: Option<i64>) -> String {
    let extra = max_age.map(|a| format!("; Max-Age={a}; expires=Thu, 01 Jan 1970 00:00:00 GMT")).unwrap_or_default();
    format!("{}={token}; HttpOnly; Path=/; SameSite=strict; Secure{extra}", accounts::COOKIE)
}

/// Whether the Admin account exists yet. The sign-in page uses this to send
/// people to the Control Center's setup wizard first.
pub async fn setup_status(State(st): State<AppState>) -> ApiResult<Json<Value>> {
    st.run(|st| {
        let ready = accounts::sync_admin(st).map_err(|e| {
            eprintln!("admin sync: {e}");
            ApiError::new(500, "The Admin account couldn't be read.")
        })?;
        Ok(Json(json!({"admin_ready": ready})))
    })
    .await
}

pub async fn get_session(sess: Session) -> Json<Value> {
    Json(session_view(&sess.user, &sess.csrf))
}

pub async fn sign_in(State(st): State<AppState>, ClientIp(ip): ClientIp, Body(body): Body<Credentials>) -> ApiResult<Response> {
    max_len("username", &body.username, 64)?;
    max_len("password", &body.password, 1024)?;
    st.run(move |st| {
        let keys = [format!("ip|{ip}"), format!("user|{}", body.username.to_lowercase())];
        let keys: Vec<&str> = keys.iter().map(String::as_str).collect();
        if st.throttle.locked(&keys) {
            return Err(ApiError::new(429, "Too many attempts. Try again in 15 minutes, or ask the Admin to unlock your account."));
        }
        if let Err(e) = accounts::sync_admin(st) {
            eprintln!("admin sync: {e}");
        }
        let mut db = st.db.connect()?;
        let user = accounts::user_by_name(&db, &body.username)?;
        let hash = user.as_ref().map(|u| u.password_hash.as_str()).unwrap_or(accounts::dummy_hash());
        let ok = accounts::verify_password(hash, &body.password) && user.is_some();
        let Some(user) = user.filter(|_| ok) else {
            st.throttle.fail(&keys);
            let shown: String = body.username.chars().take(64).collect();
            let tx = write_tx(&mut db)?;
            audit::record(&tx, Entry { action: "auth.sign_in", username: Some(&shown), outcome: Some("denied"), ip: &ip, detail: "wrong username or password", ..Default::default() })?;
            tx.commit()?;
            return Err(ApiError::new(401, "That username and password don't match. Check them and try again."));
        };
        if !user.active {
            let tx = write_tx(&mut db)?;
            audit::record(&tx, Entry { action: "auth.sign_in", user: Some(&user), outcome: Some("denied"), ip: &ip, detail: "account switched off", ..Default::default() })?;
            tx.commit()?;
            return Err(ApiError::new(403, "This account is switched off. Ask the Admin to switch it back on."));
        }
        st.throttle.clear(&keys);
        let tx = write_tx(&mut db)?;
        let (token, csrf) = accounts::start_session(&tx, user.id)?;
        tx.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (util::now(), user.id))?;
        audit::record(&tx, Entry { action: "auth.sign_in", user: Some(&user), ip: &ip, ..Default::default() })?;
        tx.commit()?;
        Ok(([(header::SET_COOKIE, cookie(&token, None))], Json(session_view(&user, &csrf))).into_response())
    })
    .await
}

pub async fn sign_out(State(st): State<AppState>, sess: Session) -> ApiResult<Response> {
    st.run(move |st| {
        let mut db = st.db.connect()?;
        let tx = write_tx(&mut db)?;
        tx.execute("DELETE FROM sessions WHERE token_hash = ?", [&sess.token_hash])?;
        audit::record(&tx, Entry { action: "auth.sign_out", user: Some(&sess.user), ip: &sess.ip, ..Default::default() })?;
        tx.commit()?;
        Ok((StatusCode::NO_CONTENT, [(header::SET_COOKIE, cookie("\"\"", Some(0)))]).into_response())
    })
    .await
}

pub async fn change_password(State(st): State<AppState>, sess: Session, Body(body): Body<PasswordChange>) -> ApiResult<Json<Value>> {
    max_len("current_password", &body.current_password, 1024)?;
    max_len("new_password", &body.new_password, 1024)?;
    st.run(move |st| {
        let user = &sess.user;
        if user.provisioned {
            return Err(ApiError::new(409, "The Admin password is changed in the Control Center on the host computer."));
        }
        if !accounts::verify_password(&user.password_hash, &body.current_password) {
            return Err(ApiError::new(400, "Your current password isn't right. Check it and try again."));
        }
        if util::chars(&body.new_password) < accounts::MIN_PASSWORD {
            return Err(ApiError::new(400, format!("Use at least {} characters. A short sentence works well.", accounts::MIN_PASSWORD)));
        }
        if body.new_password == body.current_password {
            return Err(ApiError::new(400, "Choose a password different from the current one."));
        }
        let hash = accounts::hash_password(&body.new_password);
        let mut db = st.db.connect()?;
        let tx = write_tx(&mut db)?;
        tx.execute("UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?", (&hash, user.id))?;
        // Other browsers signed in as this person are signed out.
        tx.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user.id, &sess.token_hash))?;
        audit::record(&tx, Entry { action: "auth.password_changed", user: Some(user), ip: &sess.ip, ..Default::default() })?;
        tx.commit()?;
        if user.is_admin() {
            let _ = std::fs::remove_file(accounts::first_run_note(st));
        }
        let mut u = user.clone();
        u.must_change_password = false;
        Ok(Json(session_view(&u, &sess.csrf)))
    })
    .await
}
