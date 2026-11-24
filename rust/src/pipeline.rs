//! What the worker does with a convert job: run the converter in a child
//! process, then file its output (Markdown with front matter, page images,
//! email attachments as new files in the case) and record the result.
//!
//! Scanned pages are read with OCR by themselves: the first conversion skips
//! them, so the rest of the file is searchable within seconds, and queues an
//! "ocr" job that converts the file again with OCR in its own worker lane.
//! The file stays usable while OCR runs; nobody has to press anything.

use std::path::Path;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use rusqlite::{Connection, OptionalExtension};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::audit::{self, Entry};
use crate::convert::{self, markdown::front_matter, ocr::no_window};
use crate::db::write_tx;
use crate::jobs::{self, Job};
use crate::notes;
use crate::routes::files::{ocr_available, queue_ocr, source_type};
use crate::state::AppState;
use crate::util::{self, shown_path};

const MAX_ATTEMPTS: i64 = 2;

/// Seconds a conversion may take: generous, scaled by size, since OCR of a
/// long scan is slow on an ordinary computer.
fn timeout(size: i64, ocr: bool) -> Duration {
    let base = if ocr { 7200 } else { 1800 };
    Duration::from_secs((base + (size.max(0) as u64 >> 20) * 10).min(6 * 3600))
}

struct Src {
    id: String,
    case_id: String,
    name: String,
    folder: String,
    ext: String,
    source_type: String,
    size: i64,
    sha256: String,
    status: String,
    converted_at: Option<String>,
    added_by: Option<i64>,
    parent_id: Option<String>,
}

impl Src {
    fn path(&self) -> String {
        shown_path(&self.folder, &self.name)
    }

    /// Converted before: an OCR job then runs in the background.
    fn usable(&self) -> bool {
        self.converted_at.as_deref().is_some_and(|c| !c.is_empty()) && (self.status == "converted" || self.status == "needs_ocr")
    }
}

fn load(db: &Connection, file_id: &str, case_id: &str) -> rusqlite::Result<Option<Src>> {
    db.query_row(
        "SELECT id, case_id, name, folder, ext, source_type, size, sha256, status, converted_at, added_by, parent_id
         FROM files WHERE id = ? AND case_id = ?",
        [file_id, case_id],
        |r| {
            Ok(Src {
                id: r.get(0)?,
                case_id: r.get(1)?,
                name: r.get(2)?,
                folder: r.get::<_, Option<String>>(3)?.unwrap_or_default(),
                ext: r.get(4)?,
                source_type: r.get(5)?,
                size: r.get::<_, Option<i64>>(6)?.unwrap_or(0),
                sha256: r.get::<_, Option<String>>(7)?.unwrap_or_default(),
                status: r.get(8)?,
                converted_at: r.get(9)?,
                added_by: r.get(10)?,
                parent_id: r.get(11)?,
            })
        },
    )
    .optional()
}

fn worker_entry<'a>(action: &'a str, case_id: &'a str, target: &'a str, detail: &'a str, outcome: Option<&'a str>) -> Entry<'a> {
    Entry { action, username: Some("worker"), case_id: Some(case_id), target, detail, outcome, ..Default::default() }
}

/// Queue OCR for files whose scanned pages haven't been read: files that
/// waited for OCR to be installed. Returns how many were queued.
pub fn ocr_catch_up(st: &AppState) -> anyhow::Result<usize> {
    if !ocr_available() {
        return Ok(0);
    }
    let mut conn = st.db.connect()?;
    let tx = write_tx(&mut conn)?;
    let rows: Vec<(String, String)> = tx
        .prepare(
            "SELECT case_id, id FROM files WHERE source_type = 'pdf' AND status IN ('converted', 'needs_ocr') AND scanned_pages != '[]'
             AND ocr_state IN ('', 'unavailable') LIMIT 5000",
        )?
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
        .collect::<Result<_, _>>()?;
    for (case_id, id) in &rows {
        queue_ocr(&tx, case_id, id, None)?;
    }
    tx.commit()?;
    Ok(rows.len())
}

