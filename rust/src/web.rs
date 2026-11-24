//! The parts of the server that aren't case data: the office-network check,
//! security headers, health checks and the web app's screens.

use std::net::{IpAddr, SocketAddr};
use std::path::Path;

use axum::body::Body;
use axum::extract::{ConnectInfo, Request, State};
use axum::http::{header, HeaderValue, StatusCode};
use axum::middleware::Next;
use axum::response::{Html, IntoResponse, Response};
use axum::Json;
use serde_json::json;

use crate::state::AppState;

/// IPv4 addresses arrive as ::ffff:a.b.c.d on a dual-stack socket.
pub fn canonical_ip(ip: IpAddr) -> IpAddr {
    match ip {
        IpAddr::V6(v6) => v6.to_ipv4_mapped().map(IpAddr::V4).unwrap_or(ip),
        v4 => v4,
    }
}

/// Locally the app listens on every interface so people on the office
/// network can reach it. Windows Firewall only opens the port on Private
/// networks; this check is the second lock: anyone outside the local network
/// is refused, even if the firewall rule is changed.
pub fn is_local_client(ip: IpAddr) -> bool {
    let ip = canonical_ip(ip);
    if ip.is_loopback() {
        return true;
    }
    const NETS: &[&str] = &["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7", "fe80::/10"];
    NETS.iter().any(|n| n.parse::<ipnet::IpNet>().unwrap().contains(&ip))
}

pub async fn guard(req: Request, next: Next) -> Response {
    let local = req
        .extensions()
        .get::<ConnectInfo<SocketAddr>>()
        .map(|c| is_local_client(c.0.ip()))
        .unwrap_or(false);
    if !local {
        return (
            StatusCode::FORBIDDEN,
            "Case File Manager is only available on the office network. Connect to it, or to the office VPN, and try again.",
        )
            .into_response();
    }
    let api = req.uri().path().starts_with("/api/");
    let mut resp = next.run(req).await;
    let h = resp.headers_mut();
    if api {
        // API answers hold case data: never cache them.
        h.entry(header::CACHE_CONTROL).or_insert(HeaderValue::from_static("no-store"));
    }
    h.entry("x-content-type-options").or_insert(HeaderValue::from_static("nosniff"));
    h.entry("referrer-policy").or_insert(HeaderValue::from_static("no-referrer"));
    h.entry("x-frame-options").or_insert(HeaderValue::from_static("DENY"));
    resp
}

/// Liveness: the process is up. Used by the launcher.
pub async fn health(State(st): State<AppState>) -> Json<serde_json::Value> {
    Json(json!({"status": "ok", "version": st.cfg.version}))
}

fn data_dir_writable(dir: &Path) -> bool {
    if std::fs::create_dir_all(dir).is_err() {
        return false;
    }
    let probe = dir.join(format!(".ready-{}", crate::util::token_hex(4)));
    let ok = std::fs::write(&probe, b"").is_ok();
    let _ = std::fs::remove_file(&probe);
    ok
}

/// Readiness: the app can do real work (data folder and SQLite search).
pub async fn ready(State(st): State<AppState>) -> Response {
    let checks = json!({"data_dir": data_dir_writable(&st.cfg.data_dir), "sqlite_fts5": crate::db::fts5_available()});
    let ok = checks.as_object().unwrap().values().all(|v| v == true);
    let status = if ok { StatusCode::OK } else { StatusCode::SERVICE_UNAVAILABLE };
    (status, Json(json!({"status": if ok { "ok" } else { "not_ready" }, "checks": checks}))).into_response()
}

const CSP: &str = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self'; \
connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'";

const NOT_BUILT: &str = r#"<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Case File Manager</title>
<style>
:root{--paper:#F6F5F1;--ink:#1D1C1A;--muted:#5B574F}
@media (prefers-color-scheme: dark){:root{--paper:#131311;--ink:#ECE9E2;--muted:#A7A195}}
body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.45 "IBM Plex Sans","Segoe UI",system-ui,sans-serif}
main{max-width:34rem;margin:15vh auto 0;padding:0 16px}
h1{font-size:1.375rem;font-weight:600;margin:0 0 8px}
p{color:var(--muted);margin:0 0 12px}
</style></head>
<body><main>
<h1>Case File Manager is running</h1>
<p>The screens are missing from this copy. Unzip the latest download again to repair it.</p>
</main></body></html>"#;

fn index(st: &AppState) -> Response {
    match std::fs::read(st.cfg.web_dir.join("index.html")) {
        Ok(bytes) => (
            [
                (header::CONTENT_TYPE, "text/html; charset=utf-8"),
                (header::CACHE_CONTROL, "no-cache"),
                (header::CONTENT_SECURITY_POLICY, CSP),
            ],
            bytes,
        )
            .into_response(),
        Err(_) => Html(NOT_BUILT).into_response(),
    }
}

fn not_found_text() -> Response {
    (StatusCode::NOT_FOUND, "Not found").into_response()
}

/// Built JavaScript, CSS and fonts. Names carry a content hash, so they can
/// be cached for good.
pub async fn asset(State(st): State<AppState>, axum::extract::Path(name): axum::extract::Path<String>) -> Response {
    if name.contains('/') || name.contains('\\') || name.starts_with('.') || name.is_empty() {
        return not_found_text();
    }
    let path = st.cfg.web_dir.join("assets").join(&name);
    match tokio::fs::read(&path).await {
        Ok(bytes) => {
            let mime = mime_guess::from_path(&path).first_or_octet_stream();
            (
                [(header::CONTENT_TYPE, mime.to_string()), (header::CACHE_CONTROL, "public, max-age=31536000, immutable".into())],
                Body::from(bytes),
            )
                .into_response()
        }
        Err(_) => not_found_text(),
    }
}

/// Every other address is a page of the app (cases, people, ...); the
/// browser-side router picks the screen. Unknown API addresses are a JSON 404.
pub async fn page(State(st): State<AppState>, req: Request) -> Response {
    let path = req.uri().path();
    if path == "/api" || path.starts_with("/api/") {
        return (StatusCode::NOT_FOUND, Json(json!({"detail": "Not found"}))).into_response();
    }
    index(&st)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn local_clients() {
        for (host, ok) in [
            ("127.0.0.1", true),
            ("::1", true),
            ("192.168.1.20", true),
            ("10.4.0.9", true),
            ("172.20.1.1", true),
            ("fe80::1", true),
            ("::ffff:192.168.1.5", true),
            ("8.8.8.8", false),
            ("172.32.0.1", false),
            ("2001:db8::1", false),
        ] {
            assert_eq!(is_local_client(host.parse().unwrap()), ok, "{host}");
        }
    }
}
