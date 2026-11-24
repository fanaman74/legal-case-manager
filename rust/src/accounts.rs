//! Sign-in, sessions and roles.
//!
//! Passwords are hashed with Argon2id at the same cost as the launcher, and
//! the hashes are the standard `$argon2id$...` strings, so hashes made by the
//! Python version or the launcher keep working. Sessions are random tokens in
//! an HttpOnly, Secure, SameSite=Strict cookie; only a SHA-256 of the token is
//! stored. Every request that changes something must carry the session's
//! CSRF token in a header and, when the browser sends one, an Origin matching
//! the app's own address.

use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use argon2::password_hash::{PasswordHash, PasswordHasher, PasswordVerifier, SaltString};
use argon2::{Algorithm, Argon2, Params, Version};
use axum::extract::{ConnectInfo, FromRequestParts};
use axum::http::request::Parts;
use axum::http::Method;
use rand::Rng;
use rusqlite::{Connection, OptionalExtension, Row};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;

use crate::audit::{self, Entry};
use crate::state::AppState;
use crate::util::{self, ApiError};

pub const COOKIE: &str = "cfm_session";
pub const CSRF_HEADER: &str = "x-csrf-token";
pub const SESSION_IDLE: f64 = 8.0 * 3600.0;
pub const SESSION_MAX: f64 = 7.0 * 24.0 * 3600.0;
pub const MIN_PASSWORD: usize = 12;

fn hasher() -> Argon2<'static> {
    // Same cost as the launcher (launcher/internal/auth/auth.go).
    Argon2::new(Algorithm::Argon2id, Version::V0x13, Params::new(64 * 1024, 3, 2, Some(32)).unwrap())
}

pub fn hash_password(password: &str) -> String {
    let salt = SaltString::generate(&mut rand::rngs::OsRng);
    hasher().hash_password(password.as_bytes(), &salt).expect("argon2").to_string()
}

pub fn verify_password(encoded: &str, password: &str) -> bool {
    match PasswordHash::new(encoded) {
        Ok(h) => Argon2::default().verify_password(password.as_bytes(), &h).is_ok(),
        Err(_) => false,
    }
}

/// Verified against when the username doesn't exist, so timing doesn't
/// reveal which usernames exist.
pub fn dummy_hash() -> &'static str {
    static DUMMY: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    DUMMY.get_or_init(|| hash_password(&util::token_hex(16)))
}

/// Same rule as the launcher: 2 to 64 letters, digits, . _ - @
pub fn valid_username(name: &str) -> bool {
    let n = name.chars().count();
    (2..=64).contains(&n) && name.chars().all(|c| c.is_ascii_alphanumeric() || "._-@".contains(c))
}

/// Four groups of four from an alphabet without look-alike characters, e.g.
/// 7kqm-3vxa-hn2p-wt9e.
pub fn temporary_password() -> String {
    const ALPHABET: &[u8] = b"abcdefghjkmnpqrstuvwxyz23456789";
    let mut rng = rand::rngs::OsRng;
    (0..4)
        .map(|_| (0..4).map(|_| ALPHABET[rng.gen_range(0..ALPHABET.len())] as char).collect::<String>())
        .collect::<Vec<_>>()
        .join("-")
}

pub fn token_hash(token: &str) -> String {
    hex::encode(Sha256::digest(token.as_bytes()))
}

#[derive(Clone, Debug)]
pub struct User {
    pub id: i64,
    pub username: String,
    pub display_name: String,
    pub role: String,
    pub password_hash: String,
    pub must_change_password: bool,
    pub active: bool,
    pub provisioned: bool,
    pub created_at: String,
    pub last_login_at: Option<String>,
}

impl User {
    pub const COLUMNS: &'static str =
        "u.id, u.username, u.display_name, u.role, u.password_hash, u.must_change_password, u.active, u.provisioned, u.created_at, u.last_login_at";

    /// Reads the columns in COLUMNS order, starting at `at`.
    pub fn from_row(r: &Row, at: usize) -> rusqlite::Result<User> {
        Ok(User {
            id: r.get(at)?,
            username: r.get(at + 1)?,
            display_name: r.get(at + 2)?,
            role: r.get(at + 3)?,
            password_hash: r.get(at + 4)?,
            must_change_password: r.get::<_, i64>(at + 5)? != 0,
            active: r.get::<_, i64>(at + 6)? != 0,
            provisioned: r.get::<_, i64>(at + 7)? != 0,
            created_at: r.get(at + 8)?,
            last_login_at: r.get(at + 9)?,
        })
    }

    pub fn is_admin(&self) -> bool {
        self.role == "admin"
    }

    pub fn view(&self) -> Value {
        json!({
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
            "must_change_password": self.must_change_password,
            "managed_by_control_center": self.provisioned,
        })
    }
}

