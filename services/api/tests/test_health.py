import pytest
from fastapi.testclient import TestClient

from app import main, settings
from app.main import app

client = TestClient(app, client=("127.0.0.1", 50000))


def test_health_is_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ready_checks_data_dir_and_sqlite(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "cfg", settings.Settings(tmp_path, "local", "test"))
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json()["checks"] == {"data_dir": True, "sqlite_fts5": True}


def test_ready_reports_unwritable_data_dir(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(main, "cfg", settings.Settings(blocker / "data", "local", "test"))
    r = client.get("/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["data_dir"] is False


def test_no_api_docs_exposed():
    # Unknown addresses get the app's page (its router shows "no page here"), never an API description.
    for path in ("/docs", "/redoc", "/openapi.json"):
        r = client.get(path)
        assert "openapi" not in r.text.lower() and "swagger" not in r.text.lower()
        assert r.headers["content-type"].startswith("text/html")


def test_unknown_api_address_is_json_404():
    r = client.get("/api/nothing-here")
    assert r.status_code == 404 and r.json() == {"detail": "Not found"}


def test_pages_served_with_security_headers(monkeypatch, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>")
    (tmp_path / "assets" / "app-1234.js").write_text("console.log(1)")
    monkeypatch.setattr(main, "WEB_DIR", tmp_path)
    for path in ("/", "/cases/0123456789abcdef0123456789abcdef", "/people"):
        r = client.get(path)
        assert r.status_code == 200 and "id=root" in r.text
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert r.headers["x-frame-options"] == "DENY"
    r = client.get("/assets/app-1234.js")
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
    (tmp_path / "secret.txt").write_text("not an asset")
    for sneaky in ("/assets/..%2Fsecret.txt", "/assets/..%5Csecret.txt", "/assets/.hidden"):
        assert "not an asset" not in client.get(sneaky).text
    assert client.get("/assets/missing.js").status_code == 404


def test_deploy_mode_validated(monkeypatch):
    monkeypatch.setenv("DEPLOY_MODE", "cloud")
    with pytest.raises(ValueError):
        settings.load()


@pytest.mark.parametrize(
    "host,ok",
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("192.168.1.20", True),
        ("10.4.0.9", True),
        ("172.20.1.1", True),
        ("fe80::1", True),
        ("::ffff:192.168.1.5", True),
        ("8.8.8.8", False),
        ("172.32.0.1", False),
        ("2001:db8::1", False),
        ("", False),
        (None, False),
        ("testclient", False),
    ],
)
def test_is_local_client(host, ok):
    assert main.is_local_client(host) is ok


def test_public_clients_refused_locally(monkeypatch):
    public = TestClient(app, client=("8.8.8.8", 5000))
    r = public.get("/health")
    assert r.status_code == 403
    assert "office network" in r.text
    local = TestClient(app, client=("192.168.1.20", 5000))
    assert local.get("/health").status_code == 200


def test_serve_requires_tls_locally(tmp_path):
    from app import serve

    with pytest.raises(SystemExit):
        serve.options(settings.Settings(tmp_path, "local", "t"))
    opts = serve.options(settings.Settings(tmp_path, "local", "t", tls_cert="c", tls_key="k"))
    assert opts["ssl_certfile"] == "c" and opts["proxy_headers"] is False


def test_health_checks_not_logged():
    import logging

    from app.serve import QuietHealthChecks

    f = QuietHealthChecks()
    rec = lambda path: logging.LogRecord("uvicorn.access", 20, "", 0, '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:1", "GET", path, "1.1", 200), None)
    assert f.filter(rec("/health")) is False
    assert f.filter(rec("/cases")) is True
