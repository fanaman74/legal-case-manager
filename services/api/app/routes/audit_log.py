"""The audit log (Admin only), with a tamper check of the hash chain."""

from fastapi import APIRouter, Depends, Query

from .. import accounts, audit, runtime

router = APIRouter(prefix="/api/audit")


@router.get("")
def audit_log(
    username: str | None = Query(None, max_length=64),
    action: str | None = Query(None, max_length=64, description="An action, or a prefix ending in '.' such as 'file.'"),
    case_id: str | None = Query(None, max_length=32),
    since: str | None = Query(None, max_length=40, description="ISO date or time, inclusive"),
    until: str | None = Query(None, max_length=40, description="ISO date or time, exclusive"),
    before: int | None = Query(None, ge=1, description="Page: entries older than this id"),
    limit: int = Query(200, ge=1, le=1000),
    _: dict = Depends(accounts.admin_user),
    rt: runtime.Runtime = Depends(runtime.get),
) -> dict:
    where, args = ["a.id < ?"], [before or 2**62]
    if username:
        where.append("a.username = ? COLLATE NOCASE")
        args.append(username)
    if action:
        if action.endswith("."):
            where.append("substr(a.action, 1, ?) = ?")
            args += [len(action), action]
        else:
            where.append("a.action = ?")
            args.append(action)
    if case_id:
        where.append("a.case_id = ?")
        args.append(case_id)
    if since:
        where.append("a.at >= ?")
        args.append(since)
    if until:
        where.append("a.at < ?")
        args.append(until)
    with rt.db.connect() as db:
        rows = db.execute(
            f"""SELECT a.id, a.at, a.username, a.action, a.outcome, a.case_id, c.name AS case_name, a.target, a.detail, a.ip
                FROM audit a LEFT JOIN cases c ON c.id = a.case_id
                WHERE {' AND '.join(where)} ORDER BY a.id DESC LIMIT ?""",
            [*args, limit],
        ).fetchall()
        checked, broken = audit.verify(db)
    return {"items": [dict(r) for r in rows], "tamper_check": {"entries": checked, "intact": broken is None, "first_bad_entry": broken}}
