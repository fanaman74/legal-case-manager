//! Background worker: takes jobs from the queue in app.db (see jobs.rs).
//!
//! It runs inside the server process, on its own threads. Each kind of work
//! has its own threads, so a long OCR job doesn't hold up new uploads. It
//! writes a heartbeat file every few seconds, which the launcher reads for
//! the Control Center, and a status file with the queue's counts.
//!
//! Job kinds this version doesn't do yet (search indexing, mailboxes, AI
//! models, exports) stay in the queue for a later version to pick up.

use std::collections::HashMap;
use std::time::{Duration, Instant};

use serde_json::json;

use crate::db::write_tx;
use crate::jobs::{self, Job};
use crate::pipeline;
use crate::state::AppState;

const INTERVAL: Duration = Duration::from_secs(5);
const IDLE_SLEEP: Duration = Duration::from_secs(1);

type Handler = fn(&AppState, &Job) -> anyhow::Result<()>;

fn handler(kind: &str) -> Option<Handler> {
    match kind {
        "convert" | "ocr" => Some(pipeline::run_convert),
        _ => None,
    }
}

/// Which job kinds each worker thread takes.
fn lanes(st: &AppState) -> Vec<Vec<&'static str>> {
    // OCR of a long scan takes minutes, so it has its own thread and new
    // uploads are converted meanwhile.
    let mut out = vec![vec!["convert"]; st.cfg.convert_workers];
    out.push(vec!["ocr"]);
    out
}

fn run_lane(st: AppState, kinds: Vec<&'static str>) {
    loop {
        let job = match jobs::claim(&st.db, &kinds) {
            Ok(Some(job)) => job,
            Ok(None) => {
                std::thread::sleep(IDLE_SLEEP);
                continue;
            }
            Err(e) => {
                eprintln!("couldn't read the job queue: {e}");
                std::thread::sleep(INTERVAL);
                continue;
            }
        };
        let Some(h) = handler(&job.kind) else { continue };
        let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| h(&st, &job)));
        let failed = match outcome {
            Ok(Ok(())) => continue,
            Ok(Err(e)) => e.to_string(),
            Err(_) => "panic".to_string(),
        };
        eprintln!("{} job {} failed: {failed}", job.kind, job.id);
        if let Err(e) = record_failure(&st, &job) {
            eprintln!("couldn't record the failure of job {}: {e}", job.id);
        }
    }
}

fn record_failure(st: &AppState, job: &Job) -> anyhow::Result<()> {
    let mut conn = st.db.connect()?;
    let tx = write_tx(&mut conn)?;
    jobs::fail(&tx, job.id, "Something went wrong in the background worker. Retry it; if it keeps failing, the Admin can check the worker's log in the Control Center.")?;
    if let Some(fid) = &job.file_id {
        if job.kind == "ocr" {
            tx.execute(
                "UPDATE files SET ocr_state = 'failed', ocr_error = ? WHERE id = ?",
                ("The worker couldn't read the scanned pages. The rest of the file is still searchable; if it keeps failing, the Admin can check the worker's log in the Control Center.", fid),
            )?;
        } else if job.kind == "convert" {
            tx.execute(
                "UPDATE files SET status = 'failed', error = ? WHERE id = ?",
                ("The worker couldn't convert this file. Retry it; if it keeps failing, the Admin can check the worker's log in the Control Center.", fid),
            )?;
        }
    }
    tx.commit()?;
    Ok(())
}

fn beat(st: &AppState) -> std::io::Result<()> {
    let dir = st.cfg.run_dir();
    std::fs::create_dir_all(&dir)?;
    let tmp = dir.join("worker-heartbeat.tmp");
    std::fs::write(&tmp, format!("{} {}\n", crate::util::unix_now() as i64, st.cfg.version))?;
    std::fs::rename(tmp, dir.join("worker-heartbeat"))
}

/// Queue counts for the Control Center. Counts only: no case or file names,
/// so nothing confidential reaches the launcher or its diagnostics.
fn write_status(st: &AppState) -> anyhow::Result<()> {
    let conn = st.db.connect()?;
    let counts = jobs::counts(&conn, None)?;
    let mut files: HashMap<String, i64> = HashMap::new();
    let mut stmt = conn.prepare("SELECT status, COUNT(*) FROM files GROUP BY status")?;
    for r in stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))? {
        let (k, v) = r?;
        files.insert(k, v);
    }
    let status = json!({"updated": crate::util::unix_now() as i64, "jobs": counts, "files": files});
    let dir = st.cfg.run_dir();
    let tmp = dir.join("worker-status.tmp");
    std::fs::write(&tmp, status.to_string())?;
    std::fs::rename(tmp, dir.join("worker-status.json"))?;
    Ok(())
}

/// Run every waiting job of these kinds, one after another. Returns how
/// many ran.
pub fn run_waiting(st: &AppState, kinds: &[&str]) -> anyhow::Result<usize> {
    let mut n = 0;
    while let Some(job) = jobs::claim(&st.db, kinds)? {
        n += 1;
        match handler(&job.kind) {
            Some(h) => {
                if let Err(e) = h(st, &job) {
                    eprintln!("{} job {} failed: {e}", job.kind, job.id);
                    record_failure(st, &job)?;
                }
            }
            None => {
                // Not done by this version: leave it waiting.
                jobs::retry_later(&st.db.connect()?, job.id)?;
                return Ok(n - 1);
            }
        }
    }
    Ok(n)
}

/// Start the worker threads and the housekeeping loop.
pub fn start(st: AppState) {
    match jobs::recover(&st.db) {
        Ok(n) if n > 0 => eprintln!("{n} unfinished jobs put back in the queue"),
        Err(e) => eprintln!("couldn't recover unfinished jobs: {e}"),
        _ => {}
    }
    for (i, kinds) in lanes(&st).into_iter().enumerate() {
        let st = st.clone();
        let _ = std::thread::Builder::new().name(format!("{}-{i}", kinds.join("-"))).spawn(move || run_lane(st, kinds));
    }
    std::thread::spawn(move || {
        let mut last_catch_up: Option<Instant> = None;
        let mut last_tidy: Option<Instant> = None;
        let mut healthy: Option<bool> = None;
        loop {
            let r = (|| -> anyhow::Result<()> {
                beat(&st)?;
                write_status(&st)?;
                if last_catch_up.is_none_or(|t| t.elapsed() > Duration::from_secs(300)) {
                    last_catch_up = Some(Instant::now());
                    let n = pipeline::ocr_catch_up(&st)?;
                    if n > 0 {
                        eprintln!("reading scanned pages with OCR for {n} files");
                    }
                }
                if last_tidy.is_none_or(|t| t.elapsed() > Duration::from_secs(3600)) {
                    jobs::tidy(&st.db)?;
                    st.storage.empty_trash();
                    last_tidy = Some(Instant::now());
                }
                Ok(())
            })();
            match r {
                Ok(()) => healthy = Some(true),
                Err(e) => {
                    if healthy != Some(false) {
                        eprintln!("can't use the data folder: {e}");
                    }
                    healthy = Some(false);
                }
            }
            std::thread::sleep(INTERVAL);
        }
    });
}