pub fn run_convert(st: &AppState, job: &Job) -> anyhow::Result<()> {
    let (Some(case_id), Some(file_id)) = (job.case_id.as_deref(), job.file_id.as_deref()) else {
        jobs::finish(&st.db.connect()?, job.id)?;
        return Ok(());
    };
    let mut conn = st.db.connect()?;
    let Some(row) = load(&conn, file_id, case_id)? else {
        jobs::finish(&conn, job.id)?; // deleted meanwhile
        return Ok(());
    };
    let mut ocr = job.options.get("ocr").and_then(Value::as_bool).unwrap_or(false) || job.kind == "ocr";
    // OCR of a file that is already converted runs in the background: the
    // file keeps its Markdown and stays searchable until the OCR is done.
    let background = ocr && row.usable();
    if ocr && !ocr_available() {
        if background {
            let tx = write_tx(&mut conn)?;
            tx.execute("UPDATE files SET ocr_state = 'unavailable' WHERE id = ?", [file_id])?;
            jobs::finish(&tx, job.id)?;
            tx.commit()?;
            return Ok(());
        }
        ocr = false; // convert without OCR now; the worker reads the scans once OCR is installed
    }
    if background {
        conn.execute("UPDATE files SET ocr_state = 'running', ocr_error = '' WHERE id = ?", [file_id])?;
    } else {
        conn.execute("UPDATE files SET status = 'converting', error = '' WHERE id = ?", [file_id])?;
    }

    let original = st.storage.original(case_id, file_id, &row.ext)?;
    let work = st.storage.work_dir(case_id)?;
    let result = convert_in_child(st, job, &row, &original, &work, ocr);
    let _ = std::fs::remove_dir_all(&work);
    result
}

fn convert_in_child(st: &AppState, job: &Job, row: &Src, original: &Path, work: &Path, ocr: bool) -> anyhow::Result<()> {
    let exe = std::env::current_exe()?;
    let mut cmd = Command::new(exe);
    cmd.arg("convert").arg(original).arg(&row.ext).arg(work).args(["--previews", "--lang", &st.cfg.ocr_lang]);
    if ocr {
        cmd.arg("--ocr");
    }
    cmd.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::piped());
    let mut child = no_window(&mut cmd).spawn()?;
    let mut err = child.stderr.take().unwrap();
    let t_err = std::thread::spawn(move || {
        let mut b = Vec::new();
        let _ = std::io::Read::read_to_end(&mut err, &mut b);
        b
    });
    let limit = timeout(row.size, ocr);
    let start = Instant::now();
    let status = loop {
        if let Some(s) = child.try_wait()? {
            break s;
        }
        if start.elapsed() > limit {
            let _ = child.kill();
            let _ = child.wait();
            return failed(
                st,
                job,
                row,
                "Converting this file took too long, so it was stopped. If it's a very long scan, try splitting it into smaller files.",
            );
        }
        std::thread::sleep(Duration::from_millis(100));
    };
    let stderr = t_err.join().unwrap_or_default();
    let result_file = work.join("result.json");
    if !status.success() || !result_file.is_file() {
        let msg = String::from_utf8_lossy(&stderr);
        let last = msg.trim().lines().last().unwrap_or("").chars().take(300).collect::<String>();
        eprintln!("converter exited with {status} for a {} file: {last}", row.ext);
        if job.attempts < MAX_ATTEMPTS {
            jobs::retry_later(&st.db.connect()?, job.id)?;
            return Ok(());
        }
        return failed(
            st,
            job,
            row,
            "The converter stopped unexpectedly on this file. It may be damaged. Open it on your computer to check it, or download it and upload a fresh copy.",
        );
    }
    let result: Value = serde_json::from_str(&std::fs::read_to_string(&result_file)?)?;
    if let Some(e) = result.get("error").and_then(Value::as_str) {
        return failed(st, job, row, e);
    }
    store(st, job, row, work, result, ocr)
}

