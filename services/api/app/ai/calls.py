"""Ask and Review requests, in two steps, so nothing reaches an AI service
before the person has seen exactly what will be sent.

1. prepare: choose the passages, build the request, and save it as a
   "prepared" call. The person sees the provider, the model and every
   passage.
2. send: only a prepared call can be sent, only by the person who prepared
   it, within half an hour. For a provider off this machine the browser
   must also say the person confirmed. The audit entry is written before
   the request goes out, so even a request that never returns is logged.

Every passage comes from the one case's own index and file list, so a
request can't carry another case's text."""

import json
import logging
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from .. import access, audit, search
from ..index.query import QueryError
from ..runtime import Runtime
from . import prompts, providers
from .providers import ProviderError

log = logging.getLogger("ai")

EXPIRES = timedelta(minutes=30)
ASK_PASSAGES = 12
SEARCH_PASSAGES = 40  # relevance over many documents: the best matches
TAG_PASSAGES = 3  # per document, for tag suggestions
KINDS = ("ask", *prompts.REVIEWS)


def choose_provider(db: sqlite3.Connection, rt: Runtime, use: str, provider_id: str | None) -> providers.Provider:
    all_ = providers.load_all(db, rt.cfg)
    wanted = provider_id or providers.defaults(db)[use]
    p = next((p for p in all_ if p.id == wanted and p.ready), None)
    if p is None and not provider_id:
        p = next((p for p in all_ if p.ready), None)
    if p is None:
        if provider_id:
            raise ProviderError("That AI service isn't set up. Choose another, or ask the Admin to set it up in Settings.")
        raise ProviderError("No AI service is set up yet. The Admin can set one up in Settings.")
    if p.id == "ollama" and providers.is_cloud_model(p.model):
        raise ProviderError("Ollama's cloud models are turned off in this app, so case text stays on this computer. The Admin can choose a local model in Settings.")
    return p


# --- choosing the passages ------------------------------------------------------------------


def _scope_files(db: sqlite3.Connection, case_id: str, scope: dict) -> list[sqlite3.Row]:
    where, args = ["case_id = ?", "status IN ('converted', 'needs_ocr')", "passages > 0"], [case_id]
    if scope["type"] == "file":
        where.append("id = ?")
        args.append(scope["file_id"])
    elif scope["type"] == "folder":
        folder = scope["folder"].strip("/")
        where.append("(folder = ? OR substr(folder, 1, ?) = ?)")
        args += [folder, len(folder) + 1, folder + "/"]
    return db.execute(
        f"SELECT id, name, folder, doc_date, passages FROM files WHERE {' AND '.join(where)} ORDER BY COALESCE(doc_date, '9999'), folder, name",
        args,
    ).fetchall()


def _round_robin(rt: Runtime, case_id: str, files: list[sqlite3.Row], budget: int, per_file: int | None = None) -> list[dict]:
    """Passages from the start of each document in turn, so every document
    is represented before any one takes the whole budget."""
    index = rt.index(case_id)
    loaded: dict[str, list[dict]] = {}
    picked: list[dict] = []
    used, rnd = 0, 0
    while True:
        added = False
        for f in files:
            if f["id"] not in loaded:
                loaded[f["id"]] = index.passages(f["id"])
            ps = loaded[f["id"]]
            if rnd >= len(ps) or (per_file is not None and rnd >= per_file):
                continue
            p = ps[rnd]
            if used + len(p["text"]) > budget:
                return picked
            picked.append({**p, "file_id": f["id"]})
            used += len(p["text"])
            added = True
        if not added:
            return picked
        rnd += 1


def _searched(rt: Runtime, db: sqlite3.Connection, case_id: str, text: str, scope: dict, limit: int, budget: int) -> list[dict]:
    """The passages a search for the question finds, best first: the top
    passage of each document, then the second, and so on."""
    filters = search.Filters(folder=scope.get("folder") if scope["type"] == "folder" else None)
    try:
        found = search.run(rt, db, case_id, text, "hybrid", filters, limit=25)
    except QueryError:
        found = search.run(rt, db, case_id, re.sub(r'["*()\-]', " ", text), "hybrid", filters, limit=25)
    results = found["results"]
    if scope["type"] == "file":
        results = [r for r in results if r["file"]["id"] == scope["file_id"]]
    order: list[int] = []
    for depth in range(3):
        order += [r["hits"][depth]["passage"] for r in results if len(r["hits"]) > depth]
    texts = rt.index(case_id).by_ids(order[: limit * 2])
    picked, used = [], 0
    for pid in order:
        p = texts.get(pid)
        if p is None:
            continue
        if used + len(p["text"]) > budget or len(picked) >= limit:
            break
        picked.append(p)
        used += len(p["text"])
    return picked


