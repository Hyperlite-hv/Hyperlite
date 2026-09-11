from fastapi import APIRouter, Depends

from app.core.database import get_conn
from app.core.security import require_role

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
def list_audit(limit: int = 200, user: dict = Depends(require_role("admin"))):
    limit = max(1, min(limit, 1000))
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, timestamp, username, action, resource, result, error_message "
            "FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