fn failed(st: &AppState, job: &Job, row: &Src, message: &str) -> anyhow::Result<()> {
    let mut conn = st.db.connect()?;
    let tx = write_tx(&mut conn)?;
    let detail: String = message.chars().take(300).collect();
    let path = row.path();
    if job.kind == "ocr" && row.usable() {
        // The earlier conversion stays; only the scanned pages are unread.
        tx.execute("UPDATE files SET ocr_state = 'failed', ocr_error = ? WHERE id = ?", (message, &row.id))?;
        jobs::fail(&tx, job.id, message)?;
        audit::record(&tx, worker_entry("file.ocr", &row.case_id, &path, &detail, Some("failed")))?;
    } else {
        tx.execute("UPDATE files SET status = 'failed', error = ? WHERE id = ?", (message, &row.id))?;
        jobs::fail(&tx, job.id, message)?;
        audit::record(&tx, worker_entry("file.convert", &row.case_id, &path, &detail, Some("failed")))?;
    }
    tx.commit()?;
    Ok(())
}

fn sha_hex(data: &[u8]) -> String {
    hex::encode(Sha256::digest(data))
}

fn store(st: &AppState, job: &Job, row: &Src, work: &Path, result: Value, ocr: bool) -> anyhow::Result<()> {
    let (case_id, file_id) = (row.case_id.as_str(), row.id.as_str());
    let body = std::fs::read_to_string(work.join("body.md"))?;
    let mut conn = st.db.connect()?;

    // Email attachments become files of their own, linked to the email.
    let mut linked: Vec<Value> = Vec::new();
    {
        let tx = write_tx(&mut conn)?;
        let names: Vec<String> = result.get("attachments").and_then(|a| serde_json::from_value(a.clone()).ok()).unwrap_or_default();
        for (i, name) in names.iter().enumerate() {
            let data = std::fs::read(work.join("attachments").join(i.to_string()))?;
            let sha = sha_hex(&data);
            let existing: Option<String> =
                tx.query_row("SELECT id FROM files WHERE case_id = ? AND sha256 = ?", [case_id, &sha], |r| r.get(0)).optional()?;
            if let Some(id) = existing {
                linked.push(json!({"name": name, "doc_id": id, "duplicate": id != file_id}));
                continue;
            }
            let ext = name.rsplit_once('.').map(|(_, e)| format!(".{}", e.to_lowercase())).unwrap_or_default();
            let Some(kind) = source_type(&ext) else { continue };
            let new_id = util::new_id();
            tx.execute(
                "INSERT INTO files (id, case_id, name, folder, ext, source_type, size, sha256, status, added_at, added_by, parent_id)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
                rusqlite::params![new_id, case_id, name, row.folder, ext, kind, data.len() as i64, sha, util::now(), row.added_by, file_id],
            )?;
            st.storage.keep_bytes(case_id, &new_id, &ext, &data)?;
            jobs::enqueue(&tx, "convert", Some(case_id), Some(&new_id), None, None)?;
            let target = shown_path(&row.folder, name);
            let detail = format!("extracted from {}; sha256 {sha}", row.path());
            audit::record(&tx, worker_entry("file.attachment", case_id, &target, &detail, None))?;
            linked.push(json!({"name": name, "doc_id": new_id}));
        }
        tx.commit()?;
    }

    let mut meta = result.get("meta").and_then(Value::as_object).cloned().unwrap_or_default();
    let now = util::now();
    let page_count = result.get("page_count").cloned().unwrap_or(Value::Null);
    let has_ocr = result.get("ocr").and_then(Value::as_bool).unwrap_or(false);
    let mut fields: Vec<(&str, Value)> = vec![
        ("doc_id", json!(file_id)),
        ("case_id", json!(case_id)),
        ("source_filename", json!(row.name)),
        ("relative_path", json!(row.path())),
        ("source_type", json!(row.source_type)),
        ("sha256", json!(row.sha256)),
        ("converted_at", json!(now)),
        ("converter", json!(format!("case-file-manager {}", convert::CONVERTER_VERSION))),
        ("page_count", page_count.clone()),
        ("ocr", json!(has_ocr)),
        ("title", result.get("title").cloned().unwrap_or(Value::Null)),
        ("document_date", result.get("doc_date").cloned().unwrap_or(Value::Null)),
        ("parent_doc_id", json!(row.parent_id)),
    ];
    if row.source_type == "email" {
        for k in ["from", "to", "cc", "date", "subject", "message_id", "thread"] {
            fields.push((k, meta.get(k).cloned().unwrap_or(Value::Null)));
        }
    }
    fields.push(("attachments", Value::Array(linked.iter().map(|a| json!({"name": a["name"], "doc_id": a["doc_id"]})).collect())));
    let markdown = front_matter(&fields) + &body;
    let md_sha = sha_hex(markdown.as_bytes());
    st.storage.write_markdown(case_id, file_id, &markdown)?;
    let previews = result.get("previews").and_then(Value::as_i64).unwrap_or(0);
    let preview_dir = work.join("previews");
    st.storage.replace_previews(case_id, file_id, (previews > 0).then_some(preview_dir.as_path()))?;

    let scanned: Vec<i64> = result.get("scanned_pages").and_then(|s| serde_json::from_value(s.clone()).ok()).unwrap_or_default();
    let pages = page_count.as_i64();
    let status = if !scanned.is_empty() && pages.is_some_and(|p| p > 0 && scanned.len() as i64 == p) { "needs_ocr" } else { "converted" };
    let all: Vec<String> = result.get("warnings").and_then(|w| serde_json::from_value(w.clone()).ok()).unwrap_or_default();
    let (warnings, found_notes) = notes::split(all);
    // After OCR, pages still unread mean OCR couldn't render them: say so
    // rather than queue OCR again and again.
    let (ocr_state, ocr_error) = match (ocr, scanned.is_empty(), has_ocr) {
        (false, _, _) => ("", ""),
        (true, false, _) => ("failed", "Some scanned pages couldn't be read with OCR. Open the original to read them."),
        (true, true, true) => ("done", ""),
        (true, true, false) => ("", ""),
    };
    meta.insert("attachments".into(), Value::Array(linked));

    let tx = write_tx(&mut conn)?;
    let exists: Option<i64> = tx.query_row("SELECT 1 FROM files WHERE id = ?", [file_id], |r| r.get(0)).optional()?;
    if exists.is_none() {
        return Ok(()); // the case was deleted while converting
    }
    let title = result.get("title").and_then(Value::as_str);
    let doc_date = result.get("doc_date").and_then(Value::as_str);
    tx.execute(
        "UPDATE files SET status = ?, error = '', page_count = ?, ocr = ?, warnings = ?, notes = ?, scanned_pages = ?, meta = ?,
         doc_date = ?, title = ?, converted_at = ?, markdown_sha256 = ?, previews = ?, ocr_state = ?, ocr_error = ? WHERE id = ?",
        rusqlite::params![
            status,
            pages,
            has_ocr as i64,
            util::py_json(&warnings),
            util::py_json(&found_notes),
            util::py_json(&scanned),
            util::py_json(&meta),
            doc_date,
            title,
            now,
            md_sha,
            previews,
            ocr_state,
            ocr_error,
            file_id
        ],
    )?;
    jobs::finish(&tx, job.id)?;
    let mut detail = format!("markdown sha256 {md_sha}; converter {}", convert::CONVERTER_VERSION);
    if let Some(p) = pages.filter(|p| *p > 0) {
        detail.push_str(&format!("; {p} pages"));
    }
    if has_ocr {
        detail.push_str("; OCR");
    }
    audit::record(&tx, worker_entry("file.convert", case_id, &row.path(), &detail, None))?;
    after_convert(&tx, case_id, file_id)?;
    if !scanned.is_empty() && row.source_type == "pdf" && !ocr {
        queue_ocr(&tx, case_id, file_id, None)?;
    }
    tx.commit()?;
    Ok(())
}

/// Next step for a converted file: add it to the case's search index.
pub fn after_convert(db: &Connection, case_id: &str, file_id: &str) -> rusqlite::Result<()> {
    jobs::enqueue(db, "index", Some(case_id), Some(file_id), None, None)?;
    Ok(())
}