def _number(db: sqlite3.Connection, case_id: str, picked: list[dict]) -> tuple[list[dict], list[dict]]:
    """Number the passages [1], [2], ... in document order, and describe the documents."""
    ids = list(dict.fromkeys(p["file_id"] for p in picked))
    if not ids:
        return [], []
    rows = {r["id"]: r for r in db.execute(
        f"SELECT id, name, folder, doc_date FROM files WHERE case_id = ? AND id IN ({','.join('?' * len(ids))})", [case_id, *ids])}
    keep = [i for i in ids if i in rows]  # guard: only this case's files
    position = {fid: k for k, fid in enumerate(keep)}
    docs = [{"ref": f"D{k + 1}", "file_id": fid, "name": rows[fid]["name"],
             "path": "/".join(x for x in (rows[fid]["folder"], rows[fid]["name"]) if x), "date": (rows[fid]["doc_date"] or "")[:10]}
            for k, fid in enumerate(keep)]
    picked = sorted((p for p in picked if p["file_id"] in position), key=lambda p: (position[p["file_id"]], p["seq"]))
    sources = []
    for n, p in enumerate(picked, start=1):
        d = docs[position[p["file_id"]]]
        page = p["page"] if p["page"] is not None and p["page"] >= 1 else None
        sources.append({"n": n, "passage": p["id"], "file_id": p["file_id"], "name": d["name"], "path": d["path"],
                        "page": page, "heading": p["heading"], "text": p["text"]})
    return sources, docs


def _check_scope(db: sqlite3.Connection, case_id: str, scope: dict) -> dict:
    kind = scope.get("type") or "case"
    if kind == "file":
        fid = str(scope.get("file_id") or "")
        if not db.execute("SELECT 1 FROM files WHERE id = ? AND case_id = ?", (fid, case_id)).fetchone():
            raise HTTPException(404, "That file isn't in this case.")
        return {"type": "file", "file_id": fid}
    if kind == "folder":
        folder = str(scope.get("folder") or "").strip("/")
        if not folder:
            return {"type": "case"}
        return {"type": "folder", "folder": folder}
    return {"type": "case"}


def prepare(rt: Runtime, user: dict, case_id: str, kind: str, question: str, scope: dict, provider_id: str | None) -> dict:
    question = " ".join(question.split())
    if kind not in KINDS:
        raise HTTPException(400, "Choose what kind of review to run.")
    if kind in ("ask", "relevance") and len(question) < 3:
        raise HTTPException(400, "Type a question first." if kind == "ask" else "Describe the issue first.")
    with rt.db.connect() as db:
        access.visible_case(db, user, case_id)
        scope = _check_scope(db, case_id, scope)
        p = choose_provider(db, rt, "ask" if kind == "ask" else "review", provider_id)
        budget = p.budget()
        files = _scope_files(db, case_id, scope)
        total_passages = sum(f["passages"] for f in files)
        if kind == "ask":
            picked = _searched(rt, db, case_id, question, scope, ASK_PASSAGES, budget)
        elif kind == "relevance" and scope["type"] != "file":
            picked = _searched(rt, db, case_id, question, scope, SEARCH_PASSAGES, budget)
        elif kind == "tags":
            picked = _round_robin(rt, case_id, files, budget, per_file=TAG_PASSAGES)
        else:
            picked = _round_robin(rt, case_id, files, budget)
        sources, docs = _number(db, case_id, picked)
    if not sources:
        if kind == "ask":
            raise HTTPException(404, "Nothing in this case matches that question. Try other words, or check the files have finished converting.")
        raise HTTPException(404, "There's nothing to review here yet. Files can be reviewed once they've been converted and indexed.")

    note = ""
    if kind not in ("ask", "relevance", "tags") and len(sources) < total_passages:
        if len(files) == 1:
            note = f"Only the first {len(sources)} of {total_passages} passages fit in one request, so the review covers the start of the document."
        else:
            note = (f"Only {len(sources)} of {total_passages} passages fit in one request, so the review covers the start of each document"
                    f"{'' if len(docs) == len(files) else f' ({len(docs)} of {len(files)} documents)'}. Choose a folder or a file for a fuller review.")
    elif kind == "relevance" and scope["type"] != "file":
        note = f"Includes the {len(sources)} passages that best match the issue, from {len(docs)} documents."

    call_id = uuid.uuid4().hex
    with rt.db.transaction() as db:
        db.execute(
            """INSERT INTO ai_calls (id, case_id, user_id, username, kind, scope, question, provider, model, external, sources, docs, note, state, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'prepared', ?)""",
            (call_id, case_id, user["id"], user["username"], kind, json.dumps(scope), question, p.id, p.model, int(p.external),
             json.dumps(sources), json.dumps(docs), note, audit.now()),
        )
        row = db.execute("SELECT * FROM ai_calls WHERE id = ?", (call_id,)).fetchone()
    return view(row, p)


