"""Local chat models in the app's own Ollama: which are downloaded, and
downloading one (a background job, as it can take a while).

Ollama fetches the model from its registry and checks every piece against
the digest in the model's manifest. Only the models listed in
providers.LOCAL_MODELS can be downloaded from the app, and never a cloud
model (the service also runs with OLLAMA_NO_CLOUD=1)."""

import json
import logging
import time

import httpx

from .. import audit, jobs
from ..runtime import Runtime
from .providers import LOCAL_MODELS, is_cloud_model

log = logging.getLogger("worker.model")


def installed(rt: Runtime) -> list[str] | None:
    """Models in the local service, or None if it isn't running."""
    try:
        r = httpx.get(rt.cfg.ollama_url.rstrip("/") + "/api/tags", timeout=5.0)
        r.raise_for_status()
        return sorted(m["name"] for m in r.json().get("models", []) if not is_cloud_model(m["name"]))
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None


def run_pull(rt: Runtime, job: dict) -> None:
    model = job["options"].get("model", "")
    if model not in LOCAL_MODELS:
        with rt.db.transaction() as db:
            jobs.fail(db, job["id"], "That model isn't one the app can download.")
        return
    error = ""
    last = 0.0
    try:
        with httpx.stream("POST", rt.cfg.ollama_url.rstrip("/") + "/api/pull", json={"model": model, "stream": True},
                          timeout=httpx.Timeout(60.0, read=600.0)) as r:
            if r.status_code >= 400:
                error = f"The Local AI models service refused the download (HTTP {r.status_code})."
            else:
                for line in r.iter_lines():
                    if not line.strip():
                        continue
                    msg = json.loads(line)
                    if msg.get("error"):
                        error = f"The download stopped: {str(msg['error'])[:200]}"
                        break
                    if msg.get("total") and time.monotonic() - last > 2:
                        jobs.progress(rt.db, job["id"], int(msg.get("completed") or 0), int(msg["total"]))
                        last = time.monotonic()
                    if msg.get("status") == "success":
                        break
                else:
                    error = "The download ended before it finished."
    except httpx.ConnectError:
        error = "The Local AI models service isn't running. Start it from the Control Center, then retry this job."
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("model download failed: %s", type(exc).__name__)
        error = "The download was interrupted. Check the internet connection, then retry this job."
    with rt.db.transaction() as db:
        if error:
            jobs.fail(db, job["id"], error)
        else:
            jobs.finish(db, job["id"])
        audit.record(db, action="ai.model_download", user=None, username="worker", outcome="failed" if error else "ok", target=model, detail=error[:300])
