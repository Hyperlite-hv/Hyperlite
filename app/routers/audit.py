from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core import access_log, audit, cluster_view
from app.core.audit import log_action
from app.core.csv_export import csv_lines
from app.core.security import require_role
from app.services import audit_service

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
async def list_audit(
    limit: int = 200,
    action: str | None = None,
    result: str | None = None,
    username: str | None = None,
    resource: str | None = None,
    since: str | None = None,  # ISO 8601, filters on timestamp >= since
    until: str | None = None,
    # In a cluster: every member's entries, each marked with the node that holds it ("on"), and the members that
    # did not answer (app/core/cluster_view.py). Outside a cluster: this node's entries, in the same shape.
    cluster: bool = False,
    user: dict = Depends(require_role("admin")),
):
    f = audit_service.filters(action, result, username, resource, since, until)
    rows = await audit_service.query(f, limit)
    if not cluster:
        return rows
    answers, missing = await run_in_threadpool(cluster_view.gather, user["username"], "/audit", {**f, "limit": limit})
    return {"items": cluster_view.merge(rows, answers, "timestamp", limit=limit), "unreachable": missing}


@router.get("/count")
async def count_audit(
    action: str | None = None,
    result: str | None = None,
    username: str | None = None,
    resource: str | None = None,
    since: str | None = None,
    until: str | None = None,
    cluster: bool = False,
    user: dict = Depends(require_role("admin")),
):
    """Number of entries matching the same filters as GET /audit, which only returns the
    most recent ones: lets the page say "300 entries, showing the 100 most recent". cluster: every member's, summed
    over the members that answered."""
    f = audit_service.filters(action, result, username, resource, since, until)
    total = await audit_service.count(f)
    if cluster:
        answers, _ = await run_in_threadpool(cluster_view.gather, user["username"], "/audit/count", f)
        total += sum(int(b.get("total") or 0) for b in answers.values() if isinstance(b, dict))
    return {"total": total}


@router.get("/actions")
async def list_audit_actions(cluster: bool = False, user: dict = Depends(require_role("admin"))):
    """Distinct action types already seen in the log. Feeds the "Action type"
    filter in the frontend without a hard-coded list that would drift as new
    endpoints are added. cluster: seen on any member."""
    actions = await audit_service.actions()
    if not cluster:
        return actions
    answers, _ = await run_in_threadpool(cluster_view.gather, user["username"], "/audit/actions", {})
    seen = set(actions)
    for body in answers.values():
        seen |= {a for a in body if isinstance(a, str)} if isinstance(body, list) else set()
    return sorted(seen)


@router.get("/export.csv")
def export_audit(
    action: str | None = None,
    result: str | None = None,
    username: str | None = None,
    resource: str | None = None,
    since: str | None = None,
    until: str | None = None,
    user: dict = Depends(require_role("admin")),
):
    """EVERY entry matching the same filters as GET /audit, newest first, as CSV. The
    page only loads the most recent entries: exporting those would pass a partial
    audit log off as a complete one."""
    filters = {
        k: v
        for k, v in {
            "action": action,
            "result": result,
            "username": username,
            "resource": resource,
            "since": since,
            "until": until,
        }.items()
        if v
    }
    log_action(user["username"], "export_audit", "audit_log", "success", str(filters) if filters else None)
    rows = audit_service.export_rows(audit_service.filters(action, result, username, resource, since, until))
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        csv_lines(("timestamp", "user", "action", "resource", "result", "error", "ip"), rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="hyperlite-audit-{stamp}.csv"'},
    )


class Retention(BaseModel):
    access_log_days: int | None = None
    audit_days: int | None = None


def _retention():
    return {
        "access_log_days": access_log.days(),
        "audit_days": audit.retention_days(),
        "access_log_dir": str(access_log.log_dir()),
    }


@router.get("/retention")
def get_retention(user: dict = Depends(require_role("admin"))):
    """How long the users' addresses are kept: the access log's daily files and the audit log (#448, F4)."""
    return _retention()


@router.put("/retention")
def set_retention(payload: Retention, user: dict = Depends(require_role("admin"))):
    try:
        if payload.access_log_days is not None:
            access_log.set_days(payload.access_log_days)
        if payload.audit_days is not None:
            audit.set_retention_days(payload.audit_days)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    now = _retention()
    log_action(
        user["username"], "set_retention", "audit", "success",
        f"access log {now['access_log_days']} days, audit log {now['audit_days']} days",
    )  # fmt: skip
    return now
