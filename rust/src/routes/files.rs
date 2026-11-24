//! Files in a case: upload, list, folders, details, tags, download, and the
//! case's progress and processing status.
//!
//! An upload is one file per request, with the file's bytes as the request
//! body and its path inside the uploaded folder in `?path=`. The body is
//! streamed to disk and hashed as it arrives; nothing is held in memory.

use std::collections::{BTreeMap, HashMap};

use axum::extract::{Path, Request, State};
use axum::http::{header, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use futures_util::StreamExt;
use percent_encoding::{utf8_percent_encode, AsciiSet, NON_ALPHANUMERIC};
use rusqlite::{Connection, OptionalExtension, Row};
use serde::Deserialize;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use tokio::io::AsyncWriteExt;

use super::{in_range, max_items, max_len, Body, Params};
use crate::access::{self, Case};
use crate::accounts::{CurrentUser, User};
use crate::audit::{self, Entry};
use crate::db::write_tx;
use crate::jobs;
use crate::state::AppState;
use crate::util::{self, json_list, py_json, shown_path, ApiError, ApiResult};

pub fn source_type(ext: &str) -> Option<&'static str> {
    Some(match ext {
        ".docx" => "word",
        ".pdf" => "pdf",
        ".msg" | ".eml" => "email",
        ".txt" => "text",
        ".png" | ".jpg" | ".jpeg" | ".tif" | ".tiff" => "image",
        _ => return None,
    })
}

const SUPPORTED: &str = ".docx, .pdf, .msg, .eml, .txt, .png, .jpg, .jpeg, .tif, .tiff";

/// Split an uploaded relative path into (folder, name). Folders use forward
/// slashes. Refuses anything that tries to climb out of the upload.
pub fn split_path(raw: &str) -> Option<(String, String)> {
    let replaced = raw.replace('\\', "/");
    let mut parts: Vec<&str> = replaced.split('/').collect();
    while parts.first() == Some(&"") && !parts.is_empty() {
        parts.remove(0);
    }
    if parts.is_empty() || util::chars(raw) > 1024 {
        return None;
    }
    for p in &parts {
        if p.is_empty() || *p == "." || *p == ".." || util::chars(p) > 255 || p.chars().any(|c| (c as u32) < 32 || c == '\x7f') {
            return None;
        }
    }
    let name = parts.pop().unwrap().to_string();
    Some((parts.join("/"), name))
}

pub fn extension(name: &str) -> String {
    match name.rfind('.') {
        Some(dot) if dot > 0 => name[dot..].to_lowercase(),
        _ => String::new(),
    }
}

const FILE_SELECT: &str = "SELECT f.id, f.name, f.folder, f.source_type, f.ext, f.size, f.sha256, f.status, f.tags, f.added_at,
  u.display_name, f.parent_id, f.page_count, f.ocr, f.warnings, f.notes, f.error, f.ocr_state, f.ocr_error, f.scanned_pages,
  f.doc_date, f.title, f.converted_at, f.previews, f.index_state, f.passages, f.meta, f.near_dup_of, f.near_dup_score,
  nd.name, nd.folder, f.case_id, f.markdown_sha256
  FROM files f LEFT JOIN users u ON u.id = f.added_by LEFT JOIN files nd ON nd.id = f.near_dup_of";

/// A file row as the API shows it, plus what the routes need.
pub struct FileRow {
    pub view: Value,
    pub id: String,
    pub name: String,
    pub folder: String,
    pub ext: String,
    pub source_type: String,
    pub status: String,
    pub tags: Value,
    pub ocr: bool,
    pub scanned_pages: Value,
    pub converted_at: Option<String>,
    pub previews: i64,
    pub case_id: String,
    pub markdown_sha256: Option<String>,
}

impl FileRow {
    pub fn shown(&self) -> String {
        shown_path(&self.folder, &self.name)
    }
}

