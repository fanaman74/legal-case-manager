//! Small helpers shared by the routes: errors in the API's JSON shape,
//! timestamps, random tokens and Python-compatible JSON.

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use base64::Engine;
use rand::RngCore;
use serde::Serialize;
use serde_json::{json, Value};

/// An error answer: `{"detail": ...}` with a status code, the shape the web
/// app reads (web/src/api.ts).
#[derive(Debug)]
pub struct ApiError {
    pub status: StatusCode,
    pub detail: Value,
}

impl ApiError {
    pub fn new(status: u16, detail: impl Into<String>) -> ApiError {
        ApiError { status: StatusCode::from_u16(status).unwrap(), detail: Value::String(detail.into()) }
    }
    pub fn with(status: u16, detail: Value) -> ApiError {
        ApiError { status: StatusCode::from_u16(status).unwrap(), detail }
    }
    /// A request whose body or query doesn't have the expected shape. The
    /// detail is a list, like FastAPI's, which the web app shows as "Some of
    /// the details aren't valid".
    pub fn invalid(msg: impl Into<String>) -> ApiError {
        ApiError::with(422, json!([{ "msg": msg.into() }]))
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.status, Json(json!({ "detail": self.detail }))).into_response()
    }
}

impl From<rusqlite::Error> for ApiError {
    fn from(e: rusqlite::Error) -> ApiError {
        eprintln!("database error: {e}");
        ApiError::new(500, "Something went wrong on the server. Try again, and tell the Admin if it keeps happening.")
    }
}

impl From<std::io::Error> for ApiError {
    fn from(e: std::io::Error) -> ApiError {
        eprintln!("file error: {e}");
        ApiError::new(500, "Something went wrong on the server. Try again, and tell the Admin if it keeps happening.")
    }
}

impl From<tokio::task::JoinError> for ApiError {
    fn from(e: tokio::task::JoinError) -> ApiError {
        eprintln!("task error: {e}");
        ApiError::new(500, "Something went wrong on the server. Try again, and tell the Admin if it keeps happening.")
    }
}

pub type ApiResult<T> = Result<T, ApiError>;

/// UTC time as ISO 8601 with milliseconds and Z, the format stored
/// everywhere in app.db.
pub fn now() -> String {
    chrono::Utc::now().format("%Y-%m-%dT%H:%M:%S%.3fZ").to_string()
}

pub fn unix_now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64()
}

/// Like Python's secrets.token_urlsafe(n).
pub fn token_urlsafe(n: usize) -> String {
    let mut buf = vec![0u8; n];
    rand::rngs::OsRng.fill_bytes(&mut buf);
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(buf)
}

pub fn token_hex(n: usize) -> String {
    let mut buf = vec![0u8; n];
    rand::rngs::OsRng.fill_bytes(&mut buf);
    hex::encode(buf)
}

pub fn new_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()
}

/// JSON as Python's json.dumps writes it by default (", " and ": " between
/// items, non-ASCII escaped), so values written by either version look the
/// same in app.db.
pub fn py_json<T: Serialize + ?Sized>(value: &T) -> String {
    let mut out = Vec::new();
    let mut ser = serde_json::Serializer::with_formatter(&mut out, PyFormatter);
    value.serialize(&mut ser).expect("serialisable");
    String::from_utf8(out).unwrap()
}

struct PyFormatter;

impl serde_json::ser::Formatter for PyFormatter {
    fn begin_array_value<W: ?Sized + std::io::Write>(&mut self, w: &mut W, first: bool) -> std::io::Result<()> {
        if first { Ok(()) } else { w.write_all(b", ") }
    }
    fn begin_object_key<W: ?Sized + std::io::Write>(&mut self, w: &mut W, first: bool) -> std::io::Result<()> {
        if first { Ok(()) } else { w.write_all(b", ") }
    }
    fn begin_object_value<W: ?Sized + std::io::Write>(&mut self, w: &mut W) -> std::io::Result<()> {
        w.write_all(b": ")
    }
    fn write_string_fragment<W: ?Sized + std::io::Write>(&mut self, w: &mut W, fragment: &str) -> std::io::Result<()> {
        for ch in fragment.chars() {
            if ch.is_ascii() {
                w.write_all(&[ch as u8])?;
            } else {
                let mut units = [0u16; 2];
                for u in ch.encode_utf16(&mut units) {
                    write!(w, "\\u{:04x}", u)?;
                }
            }
        }
        Ok(())
    }
}

/// "folder/name", or just "name" at the top of the case.
pub fn shown_path(folder: &str, name: &str) -> String {
    if folder.is_empty() { name.to_string() } else { format!("{folder}/{name}") }
}

/// Parse a JSON list stored as text, treating NULL or "" as empty.
pub fn json_list(text: Option<String>) -> Value {
    let t = text.unwrap_or_default();
    serde_json::from_str(if t.is_empty() { "[]" } else { &t }).unwrap_or_else(|_| json!([]))
}

/// Character count, which is what the Python version's length limits count.
pub fn chars(s: &str) -> usize {
    s.chars().count()
}

/// Python's str.strip(): removes whitespace at both ends.
pub fn strip(s: &str) -> String {
    s.trim().to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn python_json_format() {
        assert_eq!(py_json(&vec!["a", "é"]), r#"["a", "\u00e9"]"#);
        assert_eq!(py_json(&json!({"a": 1, "b": [1, 2]})), r#"{"a": 1, "b": [1, 2]}"#);
        assert_eq!(py_json(&Vec::<String>::new()), "[]");
        assert_eq!(py_json(&vec!["😀"]), r#"["\ud83d\ude00"]"#);
    }

    #[test]
    fn timestamps() {
        let t = now();
        assert_eq!(t.len(), 24);
        assert!(t.ends_with('Z'));
    }
}
