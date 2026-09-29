from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.core.audit import log_action
from app.core.csv_export import csv_lines, newest_first
from app.core.database import get_conn
from app.core.security import require_role

router = APIRouter(prefix="/audit", tags=["audit"])


def _clauses(action, result, username, resource, depuis, jusqu_a):
    clauses, params = [], []
    if action:
        clauses.append("action = ?")
        params.append(action)
    if result:
        clauses.append("result = ?")
        params.append(result)
    if username:
        clauses.append("username = ?")
        params.append(username)
    if resource:
        clauses.append("resource LIKE ?")
        params.append(f"%{resource}%")
    if depuis:
        clauses.append("timestamp >= ?")
        params.append(depuis)
    if jusqu_a:
        clauses.append("timestamp <= ?")
        params.append(jusqu_a)
    return clauses, params


def _filters(action, result, username, resource, depuis, jusqu_a):
    clauses, params = _clauses(action, result, username, resource, depuis, jusqu_a)
    return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params


@router.get("")
def list_audit(
    limit: int = 200,
    action: str | None = None,
    result: str | None = None,
    username: str | None = None,
    resource: str | None = None,
    depuis: str | None = None,  # ISO 8601, filters on timestamp >= depuis
    jusqu_a: str | None = None,
    user: dict = Depends(require_role("admin")),
):
    limit = max(1, min(limit, 1000))
    where, params = _filters(action, result, username, resource, depuis, jusqu_a)
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT id, timestamp, username, action, resource, result, error_message, ip "  # noqa: S608 -- only fixed fragments/allowlisted column names are interpolated; values are bound parameters
            f"FROM audit_log {where} ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/count")
def count_audit(
    action: str | None = None,
    result: str | None = None,
    username: str | None = None,
    resource: str | None = None,
    depuis: str | None = None,
    jusqu_a: str | None = None,
    user: dict = Depends(require_role("admin")),
):
    """Number of entries matching the same filters as GET /audit, which only returns the
    most recent ones: lets the page say "300 entries, showing the 100 most recent"."""
    where, params = _filters(action, result, username, resource, depuis, jusqu_a)
    with get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS n FROM audit_log {where}", params).fetchone()["n"]  # noqa: S608 -- fixed fragments only
    return {"total": total}


@router.get("/actions")
def list_audit_actions(user: dict = Depends(require_role("admin"))):
    """Distinct action types already seen in the log. Feeds the "Action type"
    filter in the frontend without a hard-coded list that would drift as new
    endpoints are added."""
    with get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT action FROM audit_log ORDER BY action").fetchall()
    return [r["action"] for r in rows]


EXPORT_COLUMNS = ("timestamp", "username", "action", "resource", "result", "error_message", "ip")


@router.get("/export.csv")
def export_audit(
    action: str | None = None,
    result: str | None = None,
    username: str | None = None,
    resource: str | None = None,
    depuis: str | None = None,
    jusqu_a: str | None = None,
    user: dict = Depends(require_role("admin")),
):
    """EVERY entry matching the same filters as GET /audit, newest first, as CSV. The
    page only loads the most recent entries: exporting those would pass a partial
    audit log off as a complete one."""
    clauses, params = _clauses(action, result, username, resource, depuis, jusqu_a)
    filters = {
        k: v
        for k, v in {
            "action": action,
            "result": result,
            "username": username,
            "resource": resource,
            "depuis": depuis,
            "jusqu_a": jusqu_a,
        }.items()
        if v
    }
    log_action(user["username"], "export_audit", "audit_log", "succes", str(filters) if filters else None)
    rows = newest_first("audit_log", EXPORT_COLUMNS, clauses, params, key="id")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        csv_lines(("timestamp", "user", "action", "resource", "result", "error", "ip"), rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="hyperlite-audit-{stamp}.csv"'},
    )