fn file_row(r: &Row) -> rusqlite::Result<FileRow> {
    let s = |i| r.get::<_, String>(i);
    let source_type = s(3)?;
    let meta: Value = serde_json::from_str(&r.get::<_, Option<String>>(26)?.filter(|m| !m.is_empty()).unwrap_or_else(|| "{}".into()))
        .unwrap_or_else(|_| json!({}));
    let meta_empty = meta.as_object().map(|m| m.is_empty()).unwrap_or(true);
    let email = if source_type == "email" && !meta_empty {
        let mut m = Map::new();
        for k in ["from", "to", "cc", "date", "subject"] {
            m.insert(k.into(), meta.get(k).cloned().unwrap_or(Value::Null));
        }
        Value::Object(m)
    } else {
        Value::Null
    };
    let near_of: Option<String> = r.get(27)?;
    let near = match &near_of {
        Some(of) => {
            let score: f64 = r.get::<_, Option<f64>>(28)?.unwrap_or(0.0);
            let name = match r.get::<_, Option<String>>(29)? {
                Some(n) => json!(shown_path(&r.get::<_, Option<String>>(30)?.unwrap_or_default(), &n)),
                None => Value::Null,
            };
            json!({"of": of, "name": name, "similarity": (score * 100.0).round() / 100.0})
        }
        None => Value::Null,
    };
    let tags = json_list(r.get(8)?);
    let scanned = json_list(r.get(19)?);
    let ocr = r.get::<_, i64>(13)? != 0;
    let converted_at: Option<String> = r.get(22)?;
    let previews: i64 = r.get::<_, Option<i64>>(23)?.unwrap_or(0);
    let view = json!({
        "id": s(0)?, "name": s(1)?, "folder": s(2)?, "type": source_type, "ext": s(4)?, "size": r.get::<_, i64>(5)?,
        "sha256": s(6)?, "status": s(7)?, "tags": tags, "added_at": s(9)?, "added_by": r.get::<_, Option<String>>(10)?,
        "parent_id": r.get::<_, Option<String>>(11)?, "page_count": r.get::<_, Option<i64>>(12)?, "ocr": ocr,
        "warnings": json_list(r.get(14)?), "notes": json_list(r.get(15)?), "error": s(16)?, "ocr_state": s(17)?,
        "ocr_error": s(18)?, "scanned_pages": scanned, "doc_date": r.get::<_, Option<String>>(20)?,
        "title": r.get::<_, Option<String>>(21)?, "converted_at": converted_at, "previews": previews,
        "search": {"state": s(24)?, "passages": r.get::<_, i64>(25)?},
        "email": email, "attachments": meta.get("attachments").cloned().unwrap_or_else(|| json!([])),
        "near_duplicate": near,
    });
    Ok(FileRow {
        id: s(0)?,
        name: s(1)?,
        folder: s(2)?,
        ext: s(4)?,
        source_type: s(3)?,
        status: s(7)?,
        tags,
        ocr,
        scanned_pages: scanned,
        converted_at,
        previews,
        case_id: s(31)?,
        markdown_sha256: r.get(32)?,
        view,
    })
}

pub fn load_file(db: &Connection, file_id: &str) -> rusqlite::Result<Option<FileRow>> {
    db.query_row(&format!("{FILE_SELECT} WHERE f.id = ?"), [file_id], file_row).optional()
}

fn visible_file(db: &Connection, user: &User, case_id: &str, file_id: &str) -> ApiResult<(Case, FileRow)> {
    let case = access::visible_case(db, user, case_id)?;
    let row = db
        .query_row(&format!("{FILE_SELECT} WHERE f.id = ? AND f.case_id = ?"), [file_id, case_id], file_row)
        .optional()?
        .ok_or_else(|| ApiError::new(404, "That file isn't in this case."))?;
    Ok((case, row))
}

// --- Upload --------------------------------------------------------------------

#[derive(Deserialize)]
pub struct UploadQuery {
    path: String,
}

fn too_large(limit: u64) -> ApiError {
    let gb = 1u64 << 30;
    let size = if limit >= gb { format!("{} GB", fmt_g(limit as f64 / gb as f64)) } else { format!("{} MB", fmt_g(limit as f64 / (1u64 << 20) as f64)) };
    ApiError::new(413, format!("This file is larger than the {size} limit for one file."))
}

/// Python's "{:g}" for the sizes shown in messages.
fn fmt_g(v: f64) -> String {
    let s = format!("{v:.6}");
    let s = s.trim_end_matches('0').trim_end_matches('.');
    s.to_string()
}

struct TempFile(std::path::PathBuf);

impl Drop for TempFile {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

pub async fn upload(State(st): State<AppState>, CurrentUser(user, ip): CurrentUser, Path(case_id): Path<String>, Params(q): Params<UploadQuery>, req: Request) -> ApiResult<Response> {
    {
        let (user, case_id) = (user.clone(), case_id.clone());
        st.run(move |st| {
            let db = st.db.connect()?;
            let case = access::visible_case(&db, &user, &case_id)?;
            access::require_edit(&user, &case)
        })
        .await?;
    }
    let Some((folder, name)) = split_path(&q.path) else {
        return Err(ApiError::new(400, "That file name or folder can't be used. Rename it and try again."));
    };
    let ext = extension(&name);
    let Some(kind) = source_type(&ext) else {
        let (user, case_id, ip, shown) = (user.clone(), case_id.clone(), ip.clone(), shown_path(&folder, &name));
        st.run(move |st| {
            let mut db = st.db.connect()?;
            let tx = write_tx(&mut db)?;
            audit::record(&tx, Entry { action: "file.rejected", user: Some(&user), case_id: Some(&case_id), target: &shown, detail: "unsupported type", outcome: Some("refused"), ip: &ip, ..Default::default() })?;
            tx.commit()?;
            Ok(())
        })
        .await?;
        let what = if ext.is_empty() { "Files without an extension".to_string() } else { ext };
        return Ok((
            StatusCode::UNSUPPORTED_MEDIA_TYPE,
            Json(json!({"result": "unsupported", "detail": format!("{what} isn't supported. Supported types: {SUPPORTED}.")})),
        )
            .into_response());
    };
    let limit = st.cfg.max_upload_bytes;
    if let Some(declared) = req.headers().get(header::CONTENT_LENGTH).and_then(|v| v.to_str().ok()).and_then(|v| v.parse::<u64>().ok()) {
        if declared > limit {
            return Err(too_large(limit));
        }
    }
    let tmp = TempFile(st.storage.incoming(&case_id)?);
    let mut out = tokio::fs::File::create(&tmp.0).await?;
    let mut digest = Sha256::new();
    let mut size: u64 = 0;
    let mut stream = req.into_body().into_data_stream();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|_| ApiError::new(400, "The upload was cut off. Try again."))?;
        size += chunk.len() as u64;
        if size > limit {
            return Err(too_large(limit));
        }
        digest.update(&chunk);
        out.write_all(&chunk).await?;
    }
    out.flush().await?;
    drop(out);
    let sha = hex::encode(digest.finalize());
    st.run(move |st| store(st, &user, &ip, &case_id, &folder, &name, &ext, kind, size, &sha, tmp)).await
}

