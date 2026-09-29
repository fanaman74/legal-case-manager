"""Tiny health server for tool containers (OCR, PST parser). GET /health runs
the tool's version command, so "healthy" means the binary actually works.
Standard library only. The real job endpoints arrive in phases 3 and 5."""

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOOL = os.environ["TOOL_NAME"]
# Fixed command from the image's environment; never from a request.
CHECK = os.environ["TOOL_CHECK"].split()
_cache = {"at": 0.0, "ok": False, "version": ""}
_lock = threading.Lock()


def probe() -> dict:
    with _lock:
        if time.time() - _cache["at"] < 30:
            return dict(_cache)
        try:
            out = subprocess.run(CHECK, capture_output=True, text=True, timeout=10)
            text = (out.stdout or out.stderr).strip().splitlines()
            _cache.update(ok=out.returncode == 0, version=text[0] if text else "")
        except (OSError, subprocess.TimeoutExpired) as exc:
            _cache.update(ok=False, version=str(exc))
        _cache["at"] = time.time()
        return dict(_cache)


class Handler(BaseHTTPRequestHandler):
    server_version = "tool"
    sys_version = ""

    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        p = probe()
        body = json.dumps({"tool": TOOL, "status": "ok" if p["ok"] else "failing", "version": p["version"]}).encode()
        self.send_response(200 if p["ok"] else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        if not self.path.startswith("/health"):
            sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))


def check() -> int:
    import urllib.request

    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=5) as r:
            return 0 if r.status == 200 else 1
    except Exception:
        return 1


if __name__ == "__main__":
    if sys.argv[1:] == ["check"]:
        sys.exit(check())
    print(f"{TOOL} service ready: {probe()['version']}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
