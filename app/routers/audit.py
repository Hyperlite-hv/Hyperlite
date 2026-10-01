from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

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
    depuis: str | None = None,  # ISO 8601, filters on timestamp >= depuis
    jusqu_a: str | None = None,
    user: dict = Depends(require_role("admin")),
):
    return await audit_service.query(audit_service.filters(action, result, username, resource, depuis, jusqu_a), limit)


@router.get("/count")
async def count_audit(
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
    return {
        "total": await audit_service.count(audit_service.filters(action, result, username, resource, depuis, jusqu_a))
    }


@router.get("/actions")
async def list_audit_actions(user: dict = Depends(require_role("admin"))):
    """Distinct action types already seen in the log. Feeds the "Action type"
    filter in the frontend without a hard-coded list that would drift as new
    endpoints are added."""
    return await audit_service.actions()


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
    rows = audit_service.export_rows(audit_service.filters(action, result, username, resource, depuis, jusqu_a))
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        csv_lines(("timestamp", "user", "action", "resource", "result", "error", "ip"), rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="hyperlite-audit-{stamp}.csv"'},
    )