pub fn user_by_id(db: &Connection, id: i64) -> rusqlite::Result<Option<User>> {
    db.query_row(&format!("SELECT {} FROM users u WHERE u.id = ?", User::COLUMNS), [id], |r| User::from_row(r, 0))
        .optional()
}

pub fn user_by_name(db: &Connection, name: &str) -> rusqlite::Result<Option<User>> {
    db.query_row(&format!("SELECT {} FROM users u WHERE u.username = ?", User::COLUMNS), [name], |r| User::from_row(r, 0))
        .optional()
}

// --- Admin account from the launcher -----------------------------------------

/// Create or update the Admin from the launcher's admin-account.json.
/// Returns whether an Admin exists.
pub fn sync_admin(st: &AppState) -> anyhow::Result<bool> {
    let from_file = std::fs::read_to_string(st.cfg.admin_account_file())
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        .and_then(|v| {
            let u = v.get("username")?.as_str()?.to_string();
            let h = v.get("password_hash")?.as_str()?.to_string();
            (valid_username(&u) && h.starts_with("$argon2id$")).then_some((u, h))
        });
    let mut db = st.db.connect()?;
    let current = db
        .query_row(&format!("SELECT {} FROM users u WHERE u.provisioned = 1", User::COLUMNS), [], |r| User::from_row(r, 0))
        .optional()?;
    let Some((username, pw_hash)) = from_file else {
        // Without the launcher, an Admin made on first start counts too.
        let any: i64 = db.query_row("SELECT COUNT(*) FROM users WHERE role = 'admin'", [], |r| r.get(0))?;
        return Ok(current.is_some() || any > 0);
    };
    if let Some(c) = &current {
        if c.username == username && c.password_hash == pw_hash {
            return Ok(true);
        }
    }
    let tx = crate::db::write_tx(&mut db)?;
    let current = tx
        .query_row(&format!("SELECT {} FROM users u WHERE u.provisioned = 1", User::COLUMNS), [], |r| User::from_row(r, 0))
        .optional()?;
    let clash: Option<i64> = tx
        .query_row("SELECT id FROM users WHERE username = ? AND provisioned = 0", [&username], |r| r.get(0))
        .optional()?;
    let entry = |detail| Entry { action: "admin.sync", username: Some("control-center"), target: &username, detail, ..Default::default() };
    if clash.is_some() {
        // Someone already has that username; keep the existing Admin rather
        // than merge two people.
        audit::record(&tx, Entry { outcome: Some("refused"), ..entry("username already used by another person") })?;
        tx.commit()?;
        return Ok(current.is_some());
    }
    if let Some(c) = current {
        tx.execute("UPDATE users SET username = ?, password_hash = ? WHERE id = ?", (&username, &pw_hash, c.id))?;
        if c.password_hash != pw_hash {
            tx.execute("DELETE FROM sessions WHERE user_id = ?", [c.id])?;
        }
        audit::record(&tx, entry("Admin account updated from the Control Center"))?;
    } else {
        tx.execute(
            "INSERT INTO users (username, display_name, role, password_hash, provisioned, created_at) VALUES (?, ?, 'admin', ?, 1, ?)",
            (&username, &username, &pw_hash, util::now()),
        )?;
        audit::record(&tx, entry("Admin account created in the Control Center"))?;
    }
    tx.commit()?;
    Ok(true)
}

/// When the program runs on its own (no launcher, so no Control Center
/// wizard yet) and nobody can sign in, it makes an Admin called "admin" with
/// a temporary password, shown in its window and saved in the data folder.
/// The first sign-in asks for a new password, then the note is removed.
pub fn first_run_admin(st: &AppState) -> anyhow::Result<Option<String>> {
    if sync_admin(st)? {
        return Ok(None);
    }
    let mut db = st.db.connect()?;
    let tx = crate::db::write_tx(&mut db)?;
    let admins: i64 = tx.query_row("SELECT COUNT(*) FROM users WHERE role = 'admin'", [], |r| r.get(0))?;
    if admins > 0 {
        return Ok(None);
    }
    let temp = temporary_password();
    tx.execute(
        "INSERT INTO users (username, display_name, role, password_hash, must_change_password, created_at) VALUES ('admin', 'Admin', 'admin', ?, 1, ?)",
        (hash_password(&temp), util::now()),
    )?;
    audit::record(&tx, Entry { action: "admin.first_run", username: Some("casefiles"), target: "admin", detail: "Admin account made on first start", ..Default::default() })?;
    tx.commit()?;
    std::fs::write(
        first_run_note(st),
        format!("Sign in to Case File Manager as\r\n\r\n  username: admin\r\n  password: {temp}\r\n\r\nYou'll be asked to choose your own password. This note is removed when you do.\r\n"),
    )?;
    Ok(Some(temp))
}

