from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_is_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ready_reports_queue_down(monkeypatch, tmp_path):
    from app import main

    monkeypatch.setattr(main, "cfg", main.cfg.__class__(tmp_path, "redis://127.0.0.1:1/0", "local", "test"))
    r = client.get("/ready")
    assert r.status_code == 503
    assert r.json()["checks"] == {"data_dir": True, "queue": False}


def test_no_api_docs_exposed():
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_deploy_mode_validated(monkeypatch):
    import pytest

    from app import settings

    monkeypatch.setenv("DEPLOY_MODE", "staging")
    with pytest.raises(ValueError):
        settings.load()
