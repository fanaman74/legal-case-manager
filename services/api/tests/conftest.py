import json
import os
import socket
import subprocess
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main, runtime, settings

# RUST_SERVER=path/to/casefiles runs these tests against the Rust version
# instead of the Python one: each test starts the Rust server on its own data
# folder, and the same requests must get the same answers. Tests that reach
# into the Python version's internals are marked python_only.
RUST_SERVER = os.environ.get("RUST_SERVER", "")
_rust = {"url": None}


def pytest_collection_modifyitems(config, items):
    if not RUST_SERVER:
        return
    skip = pytest.mark.skip(reason="checks Python internals; the Rust version has its own unit test")
    for item in items:
        if item.get_closest_marker("python_only"):
            item.add_marker(skip)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_rust(data_dir, **env) -> subprocess.Popen:
    port = _free_port()
    proc = subprocess.Popen(
        [RUST_SERVER],
        env={**os.environ, "DATA_DIR": str(data_dir), "PORT": str(port), "HOST": "127.0.0.1", "APP_VERSION": "test", "FIRST_RUN_ADMIN": "0", "WORKER": "0", **env},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    url = f"https://127.0.0.1:{port}"
    for _ in range(200):
        try:
            if httpx.get(url + "/health", verify=False).status_code == 200:
                _rust["url"] = url
                return proc
        except httpx.HTTPError:
            time.sleep(0.05)
        if proc.poll() is not None:
            raise RuntimeError(proc.stderr.read().decode())
    raise RuntimeError("the Rust server didn't start")

# Made by the launcher's own hashPassword (launcher/internal/auth/auth.go), so
# these tests prove the app accepts the launcher's Argon2id format.
ADMIN_PASSWORD = "correct horse battery"
LAUNCHER_HASH = "$argon2id$v=19$m=65536,t=3,p=2$e0zzmps31YpqqdvJ6EwGQw$bxZ67h68vt0+99cnhQ/cpDKMPg9BbNEEvUJSVU8abjc"


class Client:
    """A browser: keeps the session cookie and sends the CSRF token."""

    def __init__(self, ip: str = "127.0.0.1"):
        if RUST_SERVER:
            # The Rust server sees a real connection from 127.0.0.1; the Host
            # header matches the Python tests' https://cases.local.
            self.http = httpx.Client(base_url=_rust["url"], verify=False, headers={"Host": "cases.local"})
        else:
            self.http = TestClient(main.app, base_url="https://cases.local", client=(ip, 50000))
        self.csrf = ""

    def request(self, method: str, url: str, **kw):
        headers = kw.pop("headers", {})
        if method not in ("GET", "HEAD") and self.csrf:
            headers.setdefault("X-CSRF-Token", self.csrf)
        r = self.http.request(method, url, headers=headers, **kw)
        if r.content and r.headers.get("content-type", "").startswith("application/json"):
            body = r.json()
            if isinstance(body, dict) and body.get("csrf"):
                self.csrf = body["csrf"]
        return r

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def put(self, url, **kw):
        return self.request("PUT", url, **kw)

    def patch(self, url, **kw):
        return self.request("PATCH", url, **kw)

    def delete(self, url, **kw):
        return self.request("DELETE", url, **kw)

    def sign_in(self, username: str, password: str):
        return self.post("/api/session", json={"username": username, "password": password})

    def upload(self, case_id: str, path: str, content: bytes):
        return self.put(f"/api/cases/{case_id}/files", params={"path": path}, content=content)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    c = settings.Settings(tmp_path / "data", "local", "test")
    monkeypatch.setattr(main.app.state, "rt", runtime.Runtime(c))
    proc = start_rust(c.data_dir) if RUST_SERVER else None
    yield c
    if proc:
        proc.terminate()
        proc.wait()


@pytest.fixture
def rt(cfg):
    return main.app.state.rt


def write_admin(cfg, username="fred", password_hash=LAUNCHER_HASH):
    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    cfg.admin_account_file.write_text(json.dumps({"username": username, "password_hash": password_hash, "updated_at": "2026-10-10T12:00:00Z"}))


@pytest.fixture
def admin(cfg):
    write_admin(cfg)
    c = Client()
    assert c.sign_in("fred", ADMIN_PASSWORD).status_code == 200
    return c


@pytest.fixture
def people(admin):
    """Make a person and return a signed-in client for them."""

    def make(username: str, role: str, case_ids=(), ip="127.0.0.1"):
        r = admin.post("/api/users", json={"username": username, "display_name": username.title(), "role": role, "case_ids": list(case_ids)})
        assert r.status_code == 201, r.text
        temp = r.json()["temporary_password"]
        c = Client(ip)
        assert c.sign_in(username, temp).status_code == 200
        r = c.post("/api/session/password", json={"current_password": temp, "new_password": f"{username} chooses a long password"})
        assert r.status_code == 200, r.text
        c.user_id = r.json()["user"]["id"]
        return c

    return make


@pytest.fixture
def new_case(admin):
    def make(name="Smith v Jones", **extra):
        r = admin.post("/api/cases", json={"name": name, **extra})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    return make
