from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.core import tasks as task_core
from app.core.audit import log_action
from app.core.csv_export import csv_lines, newest_first
from app.core.database import get_conn
from app.core.security import get_current_user

router = APIRouter(prefix="/tasks", tags=["tasks"])

# Columns allowed for sorting: an allowlist rather than interpolating `tri`
# directly into the SQL (it is an arbitrary query parameter).
# Mapping to literals: the SQL text never contains request data.
_SORT_COLUMNS = {c: c for c in ("cree_le", "debut_le", "fin_le", "statut", "type", "cible", "username")}


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
    # One object's history: its exact name (cible matches any part of a name), and whether it is a container.
    objet: str | None = None,
    famille: Literal["vm", "container"] | None = None,
    user: dict = Depends(get_current_user),
):
    tri = _SORT_COLUMNS.get(tri, "cree_le")
    ordre_sql = "ASC" if ordre.lower() == "asc" else "DESC"
    clauses, params = _clauses(statut, type, username, cible, node, depuis)
    if objet:
        clauses.append("cible = ?")
        params.append(objet)
    if famille:
        # Container task types all name the container (create_container, clone_container, backup_container...).
        clauses.append("type LIKE '%container%'" if famille == "container" else "type NOT LIKE '%container%'")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(
            # Only fixed fragments/allowlisted column names are interpolated; values are bound parameters.
            f"SELECT * FROM tasks {where} ORDER BY {tri} {ordre_sql} LIMIT ?",  # noqa: S608
            params,
        ).fetchall()
    return [_with_cancel(dict(r)) for r in rows]


def _with_cancel(task):
    """Whether a Cancel button makes sense: a running task that can be stopped, or one nobody runs any more."""
    running = task["statut"] in ("en_cours", "en_attente")
    # A clean stop exists; or nobody runs it any more (closing is safe); otherwise it only ends by itself.
    task["arret_propre"] = running and task_core.cancellable(task["id"])
    task["orpheline"] = running and not task_core.is_live(task["id"])
    task["annulable"] = task["arret_propre"] or task["orpheline"]
    return task


EXPORT_COLUMNS = (
    "id",
    "cree_le",
    "type",
    "cible",
    "node",
    "username",
    "statut",
    "progres",
    "debut_le",
    "fin_le",
    "erreur",
)


@router.get("/export.csv")
def export_tasks(
    statut: str | None = None,
    type: str | None = None,
    username: str | None = None,
    cible: str | None = None,
    node: str | None = None,
    depuis: str | None = None,
    user: dict = Depends(get_current_user),
):
    """EVERY task matching the same filters as GET /tasks, newest first, as CSV (the page
    only loads the most recent ones)."""
    clauses, params = _clauses(statut, type, username, cible, node, depuis)
    rows = newest_first("tasks", EXPORT_COLUMNS, clauses, params)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        csv_lines(
            ("id", "created", "type", "target", "node", "user", "status", "progress", "started", "finished", "error"),
            rows,
        ),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="hyperlite-tasks-{stamp}.csv"'},
    )


def _clauses(statut, type, username, cible, node, depuis):
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
    return clauses, params


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
    return _with_cancel(task)


@router.get("/{task_id}/log")
def get_task_log(task_id: str, user: dict = Depends(get_current_user)):
    """What the task did, line by line (its own log, distinct from the audit entries of its target)."""
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Task not found")
    return task_core.get_task_log(task_id)


@router.post("/{task_id}/cancel")
def cancel_task(task_id: str, force: bool = False, user: dict = Depends(get_current_user)):
    """Ask a running task to stop. An administrator may cancel any task, another user only the tasks they
    started."""
    with get_conn() as conn:
        row = conn.execute("SELECT username, type, cible FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    if user["role"] != "admin" and row["username"] != user["username"]:
        raise HTTPException(
            status_code=403, detail="Only an administrator or the user who started it may cancel a task"
        )
    if force and user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only an administrator may close a task by hand")
    try:
        outcome = task_core.request_cancel(task_id, user["username"], force=force)
    except LookupError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    except (ValueError, task_core.NotStoppable) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    log_action(user["username"], "cancel_task", row["cible"] or row["type"], "succes", f"{row['type']}: {outcome}")
    return {"resultat": outcome}