def _get(db: sqlite3.Connection, user: dict, case_id: str, call_id: str) -> sqlite3.Row:
    access.visible_case(db, user, case_id)
    row = db.execute("SELECT * FROM ai_calls WHERE id = ? AND case_id = ?", (call_id, case_id)).fetchone()
    if row is None or (row["kind"] == "ask" and row["user_id"] != user["id"]):
        raise HTTPException(404, "That request no longer exists.")
    return row


def _describe(row: sqlite3.Row, name: str) -> str:
    sources, docs = json.loads(row["sources"]), json.loads(row["docs"])
    where = "off this machine" if row["external"] else "on this computer"
    what = "question" if row["kind"] == "ask" else prompts.REVIEWS[row["kind"]].lower()
    return f"{what}; {name} {row['model']} ({where}); {len(sources)} passages from {len(docs)} documents"


def send(rt: Runtime, user: dict, case_id: str, call_id: str, confirmed: bool, ip: str) -> dict:
    with rt.db.transaction() as db:
        row = _get(db, user, case_id, call_id)
        if row["user_id"] != user["id"]:
            raise HTTPException(404, "That request no longer exists.")
        if row["state"] != "prepared":
            raise HTTPException(409, "This request has already been sent or cancelled.")
        created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - created > EXPIRES:
            db.execute("UPDATE ai_calls SET state = 'cancelled', error = 'expired', finished_at = ? WHERE id = ?", (audit.now(), call_id))
            raise HTTPException(409, "This request was prepared more than half an hour ago. Run it again to see what will be sent.")
        p = providers.load(db, rt.cfg, row["provider"])
        if p is None or not p.ready or p.model != row["model"] or p.external != bool(row["external"]):
            db.execute("UPDATE ai_calls SET state = 'cancelled', error = 'settings changed', finished_at = ? WHERE id = ?", (audit.now(), call_id))
            raise HTTPException(409, "The AI settings changed after this request was prepared. Run it again.")
        if p.external and not confirmed:
            raise HTTPException(409, f"Confirm that this may be sent to {p.name} first.")
        db.execute("UPDATE ai_calls SET state = 'sending', sent_at = ? WHERE id = ?", (audit.now(), call_id))
        # Logged before anything leaves, so the log shows every request even if
        # the app stops while waiting for the answer.
        audit.record(db, action="ai.send", user=user, case_id=case_id, target=p.name, detail=_describe(row, p.name), ip=ip)

    kind, sources, docs = row["kind"], json.loads(row["sources"]), json.loads(row["docs"])
    try:
        key = providers.api_key(p, rt.cfg)
        reply = providers.complete(p, key, prompts.SYSTEM[kind], prompts.request(kind, row["question"], sources, docs), prompts.MAX_TOKENS[kind])
        result: dict = {"cited": prompts.cited(reply.text, len(sources)), "cut_short": reply.cut_short}
        if kind == "tags":
            try:
                result["suggestions"] = prompts.tag_suggestions(reply.text, docs)
            except (ValueError, TypeError, AttributeError):
                raise ProviderError("The AI's answer couldn't be read as tag suggestions. Try again, or choose another model.") from None
        error = ""
    except ProviderError as exc:
        reply, result, error = None, {}, str(exc)
    except Exception:
        log.exception("AI request to %s failed", p.id)
        reply, result, error = None, {}, "Something went wrong while waiting for the answer. Try again; if it keeps happening, the Admin can test the connection in Settings."

    with rt.db.transaction() as db:
        db.execute(
            """UPDATE ai_calls SET state = ?, answer = ?, result = ?, error = ?, served_model = ?, input_tokens = ?, output_tokens = ?, finished_at = ?
               WHERE id = ?""",
            ("failed" if error else "done", reply.text if reply else "", json.dumps(result), error, reply.model if reply else None,
             reply.input_tokens if reply else None, reply.output_tokens if reply else None, audit.now(), call_id),
        )
        detail = error[:300] if error else f"{reply.model}; {reply.input_tokens or '?'} tokens in, {reply.output_tokens or '?'} out"
        audit.record(db, action="ai.answer", user=user, case_id=case_id, target=p.name, outcome="failed" if error else "ok", detail=detail, ip=ip)
        row = db.execute("SELECT * FROM ai_calls WHERE id = ?", (call_id,)).fetchone()
    return view(row, p)