#[allow(clippy::too_many_arguments)]
fn store(st: &AppState, user: &User, ip: &str, case_id: &str, folder: &str, name: &str, ext: &str, kind: &str, size: u64, sha: &str, tmp: TempFile) -> ApiResult<Response> {
    let shown = shown_path(folder, name);
    let file_id = util::new_id();
    let mut db = st.db.connect()?;
    let tx = write_tx(&mut db)?;
    let case = access::visible_case(&tx, user, case_id)?; // the case may have been archived or deleted meanwhile
    access::require_edit(user, &case)?;
    let existing = tx.query_row(&format!("{FILE_SELECT} WHERE f.case_id = ? AND f.sha256 = ?"), [case_id, sha], file_row).optional()?;
    match existing {
        None => {
            tx.execute(
                "INSERT INTO files (id, case_id, name, folder, ext, source_type, size, sha256, status, added_at, added_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
                rusqlite::params![file_id, case_id, name, folder, ext, kind, size as i64, sha, util::now(), user.id],
            )?;
            let detail = format!("sha256 {sha}");
            audit::record(&tx, Entry { action: "file.upload", user: Some(user), case_id: Some(case_id), target: &shown, detail: &detail, ip, ..Default::default() })?;
            jobs::enqueue(&tx, "convert", Some(case_id), Some(&file_id), None, Some(user))?;
            // Moved into place last, before the commit: if the move fails, the record is rolled back.
            st.storage.keep(&tmp.0, case_id, &file_id, ext)?;
            let row = load_file(&tx, &file_id)?.unwrap();
            tx.commit()?;
            Ok((StatusCode::CREATED, Json(json!({"result": "stored", "file": row.view}))).into_response())
        }
        Some(existing) => {
            let detail = format!("same content as {}", existing.shown());
            audit::record(&tx, Entry { action: "file.duplicate", user: Some(user), case_id: Some(case_id), target: &shown, detail: &detail, outcome: Some("refused"), ip, ..Default::default() })?;
            tx.commit()?;
            Ok((
                StatusCode::CONFLICT,
                Json(json!({"result": "duplicate", "detail": "This file is already in the case, so it wasn't added again.", "existing": existing.view})),
            )
                .into_response())
        }
    }
}

// --- List, folders, details ------------------------------------------------------

#[derive(Deserialize)]
pub struct ListQuery {
    q: Option<String>,
    folder: Option<String>,
    #[serde(rename = "type")]
    kind: Option<String>,
    status: Option<String>,
    tag: Option<String>,
    sort: Option<String>,
    order: Option<String>,
    limit: Option<i64>,
    offset: Option<i64>,
}