pub fn first_run_note(st: &AppState) -> std::path::PathBuf {
    st.cfg.data_dir.join("FIRST-SIGN-IN.txt")
}

// --- Sessions ----------------------------------------------------------------

pub fn start_session(db: &Connection, user_id: i64) -> rusqlite::Result<(String, String)> {
    let (token, csrf) = (util::token_urlsafe(32), util::token_urlsafe(32));
    let now = util::unix_now();
    db.execute("DELETE FROM sessions WHERE last_seen < ? OR created_at < ?", (now - SESSION_IDLE, now - SESSION_MAX))?;
    db.execute(
        "INSERT INTO sessions (token_hash, user_id, csrf, created_at, last_seen) VALUES (?, ?, ?, ?, ?)",
        (token_hash(&token), user_id, &csrf, now, now),
    )?;
    Ok((token, csrf))
}

#[derive(Clone, Debug)]
pub struct Session {
    pub user: User,
    pub csrf: String,
    pub token_hash: String,
    pub ip: String,
}

fn lookup(st: &AppState, token: &str) -> rusqlite::Result<Option<(User, String)>> {
    let th = token_hash(token);
    let now = util::unix_now();
    let db = st.db.connect()?;
    let row = db
        .query_row(
            &format!("SELECT s.csrf, s.created_at, s.last_seen, {} FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?", User::COLUMNS),
            [&th],
            |r| Ok((r.get::<_, String>(0)?, r.get::<_, f64>(1)?, r.get::<_, f64>(2)?, User::from_row(r, 3)?)),
        )
        .optional()?;
    let Some((csrf, created, seen, user)) = row else { return Ok(None) };
    if !user.active || now - seen > SESSION_IDLE || now - created > SESSION_MAX {
        db.execute("DELETE FROM sessions WHERE token_hash = ?", [&th])?;
        return Ok(None);
    }
    if now - seen > 60.0 {
        db.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now, &th))?;
    }
    Ok(Some((user, csrf)))
}

pub fn cookie_value(parts: &Parts, name: &str) -> Option<String> {
    for header in parts.headers.get_all(axum::http::header::COOKIE) {
        let Ok(text) = header.to_str() else { continue };
        for pair in text.split(';') {
            if let Some((k, v)) = pair.trim().split_once('=') {
                if k == name {
                    return Some(v.to_string());
                }
            }
        }
    }
    None
}

pub fn client_ip(parts: &Parts) -> String {
    parts
        .extensions
        .get::<ConnectInfo<SocketAddr>>()
        .map(|c| crate::web::canonical_ip(c.0.ip()).to_string())
        .unwrap_or_default()
}

fn same_origin(parts: &Parts) -> bool {
    let Some(origin) = parts.headers.get("origin") else {
        return true; // not sent by every browser on same-origin requests; the CSRF token is still required
    };
    let origin = origin.to_str().unwrap_or("").to_lowercase();
    // HTTP/2 browsers send the address as :authority, not a Host header.
    let host = parts
        .headers
        .get("host")
        .and_then(|h| h.to_str().ok())
        .map(str::to_string)
        .or_else(|| parts.uri.authority().map(|a| a.to_string()))
        .unwrap_or_default()
        .to_lowercase();
    let rest = origin.strip_prefix("https://").or_else(|| origin.strip_prefix("http://"));
    matches!(rest, Some(netloc) if netloc == host)
}

/// The signed-in session, with CSRF and Origin checks on every request that
/// changes something.
impl FromRequestParts<AppState> for Session {
    type Rejection = ApiError;

    async fn from_request_parts(parts: &mut Parts, st: &AppState) -> Result<Session, ApiError> {
        let token = cookie_value(parts, COOKIE).unwrap_or_default();
        let found = if token.is_empty() {
            None
        } else {
            let st2 = st.clone();
            tokio::task::spawn_blocking(move || lookup(&st2, &token)).await??
        };
        let Some((user, csrf)) = found else {
            return Err(ApiError::new(401, "Sign in to continue."));
        };
        if parts.method != Method::GET && parts.method != Method::HEAD {
            let sent = parts.headers.get(CSRF_HEADER).and_then(|v| v.to_str().ok()).unwrap_or("");
            let ok = same_origin(parts) && !sent.is_empty() && bool::from(sent.as_bytes().ct_eq(csrf.as_bytes()));
            if !ok {
                return Err(ApiError::new(403, "This request didn't come from the app. Reload the page and try again."));
            }
        }
        let th = token_hash(&cookie_value(parts, COOKIE).unwrap_or_default());
        Ok(Session { user, csrf, token_hash: th, ip: client_ip(parts) })
    }
}

