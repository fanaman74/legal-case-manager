"""Search in a case, and rebuilding a case's search index."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

from .. import access, accounts, audit, jobs, runtime, search
from ..index.query import QueryError

router = APIRouter(prefix="/api/cases/{case_id}")

_DATE = "^\\d{4}-\\d{2}-\\d{2}$"


@router.get("/search")
async def run_search(
    case_id: str,
    request: Request,
    q: str = Query(..., max_length=500),
    mode: str = Query("hybrid", pattern="^(hybrid|keyword|semantic)$"),
    type: str | None = Query(None, pattern="^(word|pdf|email|text|image)$"),
    folder: str | None = Query(None, max_length=1024),
    date_from: str | None = Query(None, pattern=_DATE),
    date_to: str | None = Query(None, pattern=_DATE),
    tag: str | None = Query(None, max_length=50),
    limit: int = Query(25, ge=1, le=100),
    user: dict = Depends(accounts.current_user),
    rt: runtime.Runtime = Depends(runtime.get),
) -> dict:
    filters = search.Filters(type, folder, date_from, date_to, tag)

    def go() -> dict:
        with rt.db.connect() as db:
            access.visible_case(db, user, case_id)
            try:
                found = search.run(rt, db, case_id, q, mode, filters, limit)
            except QueryError as exc:
                raise HTTPException(400, str(exc)) from exc
            counts = {r["index_state"]: r["n"] for r in db.execute(
                "SELECT index_state, COUNT(*) AS n FROM files WHERE case_id = ? AND status IN ('converted', 'needs_ocr') GROUP BY index_state", (case_id,))}
            waiting = db.execute("SELECT COUNT(*) FROM files WHERE case_id = ? AND status IN ('queued', 'converting')", (case_id,)).fetchone()[0]
        with rt.db.transaction() as db:
            audit.record(db, action="case.search", user=user, case_id=case_id, target=q[:200],
                         detail=f"{mode}; {found['documents']} documents", ip=accounts.client_ip(request))
        found["index"] = {"indexed": counts.get("indexed", 0), "keyword_only": counts.get("keyword_only", 0),
                          "waiting": counts.get("", 0) + waiting, "failed": counts.get("failed", 0)}
        return found

    return await run_in_threadpool(go)


@router.post("/reindex")
def reindex(case_id: str, request: Request, user: dict = Depends(accounts.current_user), rt: runtime.Runtime = Depends(runtime.get)) -> dict:
    """Index every converted file in the case again (after changing the
    embedding model, or if search seems to miss things)."""
    with rt.db.transaction() as db:
        case = access.visible_case(db, user, case_id)
        access.require_edit(user, case)
        rows = db.execute("SELECT id FROM files WHERE case_id = ? AND status IN ('converted', 'needs_ocr')", (case_id,)).fetchall()
        for r in rows:
            jobs.enqueue(db, "index", case_id, r["id"], user=user)
        audit.record(db, action="case.reindex", user=user, case_id=case_id, detail=f"{len(rows)} files", ip=accounts.client_ip(request))
    return {"queued": len(rows)}