pub async fn list_files(State(st): State<AppState>, CurrentUser(user, _): CurrentUser, Path(case_id): Path<String>, Params(q): Params<ListQuery>) -> ApiResult<Json<Value>> {
    let text = q.q.unwrap_or_default();
    max_len("q", &text, 200)?;
    let sort = match q.sort.as_deref().unwrap_or("added") {
        "name" => "name COLLATE NOCASE",
        "folder" => "folder COLLATE NOCASE",
        "type" => "source_type",
        "size" => "size",
        "added" => "added_at",
        _ => return Err(ApiError::invalid("sort: name, folder, type, size or added")),
    };
    let order = match q.order.as_deref().unwrap_or("desc") {
        "asc" => "ASC",
        "desc" => "DESC",
        _ => return Err(ApiError::invalid("order: asc or desc")),
    };
    if let Some(k) = &q.kind {
        if !["word", "pdf", "email", "text", "image"].contains(&k.as_str()) {
            return Err(ApiError::invalid("type: word, pdf, email, text or image"));
        }
    }
    let limit = q.limit.unwrap_or(200);
    in_range("limit", limit, 1, 1000)?;
    let offset = q.offset.unwrap_or(0);
    in_range("offset", offset, 0, i64::MAX)?;
    st.run(move |st| {
        let mut where_ = vec!["f.case_id = ?".to_string()];
        let mut args: Vec<rusqlite::types::Value> = vec![case_id.clone().into()];
        if !text.is_empty() {
            where_.push("f.name LIKE ? ESCAPE '\\'".into());
            args.push(format!("%{}%", text.replace('\\', "\\\\").replace('%', "\\%").replace('_', "\\_")).into());
        }
        if let Some(f) = q.folder.as_deref().map(|f| f.trim_matches('/')).filter(|f| !f.is_empty()) {
            where_.push("(f.folder = ? OR substr(f.folder, 1, ?) = ?)".into());
            args.push(f.to_string().into());
            args.push(((util::chars(f) + 1) as i64).into());
            args.push(format!("{f}/").into());
        }
        if let Some(k) = &q.kind {
            where_.push("f.source_type = ?".into());
            args.push(k.clone().into());
        }
        match q.status.as_deref() {
            None | Some("") => {}
            Some("warnings") => where_.push("f.warnings != '[]'".into()),
            Some("scanned") => where_.push("f.scanned_pages != '[]'".into()),
            Some("ocr") => where_.push("f.ocr_state != ''".into()),
            Some("near_duplicate") => where_.push("f.near_dup_of IS NOT NULL".into()),
            Some(s) => {
                where_.push("f.status = ?".into());
                args.push(s.to_string().into());
            }
        }
        if let Some(t) = q.tag.as_deref().filter(|t| !t.is_empty()) {
            where_.push("EXISTS (SELECT 1 FROM json_each(f.tags) WHERE value = ?)".into());
            args.push(t.to_string().into());
        }
        let clause = where_.join(" AND ");
        let db = st.db.connect()?;
        access::visible_case(&db, &user, &case_id)?;
        let total: i64 = db.query_row(&format!("SELECT COUNT(*) FROM files f WHERE {clause}"), rusqlite::params_from_iter(args.iter()), |r| r.get(0))?;
        args.push(limit.into());
        args.push(offset.into());
        let mut stmt = db.prepare(&format!("{FILE_SELECT} WHERE {clause} ORDER BY f.{sort} {order}, f.id LIMIT ? OFFSET ?"))?;
        let items = stmt
            .query_map(rusqlite::params_from_iter(args.iter()), file_row)?
            .map(|r| r.map(|f| f.view))
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(Json(json!({"items": items, "total": total})))
    })
    .await
}

/// Every folder with the number of files directly in it. The browser builds
/// the tree from the paths.
pub async fn folders(State(st): State<AppState>, CurrentUser(user, _): CurrentUser, Path(case_id): Path<String>) -> ApiResult<Json<Value>> {
    st.run(move |st| {
        let db = st.db.connect()?;
        access::visible_case(&db, &user, &case_id)?;
        let mut stmt = db.prepare("SELECT folder, COUNT(*) FROM files WHERE case_id = ? GROUP BY folder ORDER BY folder")?;
        let items = stmt
            .query_map([&case_id], |r| Ok(json!({"path": r.get::<_, String>(0)?, "files": r.get::<_, i64>(1)?})))?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(Json(json!({"items": items})))
    })
    .await
}