def cancel(rt: Runtime, user: dict, case_id: str, call_id: str, ip: str) -> None:
    with rt.db.transaction() as db:
        row = _get(db, user, case_id, call_id)
        if row["user_id"] != user["id"] or row["state"] != "prepared":
            return
        db.execute("UPDATE ai_calls SET state = 'cancelled', finished_at = ? WHERE id = ?", (audit.now(), call_id))
        audit.record(db, action="ai.cancel", user=user, case_id=case_id, target=row["provider"], detail=_describe(row, row["provider"]) + "; not sent", ip=ip)


def accept_tag(rt: Runtime, user: dict, case_id: str, call_id: str, file_id: str, tag: str, ip: str) -> dict:
    with rt.db.transaction() as db:
        row = _get(db, user, case_id, call_id)
        case = access.visible_case(db, user, case_id)
        access.require_edit(user, case)
        result = json.loads(row["result"] or "{}")
        suggestion = next((s for s in result.get("suggestions", []) if s["file_id"] == file_id), None)
        if row["kind"] != "tags" or row["state"] != "done" or suggestion is None or tag not in (suggestion["tags"] + [suggestion["document_type"]]):
            raise HTTPException(404, "That suggestion no longer exists.")
        f = db.execute("SELECT name, folder, tags FROM files WHERE id = ? AND case_id = ?", (file_id, case_id)).fetchone()
        if f is None:
            raise HTTPException(404, "That file isn't in this case any more.")
        tags = json.loads(f["tags"])
        if tag not in tags:
            tags.append(tag)
            db.execute("UPDATE files SET tags = ? WHERE id = ?", (json.dumps(tags), file_id))
            audit.record(db, action="file.tags", user=user, case_id=case_id, target="/".join(x for x in (f["folder"], f["name"]) if x),
                         detail=f"{', '.join(tags)} (accepted AI suggestion “{tag}”)", ip=ip)
        accepted = result.setdefault("accepted", {}).setdefault(file_id, [])
        if tag not in accepted:
            accepted.append(tag)
        db.execute("UPDATE ai_calls SET result = ? WHERE id = ?", (json.dumps(result), call_id))
        row = db.execute("SELECT * FROM ai_calls WHERE id = ?", (call_id,)).fetchone()
        p = providers.load(db, rt.cfg, row["provider"])
    return view(row, p)


def history(rt: Runtime, user: dict, case_id: str, kind: str) -> list[dict]:
    """Reviews are shared with everyone in the case; questions are each
    person's own."""
    with rt.db.connect() as db:
        access.visible_case(db, user, case_id)
        if kind == "ask":
            rows = db.execute("SELECT * FROM ai_calls WHERE case_id = ? AND kind = 'ask' AND user_id = ? AND state IN ('done', 'failed') ORDER BY created_at DESC LIMIT 20", (case_id, user["id"]))
        else:
            rows = db.execute("SELECT * FROM ai_calls WHERE case_id = ? AND kind != 'ask' AND state IN ('done', 'failed') ORDER BY created_at DESC LIMIT 50", (case_id,))
        known = {p.id: p for p in providers.load_all(db, rt.cfg)}
        return [view(r, known.get(r["provider"])) for r in rows]


def get(rt: Runtime, user: dict, case_id: str, call_id: str) -> dict:
    with rt.db.connect() as db:
        row = _get(db, user, case_id, call_id)
        p = providers.load(db, rt.cfg, row["provider"])
    return view(row, p)


def view(row: sqlite3.Row, p: providers.Provider | None) -> dict:
    result = json.loads(row["result"] or "{}")
    sources = json.loads(row["sources"])
    return {
        "id": row["id"],
        "kind": row["kind"],
        "label": "Question" if row["kind"] == "ask" else prompts.REVIEWS[row["kind"]],
        "state": row["state"],
        "question": row["question"],
        "scope": json.loads(row["scope"]),
        "provider": {"id": row["provider"], "name": p.name if p else row["provider"], "destination": p.destination if p else row["provider"],
                     "model": row["model"], "external": bool(row["external"])},
        "served_model": row["served_model"],
        "sources": sources,
        "documents": len(json.loads(row["docs"])),
        "note": row["note"],
        "answer": row["answer"],
        "cited": result.get("cited", []),
        "cut_short": result.get("cut_short", False),
        "suggestions": result.get("suggestions"),
        "accepted": result.get("accepted", {}),
        "error": row["error"],
        "username": row["username"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
    }


def tidy(rt: Runtime) -> None:
    """Requests prepared but never sent are dropped after a day; they hold
    copies of case text."""
    with rt.db.transaction() as db:
        db.execute("DELETE FROM ai_calls WHERE state IN ('prepared', 'cancelled') AND created_at < strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-1 day')")
        # A request whose answer never came back (the app stopped meanwhile).
        db.execute("UPDATE ai_calls SET state = 'failed', error = 'The app stopped before the answer came back. Run it again.' WHERE state = 'sending' AND sent_at < strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-1 hour')")
