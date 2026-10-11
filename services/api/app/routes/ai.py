"""Ask and Review in a case: prepare a request (see what will be sent),
send or cancel it, read past answers, accept tag suggestions."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .. import accounts, runtime
from ..ai import calls, providers
from ..ai.providers import ProviderError

router = APIRouter(prefix="/api")


class Scope(BaseModel):
    type: Literal["case", "folder", "file"] = "case"
    folder: str = Field("", max_length=1024)
    file_id: str = Field("", max_length=32)


class Prepare(BaseModel):
    kind: Literal["ask", "summary", "chronology", "relevance", "extract", "tags"]
    question: str = Field("", max_length=2000)
    scope: Scope = Scope()
    provider: str | None = Field(None, max_length=20)


class Send(BaseModel):
    confirmed: bool = False


class Accept(BaseModel):
    file_id: str = Field(max_length=32)
    tag: str = Field(max_length=50)


@router.get("/ai/providers")
def available(user: dict = Depends(accounts.current_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    """The AI services anyone can choose for Ask and Review: name, model and
    whether it sends data off this machine. Nothing else about the setup."""
    with rt.db.connect() as db:
        ready = [p for p in providers.load_all(db, rt.cfg) if p.ready and not (p.id == "ollama" and providers.is_cloud_model(p.model))]
        defaults = providers.defaults(db)
    ids = [p.id for p in ready]
    pick = lambda use: defaults[use] if defaults[use] in ids else (ids[0] if ids else "")  # noqa: E731
    return {
        "providers": [{"id": p.id, "name": p.name, "destination": p.destination, "model": p.model, "external": p.external} for p in ready],
        "defaults": {"ask": pick("ask"), "review": pick("review")},
    }


@router.post("/cases/{case_id}/ai")
async def prepare(case_id: str, body: Prepare, user: dict = Depends(accounts.current_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    def go() -> dict:
        try:
            return calls.prepare(rt, user, case_id, body.kind, body.question, body.scope.model_dump(), body.provider)
        except ProviderError as exc:
            raise HTTPException(409, str(exc)) from exc

    return await run_in_threadpool(go)


@router.post("/cases/{case_id}/ai/{call_id}/send")
async def send(case_id: str, call_id: str, body: Send, request: Request, user: dict = Depends(accounts.current_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    ip = accounts.client_ip(request)
    return await run_in_threadpool(calls.send, rt, user, case_id, call_id, body.confirmed, ip)


@router.post("/cases/{case_id}/ai/{call_id}/cancel")
def cancel(case_id: str, call_id: str, request: Request, user: dict = Depends(accounts.current_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    calls.cancel(rt, user, case_id, call_id, accounts.client_ip(request))
    return {"ok": True}


@router.get("/cases/{case_id}/ai")
def history(case_id: str, kind: str = Query("review", pattern="^(ask|review)$"), user: dict = Depends(accounts.current_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    return {"items": calls.history(rt, user, case_id, kind)}


@router.get("/cases/{case_id}/ai/{call_id}")
def get_call(case_id: str, call_id: str, user: dict = Depends(accounts.current_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    return calls.get(rt, user, case_id, call_id)


@router.post("/cases/{case_id}/ai/{call_id}/accept")
def accept(case_id: str, call_id: str, body: Accept, request: Request, user: dict = Depends(accounts.current_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    return calls.accept_tag(rt, user, case_id, call_id, body.file_id, " ".join(body.tag.split()), accounts.client_ip(request))