/// The signed-in user, who has already chosen their own password.
pub struct CurrentUser(pub User, pub String);

impl FromRequestParts<AppState> for CurrentUser {
    type Rejection = ApiError;

    async fn from_request_parts(parts: &mut Parts, st: &AppState) -> Result<CurrentUser, ApiError> {
        let s = Session::from_request_parts(parts, st).await?;
        if s.user.must_change_password {
            return Err(ApiError::with(
                403,
                json!({"code": "password_change_required", "message": "Choose your own password to continue."}),
            ));
        }
        Ok(CurrentUser(s.user, s.ip))
    }
}

pub struct AdminUser(pub User, pub String);

impl FromRequestParts<AppState> for AdminUser {
    type Rejection = ApiError;

    async fn from_request_parts(parts: &mut Parts, st: &AppState) -> Result<AdminUser, ApiError> {
        let CurrentUser(u, ip) = CurrentUser::from_request_parts(parts, st).await?;
        if !u.is_admin() {
            return Err(ApiError::new(403, "Only the Admin can do this."));
        }
        Ok(AdminUser(u, ip))
    }
}

/// The caller's address, for routes that don't need a session.
pub struct ClientIp(pub String);

impl<S: Send + Sync> FromRequestParts<S> for ClientIp {
    type Rejection = std::convert::Infallible;

    async fn from_request_parts(parts: &mut Parts, _: &S) -> Result<ClientIp, Self::Rejection> {
        Ok(ClientIp(client_ip(parts)))
    }
}

// --- Sign-in throttle ----------------------------------------------------------

pub const MAX_FAILURES: usize = 5;
pub const WINDOW: Duration = Duration::from_secs(15 * 60);

/// After MAX_FAILURES wrong passwords within WINDOW, from one address or for
/// one username, further attempts are refused until the window passes or
/// the Admin unlocks the account.
#[derive(Default)]
pub struct Throttle {
    fails: Mutex<HashMap<String, Vec<Instant>>>,
}

impl Throttle {
    fn recent(map: &mut HashMap<String, Vec<Instant>>, key: &str) -> usize {
        let now = Instant::now();
        if let Some(list) = map.get_mut(key) {
            list.retain(|t| now.duration_since(*t) < WINDOW);
            if list.is_empty() {
                map.remove(key);
                return 0;
            }
            return list.len();
        }
        0
    }

    pub fn locked(&self, keys: &[&str]) -> bool {
        let mut map = self.fails.lock().unwrap();
        keys.iter().any(|k| Self::recent(&mut map, k) >= MAX_FAILURES)
    }

    pub fn fail(&self, keys: &[&str]) {
        let mut map = self.fails.lock().unwrap();
        for k in keys {
            Self::recent(&mut map, k);
            map.entry(k.to_string()).or_default().push(Instant::now());
        }
    }

    pub fn clear(&self, keys: &[&str]) {
        let mut map = self.fails.lock().unwrap();
        for k in keys {
            map.remove(*k);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Made by the launcher's own hashPassword; the app must accept it.
    #[test]
    fn accepts_launcher_hash() {
        let h = "$argon2id$v=19$m=65536,t=3,p=2$e0zzmps31YpqqdvJ6EwGQw$bxZ67h68vt0+99cnhQ/cpDKMPg9BbNEEvUJSVU8abjc";
        assert!(verify_password(h, "correct horse battery"));
        assert!(!verify_password(h, "wrong"));
        assert!(!verify_password("nonsense", "x"));
    }

    #[test]
    fn new_hashes_use_launcher_cost() {
        let h = hash_password("a long password here");
        assert!(h.starts_with("$argon2id$v=19$m=65536,t=3,p=2$"));
        assert!(verify_password(&h, "a long password here"));
    }

    #[test]
    fn usernames_and_temporary_passwords() {
        assert!(valid_username("fred"));
        assert!(valid_username("a.b-c_d@e"));
        assert!(!valid_username("a"));
        assert!(!valid_username("bad name"));
        assert!(!valid_username("café"));
        let t = temporary_password();
        assert_eq!(t.len(), 19);
        assert_eq!(t.matches('-').count(), 3);
    }

    #[test]
    fn throttle_locks_after_five() {
        let t = Throttle::default();
        for _ in 0..4 {
            t.fail(&["ip|1", "user|a"]);
        }
        assert!(!t.locked(&["user|a"]));
        t.fail(&["ip|1", "user|a"]);
        assert!(t.locked(&["user|a"]));
        t.clear(&["user|a"]);
        assert!(!t.locked(&["user|a"]));
        assert!(t.locked(&["ip|1"]));
    }
}
