"""Web app / API. Phase 1 only exposes health endpoints and a holding page;
accounts, cases and files arrive in phase 2."""

import os
import tempfile

import redis
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from . import settings

cfg = settings.load()
app = FastAPI(title="Case File Manager", version=cfg.version, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
def health() -> dict:
    """Liveness: the process is up. Used by Docker, the launcher and Railway."""
    return {"status": "ok", "version": cfg.version}


def _data_dir_writable() -> bool:
    try:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=cfg.data_dir, prefix=".ready-", delete=True):
            pass
        return True
    except OSError:
        return False


def _queue_reachable() -> bool:
    try:
        return bool(redis.Redis.from_url(cfg.redis_url, socket_timeout=2).ping())
    except redis.RedisError:
        return False


@app.get("/ready")
def ready() -> JSONResponse:
    """Readiness: the app can do real work (data folder and job queue)."""
    checks = {"data_dir": _data_dir_writable(), "queue": _queue_reachable()}
    ok = all(checks.values())
    return JSONResponse({"status": "ok" if ok else "not_ready", "checks": checks}, status_code=200 if ok else 503)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Case File Manager</title>
<style>
:root{--paper:#F6F5F1;--ink:#1D1C1A;--muted:#5B574F;--rule:#D9D5CC}
@media (prefers-color-scheme: dark){:root{--paper:#131311;--ink:#ECE9E2;--muted:#A7A195;--rule:#36342F}}
body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.45 "IBM Plex Sans","Segoe UI",system-ui,sans-serif}
main{max-width:34rem;margin:15vh auto 0;padding:0 16px}
h1{font-size:1.375rem;font-weight:600;margin:0 0 8px}
p{color:var(--muted);margin:0 0 12px}
</style></head>
<body><main>
<h1>Case File Manager is running</h1>
<p>Sign-in, cases and file upload arrive in the next version. The Admin can check every service in the Control Center on the host computer.</p>
</main></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGE
