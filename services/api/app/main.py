"""Web app / API: health checks, sign-in, people, cases, file upload and the
audit log. Every /api route except sign-in needs a signed-in session."""

import ipaddress
import sqlite3
import tempfile
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response

from . import runtime, settings
from .routes import audit_log, cases, files, session, users

cfg = settings.load()
app = FastAPI(title="Case File Manager", version=cfg.version, docs_url=None, redoc_url=None, openapi_url=None)
app.state.rt = runtime.Runtime(cfg)
for module in (session, users, cases, files, audit_log):
    app.include_router(module.router)

# Locally the app listens on every interface so people on the office network
# can reach it. Windows Firewall only opens the port on Private networks; this
# check is the second lock: anyone outside the local network is refused, even
# if the firewall rule is changed.
_LOCAL_NETS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7", "fe80::/10")]


def is_local_client(host: str | None) -> bool:
    try:
        ip = ipaddress.ip_address(host or "")
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_loopback or any(ip in n for n in _LOCAL_NETS)


@app.middleware("http")
async def local_network_only(request: Request, call_next):
    if not is_local_client(request.client.host if request.client else None):
        return PlainTextResponse("Case File Manager is only available on the office network. Connect to it, or to the office VPN, and try again.", status_code=403)
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        # API answers hold case data: never cache them.
        response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


@app.get("/health")
def health() -> dict:
    """Liveness: the process is up. Used by the launcher."""
    return {"status": "ok", "version": cfg.version}


def _data_dir_writable() -> bool:
    try:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=cfg.data_dir, prefix=".ready-", delete=True):
            pass
        return True
    except OSError:
        return False


def _sqlite_search_available() -> bool:
    """Keyword search and the job queue both need SQLite with FTS5."""
    try:
        with sqlite3.connect(":memory:") as db:
            db.execute("CREATE VIRTUAL TABLE t USING fts5(body)")
        return True
    except sqlite3.Error:
        return False


@app.get("/ready")
def ready() -> JSONResponse:
    """Readiness: the app can do real work (data folder and SQLite search)."""
    checks = {"data_dir": _data_dir_writable(), "sqlite_fts5": _sqlite_search_available()}
    ok = all(checks.values())
    return JSONResponse({"status": "ok" if ok else "not_ready", "checks": checks}, status_code=200 if ok else 503)


# The web app's screens (built from web/ into app/web by scripts/build.sh).
WEB_DIR = Path(__file__).parent / "web"
CSP = "; ".join((
    "default-src 'self'",
    "img-src 'self' data:",
    # Radix adds a small <style> element while dialogs are open.
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
))

NOT_BUILT = """<!doctype html>
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
<p>The screens are missing from this install. Run Setup.exe again from the latest download to repair it.</p>
</main></body></html>"""


def _index() -> Response:
    index = WEB_DIR / "index.html"
    if not index.is_file():
        return HTMLResponse(NOT_BUILT)
    return FileResponse(index, media_type="text/html", headers={"Cache-Control": "no-cache", "Content-Security-Policy": CSP})


@app.get("/assets/{name}")
def asset(name: str) -> Response:
    """Built JavaScript, CSS and fonts. Names carry a content hash, so they
    can be cached for good."""
    path = WEB_DIR / "assets" / name
    if "/" in name or "\\" in name or name.startswith(".") or not path.is_file():
        return PlainTextResponse("Not found", status_code=404)
    return FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/{path:path}", include_in_schema=False)
def page(path: str) -> Response:
    """Every other address is a page of the app (cases, people, ...); the
    browser-side router picks the screen."""
    if path.startswith("api/") or path == "api":
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return _index()