pub async fn get_file(State(st): State<AppState>, CurrentUser(user, _): CurrentUser, Path((case_id, file_id)): Path<(String, String)>) -> ApiResult<Json<Value>> {
    st.run(move |st| {
        let db = st.db.connect()?;
        Ok(Json(visible_file(&db, &user, &case_id, &file_id)?.1.view))
    })
    .await
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FileEdit {
    tags: Vec<String>,
}

pub async fn edit_file(State(st): State<AppState>, CurrentUser(user, ip): CurrentUser, Path((case_id, file_id)): Path<(String, String)>, Body(body): Body<FileEdit>) -> ApiResult<Json<Value>> {
    max_items("tags", &body.tags, 50)?;
    let mut tags: Vec<String> = Vec::new();
    for t in &body.tags {
        let t = t.split_whitespace().collect::<Vec<_>>().join(" ");
        if t.is_empty() || util::chars(&t) > 50 {
            return Err(ApiError::new(400, "Tags must be 1 to 50 characters."));
        }
        if !tags.contains(&t) {
            tags.push(t);
        }
    }
    st.run(move |st| {
        let mut db = st.db.connect()?;
        let tx = write_tx(&mut db)?;
        let (case, row) = visible_file(&tx, &user, &case_id, &file_id)?;
        access::require_edit(&user, &case)?;
        if row.tags != json!(tags) {
            tx.execute("UPDATE files SET tags = ? WHERE id = ?", (py_json(&tags), &file_id))?;
            let detail = if tags.is_empty() { "(none)".to_string() } else { tags.join(", ") };
            audit::record(&tx, Entry { action: "file.tags", user: Some(&user), case_id: Some(&case_id), target: &row.shown(), detail: &detail, ip: &ip, ..Default::default() })?;
        }
        let row = load_file(&tx, &file_id)?.unwrap();
        tx.commit()?;
        Ok(Json(row.view))
    })
    .await
}

/// Characters Python's urllib.parse.quote leaves alone (plus '/').
const QUOTE_SAFE: &AsciiSet = &NON_ALPHANUMERIC.remove(b'_').remove(b'.').remove(b'-').remove(b'~').remove(b'/');

fn attachment(name: &str) -> String {
    let quoted = utf8_percent_encode(name, QUOTE_SAFE).to_string();
    if quoted != name {
        format!("attachment; filename*=utf-8''{quoted}")
    } else {
        format!("attachment; filename=\"{name}\"")
    }
}

/// The original, byte for byte, always as a download: uploaded files are
/// untrusted, so the browser never renders them on the app's own address.
pub async fn download(State(st): State<AppState>, CurrentUser(user, ip): CurrentUser, Path((case_id, file_id)): Path<(String, String)>) -> ApiResult<Response> {
    let (path, name) = st
        .run(move |st| {
            let mut db = st.db.connect()?;
            let tx = write_tx(&mut db)?;
            let (_, row) = visible_file(&tx, &user, &case_id, &file_id)?;
            let path = st.storage.original(&case_id, &file_id, &row.ext)?;
            if !path.is_file() {
                return Err(ApiError::new(410, "The stored copy of this file is missing from the data folder. Restore it from a backup."));
            }
            audit::record(&tx, Entry { action: "file.download", user: Some(&user), case_id: Some(&case_id), target: &row.shown(), ip: &ip, ..Default::default() })?;
            tx.commit()?;
            Ok((path, row.name))
        })
        .await?;
    let file = tokio::fs::File::open(&path).await?;
    let len = file.metadata().await?.len();
    Ok((
        [
            (header::CONTENT_TYPE, "application/octet-stream".to_string()),
            (header::CONTENT_DISPOSITION, attachment(&name)),
            (header::CONTENT_LENGTH, len.to_string()),
            (header::CONTENT_SECURITY_POLICY, "sandbox".to_string()),
            (header::CACHE_CONTROL, "no-store".to_string()),
        ],
        axum::body::Body::from_stream(tokio_util::io::ReaderStream::new(file)),
    )
        .into_response())
}

// --- Conversion requests (the worker does the work) -------------------------------

/// Whether Tesseract is installed (the launcher sets TESSERACT_CMD).
pub fn ocr_available() -> bool {
    let cmd = std::env::var("TESSERACT_CMD").ok().filter(|c| !c.is_empty()).unwrap_or_else(|| "tesseract".into());
    which::which(cmd).is_ok()
}

fn wants_ocr(row: &FileRow) -> bool {
    row.source_type == "pdf" && (row.status == "converted" || row.status == "needs_ocr") && row.scanned_pages != json!([])
}

/// Queue reading a file's scanned pages, or note that OCR isn't installed yet
/// (the worker tries again once it is).
pub fn queue_ocr(db: &Connection, row: &FileRow, user: Option<&User>) -> rusqlite::Result<&'static str> {
    let state = if !ocr_available() {
        "unavailable"
    } else {
        jobs::enqueue(db, "ocr", Some(&row.case_id), Some(&row.id), Some(json!({"ocr": true})), user)?;
        "waiting"
    };
    db.execute("UPDATE files SET ocr_state = ?, ocr_error = '' WHERE id = ?", (state, &row.id))?;
    Ok(state)
}

