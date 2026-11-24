//! Case File Manager: the web app and API in one program.
//!
//! Serves HTTPS on every interface (port 8443 by default), refusing anyone
//! outside the local network. With TLS_CERT and TLS_KEY it uses the
//! launcher's certificate; without them it makes a certificate for this
//! computer once and keeps it in the data folder.

mod access;
mod accounts;
mod audit;
mod config;
mod convert;
mod db;
mod jobs;
mod notes;
mod pipeline;
mod routes;
mod state;
mod storage;
mod tls;
mod util;
mod web;
mod worker;

use std::net::SocketAddr;

use axum::extract::DefaultBodyLimit;
use axum::routing::{get, post, put};
use axum::Router;

use crate::routes::{audit_log, cases, files, session, users, work};
use crate::state::AppState;

pub fn app(st: AppState) -> Router {
    let api = Router::new()
        .route("/setup", get(session::setup_status))
        .route("/session", get(session::get_session).post(session::sign_in).delete(session::sign_out))
        .route("/session/password", post(session::change_password))
        .route("/users", get(users::list_users).post(users::add_user))
        .route("/users/{id}", axum::routing::patch(users::edit_user))
        .route("/users/{id}/reset-password", post(users::reset_password))
        .route("/users/{id}/unlock", post(users::unlock))
        .route("/users/{id}/cases", put(users::set_cases))
        .route("/cases", get(cases::list_cases).post(cases::create_case))
        .route("/cases/{case}", get(cases::get_case).patch(cases::edit_case))
        .route("/cases/{case}/archive", post(cases::archive_case))
        .route("/cases/{case}/restore", post(cases::restore_case))
        .route("/cases/{case}/delete", post(cases::delete_case))
        .route("/cases/{case}/members", get(cases::list_members).put(cases::set_members))
        .route("/cases/{case}/activity", get(cases::case_activity))
        .route("/cases/{case}/files", get(files::list_files).put(files::upload).layer(DefaultBodyLimit::disable()))
        .route("/cases/{case}/folders", get(files::folders))
        .route("/cases/{case}/files/{file}", get(files::get_file).patch(files::edit_file))
        .route("/cases/{case}/files/{file}/original", get(files::download))
        .route("/cases/{case}/files/{file}/convert", post(files::reconvert))
        .route("/cases/{case}/files/{file}/markdown", get(files::markdown))
        .route("/cases/{case}/files/{file}/pages/{page}", get(files::page_image))
        .route("/cases/{case}/convert", post(files::bulk_convert))
        .route("/cases/{case}/progress", get(files::progress))
        .route("/cases/{case}/status", get(files::status))
        .route("/audit", get(audit_log::audit_log))
        .route("/status", get(work::status))
        .route("/jobs", get(work::list_jobs))
        .route("/jobs/{id}/retry", post(work::retry));
    Router::new()
        .route("/health", get(web::health))
        .route("/ready", get(web::ready))
        .route("/assets/{name}", get(web::asset))
        .nest("/api", api)
        .fallback(web::page)
        .layer(axum::middleware::from_fn(web::guard))
        .with_state(st)
}

fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    // `casefiles convert ...` converts one file (run by the worker as a
    // child process); anything else starts the server.
    if args.get(1).map(String::as_str) == Some("convert") {
        std::process::exit(convert::main(&args[2..]));
    }
    // `casefiles work convert ocr` runs every waiting job of those kinds and
    // exits (for tests and for catching up by hand).
    if args.get(1).map(String::as_str) == Some("work") {
        let st = AppState::new(config::Settings::load()?)?;
        let kinds: Vec<&str> = args[2..].iter().map(String::as_str).collect();
        println!("{}", worker::run_waiting(&st, &kinds)?);
        return Ok(());
    }
    tokio::runtime::Builder::new_multi_thread().enable_all().build()?.block_on(serve())
}

async fn serve() -> anyhow::Result<()> {
    let _ = rustls::crypto::ring::default_provider().install_default();
    let cfg = config::Settings::load()?;
    let tls = tls::config(&cfg).await?;
    let st = AppState::new(cfg.clone())?;
    st.storage.empty_trash();
    // WORKER=0 leaves conversion to `casefiles work` (the tests do this).
    if std::env::var("WORKER").as_deref() != Ok("0") {
        worker::start(st.clone());
    }
    // Started on its own (double-clicked), not by the launcher: make a first
    // Admin if there is none, and open the app in the browser.
    let standalone = cfg.tls_cert.is_empty() && std::env::var("FIRST_RUN_ADMIN").as_deref() != Ok("0");
    if standalone {
        if let Some(temp) = accounts::first_run_admin(&st)? {
            eprintln!("\nFirst start: sign in as  admin  with the password  {temp}");
            eprintln!("(also saved in {})\n", accounts::first_run_note(&st).display());
        }
    }
    let addr: SocketAddr = format!("{}:{}", cfg.host, cfg.port).parse()?;
    eprintln!("Case File Manager {} is running at https://localhost:{}/ (data in {})", cfg.version, cfg.port, cfg.data_dir.display());
    if standalone && std::env::var("OPEN_BROWSER").as_deref() != Ok("0") {
        open_browser(&format!("https://localhost:{}/", cfg.port));
    }
    axum_server::bind_rustls(addr, tls)
        .serve(app(st).into_make_service_with_connect_info::<SocketAddr>())
        .await?;
    Ok(())
}

fn open_browser(url: &str) {
    #[cfg(windows)]
    let _ = std::process::Command::new("cmd").args(["/C", "start", "", url]).spawn();
    #[cfg(not(windows))]
    let _ = url;
}
