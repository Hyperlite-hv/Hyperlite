from typing import Optional

from fastapi import APIRouter, Depends

from app.core.database import get_conn
from app.core.security import require_role

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
def list_audit(
    limit: int = 200,
    action: Optional[str] = None,
    result: Optional[str] = None,
    username: Optional[str] = None,
    resource: Optional[str] = None,
    depuis: Optional[str] = None,  # ISO 8601, filtre timestamp >= depuis
    jusqu_a: Optional[str] = None,
    user: dict = Depends(require_role("admin")),
):
    limit = max(1, min(limit, 1000))

    clauses, params = [], []
    if action:
        clauses.append("action = ?"); params.append(action)
    if result:
        clauses.append("result = ?"); params.append(result)
    if username:
        clauses.append("username = ?"); params.append(username)
    if resource:
        clauses.append("resource LIKE ?"); params.append(f"%{resource}%")
    if depuis:
        clauses.append("timestamp >= ?"); params.append(depuis)
    if jusqu_a:
        clauses.append("timestamp <= ?"); params.append(jusqu_a)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT id, timestamp, username, action, resource, result, error_message "
            f"FROM audit_log {where} ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/actions")
def list_audit_actions(user: dict = Depends(require_role("admin"))):
    """Types d'action distincts deja vus dans le journal -- alimente le
    filtre "Type d'action" cote front sans liste codee en dur qui driverait
    au fil des nouveaux endpoints."""
    with get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT action FROM audit_log ORDER BY action").fetchall()
    return [r["action"] for r in rows]
