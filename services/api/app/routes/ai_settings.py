"""Settings for the AI services (Admin only): which providers are on, their
address, model and API key, the default for Ask and for Review, local
chat models, and the search (embedding) model.

API keys go in and never come out: the screen only ever sees whether a key
is saved and its last four characters."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .. import accounts, audit, jobs, runtime
from ..ai import local, providers, secrets
from ..ai.providers import ProviderError

router = APIRouter(prefix="/api/settings/ai")


class ProviderEdit(BaseModel):
    enabled: bool | None = None
    base_url: str | None = Field(None, max_length=500)
    model: str | None = Field(None, max_length=200)
    api_key: str | None = Field(None, max_length=500)
    remove_key: bool = False


class Defaults(BaseModel):
    ask: str = Field("", max_length=20)
    review: str = Field("", max_length=20)


class Pull(BaseModel):
    model: str = Field(max_length=100)


def _settings(rt: runtime.Runtime) -> dict:
    with rt.db.connect() as db:
        all_ = providers.load_all(db, rt.cfg)
        defaults = providers.defaults(db)
        pulls = {r["model"]: dict(r) for r in db.execute(
            "SELECT json_extract(options, '$.model') AS model, state, done, total, error FROM jobs WHERE kind = 'model' AND state IN ('queued', 'running', 'failed') ORDER BY id")}
        files = db.execute("SELECT COUNT(*) FROM files WHERE status IN ('converted', 'needs_ocr')").fetchone()[0]
    have = local.installed(rt)
    return {
        "providers": [p.view() for p in all_],
        "defaults": defaults,
        "local": {
            "running": have is not None,
            "installed": [m for m in (have or []) if m.split(":")[0] != rt.cfg.embed_model.split(":")[0]],
            "available": [
                {"model": m, "about": about, "installed": have is not None and (m in have),
                 "download": pulls.get(m)}
                for m, about in providers.LOCAL_MODELS.items()
            ],
        },
        "embedding": {"model": rt.cfg.embed_model, "files": files},
    }


@router.get("")
async def get_settings(user: dict = Depends(accounts.admin_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    return await run_in_threadpool(_settings, rt)


@router.put("/providers/{provider_id}")
def edit_provider(provider_id: str, body: ProviderEdit, request: Request, user: dict = Depends(accounts.admin_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    changes = []
    with rt.db.transaction() as db:
        p = providers.load(db, rt.cfg, provider_id)
        if p is None:
            raise HTTPException(404, "That AI service isn't one this app knows.")
        try:
            if body.base_url is not None and p.kind.url_editable:
                url = providers.check_url(body.base_url, p.kind.needs_key) if body.base_url.strip() else p.kind.base_url
                if url != p.base_url:
                    p.base_url, p.test_ok = url, None
                    changes.append(f"address {url}")
            if body.model is not None:
                model = body.model.strip()
                if p.id == "ollama" and providers.is_cloud_model(model):
                    raise ProviderError("Ollama's cloud models are turned off in this app, so case text stays on this computer. Choose a local model.")
                if model != p.model:
                    p.model, p.test_ok = model, None
                    changes.append(f"model {model or '(none)'}")
        except ProviderError as exc:
            raise HTTPException(400, str(exc)) from exc
        if body.remove_key and p.key is not None:
            p.key, p.key_hint, p.test_ok = None, "", None
            changes.append("API key removed")
        elif body.api_key and body.api_key.strip():
            key = body.api_key.strip()
            if any(c.isspace() for c in key) or len(key) < 8:
                raise HTTPException(400, "That doesn't look like an API key. Copy it again from the provider's website.")
            if not p.kind.needs_key:
                raise HTTPException(400, f"{p.kind.label} doesn't use an API key.")
            changes.append("API key replaced" if p.key is not None else "API key saved")
            p.key, p.key_hint, p.test_ok = secrets.protect(key, rt.cfg.run_dir), secrets.hint(key), None
        if body.enabled is not None and body.enabled != p.enabled:
            p.enabled = body.enabled
            changes.append("turned on" if body.enabled else "turned off")
        if changes:
            providers.save(db, rt.cfg, p, user)
            audit.record(db, action="settings.ai", user=user, target=p.kind.label, detail="; ".join(changes), ip=accounts.client_ip(request))
        p = providers.load(db, rt.cfg, provider_id)
    return p.view()


@router.post("/providers/{provider_id}/test")
async def test_provider(provider_id: str, request: Request, user: dict = Depends(accounts.admin_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    """Check the address, key and model. Sends no case text."""

    def go() -> dict:
        with rt.db.connect() as db:
            p = providers.load(db, rt.cfg, provider_id)
        if p is None:
            raise HTTPException(404, "That AI service isn't one this app knows.")
        try:
            result = providers.test(p, providers.api_key(p, rt.cfg))
        except ProviderError as exc:
            result = {"ok": False, "message": str(exc), "models": []}
        with rt.db.transaction() as db:
            db.execute("UPDATE ai_providers SET tested_at = ?, test_ok = ? WHERE id = ?", (audit.now(), int(result["ok"]), provider_id))
            audit.record(db, action="settings.ai_test", user=user, target=p.kind.label, outcome="ok" if result["ok"] else "failed",
                         detail=result["message"][:300], ip=accounts.client_ip(request))
            p = providers.load(db, rt.cfg, provider_id)
        return {**result, "provider": p.view()}

    return await run_in_threadpool(go)


@router.put("/defaults")
def set_defaults(body: Defaults, request: Request, user: dict = Depends(accounts.admin_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    with rt.db.transaction() as db:
        known = {p.id for p in providers.load_all(db, rt.cfg)}
        for use, value in (("ask", body.ask), ("review", body.review)):
            if value and value not in known:
                raise HTTPException(400, "Choose one of the AI services listed.")
            db.execute("INSERT INTO ai_settings (name, value) VALUES (?, ?) ON CONFLICT (name) DO UPDATE SET value = excluded.value", (f"{use}_provider", value))
        audit.record(db, action="settings.ai", user=user, target="defaults", detail=f"Ask: {body.ask or '(first available)'}; Review: {body.review or '(first available)'}", ip=accounts.client_ip(request))
        return providers.defaults(db)


@router.post("/local-models")
def download_model(body: Pull, request: Request, user: dict = Depends(accounts.admin_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    if body.model not in providers.LOCAL_MODELS:
        raise HTTPException(400, "Choose one of the models listed.")
    with rt.db.transaction() as db:
        if db.execute("SELECT 1 FROM jobs WHERE kind = 'model' AND state IN ('queued', 'running') AND json_extract(options, '$.model') = ?", (body.model,)).fetchone():
            return {"queued": False}
        db.execute("DELETE FROM jobs WHERE kind = 'model' AND state = 'failed' AND json_extract(options, '$.model') = ?", (body.model,))
        jobs.enqueue(db, "model", None, options={"model": body.model}, user=user)
        audit.record(db, action="ai.model_download", user=user, target=body.model, detail="download started", ip=accounts.client_ip(request))
    return {"queued": True}


@router.post("/reindex")
def reindex_all(request: Request, user: dict = Depends(accounts.admin_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    """Index every converted file in every case again."""
    with rt.db.transaction() as db:
        rows = db.execute("SELECT id, case_id FROM files WHERE status IN ('converted', 'needs_ocr')").fetchall()
        for r in rows:
            jobs.enqueue(db, "index", r["case_id"], r["id"], user=user)
        audit.record(db, action="settings.reindex_all", user=user, detail=f"{len(rows)} files", ip=accounts.client_ip(request))
    return {"queued": len(rows)}