fn queue_convert(db: &Connection, user: &User, row: &FileRow, ocr: bool, ip: &str) -> rusqlite::Result<()> {
    if ocr && wants_ocr(row) {
        // Read the scanned pages in the background; the file stays usable.
        queue_ocr(db, row, Some(user))?;
    } else {
        // A file read with OCR before is read with OCR again.
        jobs::enqueue(db, "convert", Some(&row.case_id), Some(&row.id), Some(json!({"ocr": ocr || row.ocr})), Some(user))?;
        db.execute("UPDATE files SET status = 'queued', error = '' WHERE id = ?", [&row.id])?;
    }
    let action = if ocr { "file.ocr" } else { "file.reconvert" };
    audit::record(db, Entry { action, user: Some(user), case_id: Some(&row.case_id), target: &row.shown(), ip, ..Default::default() })
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Convert {
    #[serde(default)]
    ocr: bool,
}

/// Convert a file again (after a failure, or with OCR for scanned pages).
pub async fn reconvert(State(st): State<AppState>, CurrentUser(user, ip): CurrentUser, Path((case_id, file_id)): Path<(String, String)>, Body(body): Body<Convert>) -> ApiResult<Json<Value>> {
    st.run(move |st| {
        let mut db = st.db.connect()?;
        let tx = write_tx(&mut db)?;
        let (case, row) = visible_file(&tx, &user, &case_id, &file_id)?;
        access::require_edit(&user, &case)?;
        if body.ocr && row.source_type != "pdf" {
            return Err(ApiError::new(400, "OCR can be run on PDFs. Images are always read with OCR."));
        }
        queue_convert(&tx, &user, &row, body.ocr, &ip)?;
        let row = load_file(&tx, &file_id)?.unwrap();
        tx.commit()?;
        Ok(Json(row.view))
    })
    .await
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Bulk {
    what: String,
}

/// Run OCR on every scanned PDF in the case, or retry every failed file.
pub async fn bulk_convert(State(st): State<AppState>, CurrentUser(user, ip): CurrentUser, Path(case_id): Path<String>, Body(body): Body<Bulk>) -> ApiResult<Json<Value>> {
    if body.what != "ocr_scanned" && body.what != "retry_failed" {
        return Err(ApiError::invalid("what: ocr_scanned or retry_failed"));
    }
    st.run(move |st| {
        let mut db = st.db.connect()?;
        let tx = write_tx(&mut db)?;
        let case = access::visible_case(&tx, &user, &case_id)?;
        access::require_edit(&user, &case)?;
        let cond = if body.what == "ocr_scanned" {
            "f.source_type = 'pdf' AND f.status IN ('needs_ocr', 'converted') AND f.scanned_pages != '[]'"
        } else {
            "f.status = 'failed'"
        };
        let rows: Vec<FileRow> = {
            let mut stmt = tx.prepare(&format!("{FILE_SELECT} WHERE f.case_id = ? AND {cond}"))?;
            let it = stmt.query_map([&case_id], file_row)?;
            it.collect::<rusqlite::Result<_>>()?
        };
        for row in &rows {
            let ocr = body.what == "ocr_scanned" || row.scanned_pages != json!([]);
            queue_convert(&tx, &user, row, ocr, &ip)?;
        }
        tx.commit()?;
        Ok(Json(json!({"queued": rows.len()})))
    })
    .await
}

// --- Progress and status ------------------------------------------------------------

fn count_by(db: &Connection, sql: &str, case_id: &str, empty_as: Option<&str>) -> rusqlite::Result<Map<String, Value>> {
    let mut stmt = db.prepare(sql)?;
    let mut out = Map::new();
    let mut rows = stmt.query([case_id])?;
    while let Some(r) = rows.next()? {
        let mut k: String = r.get(0)?;
        if k.is_empty() {
            if let Some(e) = empty_as {
                k = e.to_string();
            }
        }
        out.insert(k, json!(r.get::<_, i64>(1)?));
    }
    Ok(out)
}

/// Counts for the case's progress line and What to do next panel.
pub async fn progress(State(st): State<AppState>, CurrentUser(user, _): CurrentUser, Path(case_id): Path<String>) -> ApiResult<Json<Value>> {
    st.run(move |st| {
        let db = st.db.connect()?;
        access::visible_case(&db, &user, &case_id)?;
        let one = |sql: &str| -> rusqlite::Result<i64> { db.query_row(sql, [&case_id], |r| r.get(0)) };
        let statuses = count_by(&db, "SELECT status, COUNT(*) FROM files WHERE case_id = ? GROUP BY status", &case_id, None)?;
        let total: i64 = statuses.values().map(|v| v.as_i64().unwrap()).sum();
        let warnings = one("SELECT COUNT(*) FROM files WHERE case_id = ? AND warnings != '[]'")?;
        let scanned = one("SELECT COUNT(*) FROM files WHERE case_id = ? AND scanned_pages != '[]'")?;
        let ocr = count_by(&db, "SELECT ocr_state, COUNT(*) FROM files WHERE case_id = ? AND ocr_state != '' GROUP BY ocr_state", &case_id, None)?;
        let near = one("SELECT COUNT(*) FROM files WHERE case_id = ? AND near_dup_of IS NOT NULL")?;
        let job_counts = jobs::counts(&db, Some(&[case_id.clone()]))?;
        let index = count_by(
            &db,
            "SELECT index_state, COUNT(*) FROM files WHERE case_id = ? AND status IN ('converted', 'needs_ocr') GROUP BY index_state",
            &case_id,
            Some("waiting"),
        )?;
        Ok(Json(json!({"files": statuses, "total": total, "warnings": warnings, "scanned": scanned, "ocr": ocr,
                        "near_duplicates": near, "jobs": job_counts, "index": index})))
    })
    .await
}

#[derive(Deserialize)]
pub struct StatusQuery {
    show: Option<String>,
    limit: Option<i64>,
    offset: Option<i64>,
}

struct StatusRow {
    id: String,
    name: String,
    folder: String,
    kind: String,
    size: i64,
    status: String,
    error: String,
    warnings: String,
    notes: String,
    scanned_pages: String,
    ocr: bool,
    ocr_state: String,
    ocr_error: String,
    index_state: String,
    passages: i64,
    converted_at: Option<String>,
    indexed_at: Option<String>,
    page_count: Option<i64>,
    near_dup: bool,
}

/// ready: searchable by exact words and by meaning, nothing left to do.
/// working: something is queued or running, or waiting for the local model.
/// attention: something failed or is missing and won't fix itself.
fn stage(r: &StatusRow, pending: bool) -> &'static str {
    if r.status == "failed" || r.ocr_state == "failed" || r.index_state == "failed" || r.warnings != "[]" || r.ocr_state == "unavailable" {
        return "attention";
    }
    if pending
        || r.status == "queued"
        || r.status == "converting"
        || r.ocr_state == "waiting"
        || r.ocr_state == "running"
        || r.index_state.is_empty()
        || r.index_state == "keyword_only"
    {
        return "working";
    }
    "ready"
}

fn bump(v: &mut Value, path: &[&str], by: i64) {
    let mut cur = v;
    for p in path {
        cur = cur.get_mut(*p).unwrap();
    }
    *cur = json!(cur.as_i64().unwrap() + by);
}

/// Every file's progress through conversion to Markdown, OCR of scanned
/// pages and the search index, with counts for the whole case.
pub async fn status(State(st): State<AppState>, CurrentUser(user, _): CurrentUser, Path(case_id): Path<String>, Params(q): Params<StatusQuery>) -> ApiResult<Json<Value>> {
    let show = q.show.unwrap_or_else(|| "all".into());
    if !["all", "attention", "working", "ready"].contains(&show.as_str()) {
        return Err(ApiError::invalid("show: all, attention, working or ready"));
    }
    let limit = q.limit.unwrap_or(500);
    in_range("limit", limit, 1, 2000)?;
    let offset = q.offset.unwrap_or(0);
    in_range("offset", offset, 0, i64::MAX)?;
    st.run(move |st| {
        let db = st.db.connect()?;
        access::visible_case(&db, &user, &case_id)?;
        let rows: Vec<StatusRow> = {
            let mut stmt = db.prepare(
                "SELECT id, name, folder, source_type, size, status, error, warnings, notes, scanned_pages, ocr, ocr_state, ocr_error,
                        index_state, passages, converted_at, indexed_at, page_count, near_dup_of FROM files WHERE case_id = ?
                 ORDER BY folder COLLATE NOCASE, name COLLATE NOCASE, id",
            )?;
            let it = stmt.query_map([&case_id], |r| {
                let text = |i| r.get::<_, Option<String>>(i).map(|v| v.unwrap_or_default());
                Ok(StatusRow {
                    id: r.get(0)?,
                    name: r.get(1)?,
                    folder: r.get(2)?,
                    kind: r.get(3)?,
                    size: r.get(4)?,
                    status: r.get(5)?,
                    error: text(6)?,
                    warnings: text(7)?,
                    notes: text(8)?,
                    scanned_pages: text(9)?,
                    ocr: r.get::<_, i64>(10)? != 0,
                    ocr_state: text(11)?,
                    ocr_error: text(12)?,
                    index_state: text(13)?,
                    passages: r.get::<_, Option<i64>>(14)?.unwrap_or(0),
                    converted_at: r.get(15)?,
                    indexed_at: r.get(16)?,
                    page_count: r.get(17)?,
                    near_dup: r.get::<_, Option<String>>(18)?.is_some(),
                })
            })?;
            it.collect::<rusqlite::Result<_>>()?
        };
        let mut pending: HashMap<String, BTreeMap<String, String>> = HashMap::new();
        {
            let mut stmt = db.prepare("SELECT file_id, kind, state FROM jobs WHERE case_id = ? AND file_id IS NOT NULL AND state IN ('queued', 'running')")?;
            let mut it = stmt.query([&case_id])?;
            while let Some(j) = it.next()? {
                pending.entry(j.get(0)?).or_default().insert(j.get(1)?, j.get(2)?);
            }
        }
        let mut summary = json!({
            "total": rows.len(),
            "stages": {"attention": 0, "working": 0, "ready": 0},
            "markdown": {"done": 0, "working": 0, "failed": 0},
            "ocr": {"needed": 0, "done": 0, "working": 0, "unavailable": 0, "failed": 0},
            "keyword": {"done": 0, "working": 0},
            "meaning": {"done": 0, "working": 0, "waiting_model": 0, "failed": 0},
            "passages": 0,
            "warnings": 0,
        });
        let empty = BTreeMap::new();
        let mut items = Vec::new();
        for r in &rows {
            let jobs_for = pending.get(&r.id).unwrap_or(&empty);
            let stage = stage(r, !jobs_for.is_empty());
            bump(&mut summary, &["stages", stage], 1);
            let converted = r.status == "converted" || r.status == "needs_ocr";
            let m = if converted { "done" } else if r.status == "failed" { "failed" } else { "working" };
            bump(&mut summary, &["markdown", m], 1);
            if !r.ocr_state.is_empty() {
                bump(&mut summary, &["ocr", "needed"], 1);
                let o = match r.ocr_state.as_str() {
                    "done" => "done",
                    "unavailable" => "unavailable",
                    "failed" => "failed",
                    _ => "working",
                };
                bump(&mut summary, &["ocr", o], 1);
            }
            if converted {
                let indexing = jobs_for.contains_key("index") || r.index_state.is_empty();
                let k = if r.index_state == "indexed" || r.index_state == "keyword_only" { "done" } else { "working" };
                bump(&mut summary, &["keyword", k], 1);
                let meaning = if r.index_state == "indexed" && !indexing {
                    "done"
                } else if r.index_state == "keyword_only" && !indexing {
                    "waiting_model"
                } else if r.index_state == "failed" {
                    "failed"
                } else {
                    "working"
                };
                bump(&mut summary, &["meaning", meaning], 1);
                bump(&mut summary, &["passages"], r.passages);
            }
            if r.warnings != "[]" {
                bump(&mut summary, &["warnings"], 1);
            }
            if show != "all" && stage != show {
                continue;
            }
            items.push(json!({
                "id": r.id, "name": r.name, "folder": r.folder, "type": r.kind, "size": r.size,
                "stage": stage, "status": r.status, "error": r.error,
                "warnings": json_list(Some(r.warnings.clone())), "notes": json_list(Some(r.notes.clone())),
                "page_count": r.page_count, "scanned_pages": json_list(Some(r.scanned_pages.clone())).as_array().map(|a| a.len()).unwrap_or(0),
                "ocr": r.ocr, "ocr_state": r.ocr_state, "ocr_error": r.ocr_error,
                "index_state": r.index_state, "passages": r.passages, "near_duplicate": r.near_dup,
                "converted_at": r.converted_at, "indexed_at": r.indexed_at, "jobs": jobs_for,
            }));
        }
        let matching = items.len();
        let page: Vec<Value> = items.into_iter().skip(offset as usize).take(limit as usize).collect();
        Ok(Json(json!({"summary": summary, "model": st.cfg.embed_model, "ocr_installed": ocr_available(), "items": page, "matching": matching})))
    })
    .await
}

// --- Converted documents -------------------------------------------------------------

/// (front matter without the --- lines, body)
pub fn split_front_matter(md: &str) -> (String, String) {
    if let Some(rest) = md.strip_prefix("---\n") {
        if let Some(end) = rest.find("\n---\n") {
            return (rest[..end].to_string(), rest[end + 5..].trim_start_matches('\n').to_string());
        }
    }
    (String::new(), md.to_string())
}

/// The converted Markdown, split into its front matter and body. Opening a
/// document is recorded in the audit log.
pub async fn markdown(State(st): State<AppState>, CurrentUser(user, ip): CurrentUser, Path((case_id, file_id)): Path<(String, String)>) -> ApiResult<Json<Value>> {
    st.run(move |st| {
        let mut db = st.db.connect()?;
        let tx = write_tx(&mut db)?;
        let (_, row) = visible_file(&tx, &user, &case_id, &file_id)?;
        let path = st.storage.markdown(&case_id, &file_id)?;
        if row.converted_at.is_none() || !path.is_file() {
            let msg = if row.status == "queued" || row.status == "converting" {
                "This file hasn't been converted yet."
            } else {
                "There's no Markdown for this file. Convert it again to make it."
            };
            return Err(ApiError::new(409, msg));
        }
        audit::record(&tx, Entry { action: "file.view", user: Some(&user), case_id: Some(&case_id), target: &row.shown(), ip: &ip, ..Default::default() })?;
        tx.commit()?;
        let text = std::fs::read_to_string(&path)?;
        let (fm, body) = split_front_matter(&text);
        Ok(Json(json!({"front_matter": fm, "body": body, "markdown_sha256": row.markdown_sha256})))
    })
    .await
}

/// A page image made by the worker for the document viewer.
pub async fn page_image(State(st): State<AppState>, CurrentUser(user, _): CurrentUser, Path((case_id, file_id, page)): Path<(String, String, i64)>) -> ApiResult<Response> {
    let path = st
        .run(move |st| {
            let db = st.db.connect()?;
            let (_, row) = visible_file(&db, &user, &case_id, &file_id)?;
            if page < 1 || page > row.previews {
                return Err(ApiError::new(404, "There's no picture of that page."));
            }
            let path = st.storage.preview(&case_id, &file_id, page)?;
            if !path.is_file() {
                return Err(ApiError::new(404, "There's no picture of that page. Convert the file again to make one."));
            }
            Ok(path)
        })
        .await?;
    let bytes = tokio::fs::read(path).await?;
    Ok(([(header::CONTENT_TYPE, "image/jpeg"), (header::CACHE_CONTROL, "private, max-age=3600")], bytes).into_response())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn paths() {
        assert_eq!(split_path("/Top/Sub/file.pdf"), Some(("Top/Sub".into(), "file.pdf".into())));
        assert_eq!(split_path("Top\\Sub\\file.pdf"), Some(("Top/Sub".into(), "file.pdf".into())));
        assert_eq!(split_path("file.pdf"), Some(("".into(), "file.pdf".into())));
        for bad in ["../outside.pdf", "a/../../b.pdf", "a//b.pdf", "", "a/", "bad\x01name.pdf", "./x.pdf"] {
            assert_eq!(split_path(bad), None, "{bad:?}");
        }
    }

    #[test]
    fn extensions() {
        assert_eq!(extension("SCAN.TIFF"), ".tiff");
        assert_eq!(extension(".hidden"), "");
        assert_eq!(extension("README"), "");
    }

    #[test]
    fn download_names() {
        assert_eq!(attachment("a.pdf"), "attachment; filename=\"a.pdf\"");
        assert_eq!(attachment("re offer.msg"), "attachment; filename*=utf-8''re%20offer.msg");
    }

    #[test]
    fn sizes() {
        assert_eq!(fmt_g(2.0), "2");
        assert_eq!(fmt_g(1.5), "1.5");
    }

    #[test]
    fn front_matter() {
        assert_eq!(split_front_matter("---\na: 1\n---\n\nbody"), ("a: 1".into(), "body".into()));
        assert_eq!(split_front_matter("plain"), ("".into(), "plain".into()));
    }
}
