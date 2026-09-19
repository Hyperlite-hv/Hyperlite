from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.database import get_conn
from app.core.security import get_current_user

router = APIRouter(prefix="/tasks", tags=["tasks"])

# Columns allowed for sorting: an allowlist rather than interpolating `tri`
# directly into the SQL (it is an arbitrary query parameter).
_SORTABLE = {"cree_le", "debut_le", "fin_le", "statut", "type", "cible", "username"}


@router.get("")
def list_tasks(
    statut: str | None = None,
    type: str | None = None,
    username: str | None = None,
    cible: str | None = None,
    node: str | None = None,
    depuis: str | None = None,  # ISO 8601, filters on cree_le >= depuis
    tri: str = "cree_le",
    ordre: str = "desc",
    limit: int = Query(200, ge=1, le=1000),
    user: dict = Depends(get_current_user),
):
    tri = tri if tri in _SORTABLE else "cree_le"
    ordre_sql = "ASC" if ordre.lower() == "asc" else "DESC"

    clauses, params = [], []
    if statut:
        clauses.append("statut = ?")
        params.append(statut)
    if type:
        clauses.append("type = ?")
        params.append(type)
    if username:
        clauses.append("username = ?")
        params.append(username)
    if node:
        clauses.append("node = ?")
        params.append(node)
    if cible:
        clauses.append("cible LIKE ?")
        params.append(f"%{cible}%")
    if depuis:
        clauses.append("cree_le >= ?")
        params.append(depuis)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(
            # Only fixed fragments/allowlisted column names are interpolated; values are bound parameters.
            f"SELECT * FROM tasks {where} ORDER BY {tri} {ordre_sql} LIMIT ?",  # noqa: S608
            params,
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/{task_id}")
def get_task(task_id: str, user: dict = Depends(get_current_user)):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Task not found")
        task = dict(row)
        # Attach the audit_log entries for the same target so the detail view can show
        # the full story on click (exact failure reason, related actions...).
        logs = conn.execute(
            "SELECT timestamp, action, result, error_message FROM audit_log "
            "WHERE resource = ? ORDER BY id DESC LIMIT 20",
            (task["cible"],),
        ).fetchall()
    task["logs"] = [dict(row) for row in logs]
    return task
