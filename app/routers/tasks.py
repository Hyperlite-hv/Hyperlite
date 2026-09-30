from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.core import tasks as task_core
from app.core.audit import log_action
from app.core.csv_export import csv_lines
from app.core.security import get_current_user
from app.services import task_service

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("")
async def list_tasks(
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
    filters = {"statut": statut, "type": type, "username": username, "cible": cible, "node": node, "depuis": depuis}
    filters |= {"objet": objet, "famille": famille}
    return await task_service.list_tasks(filters, tri, ordre, limit)


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
    filters = {"statut": statut, "type": type, "username": username, "cible": cible, "node": node, "depuis": depuis}
    rows = task_service.export_rows(filters)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        csv_lines(
            ("id", "created", "type", "target", "node", "user", "status", "progress", "started", "finished", "error"),
            rows,
        ),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="hyperlite-tasks-{stamp}.csv"'},
    )


@router.get("/{task_id}")
async def get_task(task_id: str, user: dict = Depends(get_current_user)):
    try:
        return await task_service.get_task(task_id)
    except task_service.TaskNotFound:
        raise HTTPException(status_code=404, detail="Task not found") from None


@router.get("/{task_id}/log")
async def get_task_log(task_id: str, user: dict = Depends(get_current_user)):
    """What the task did, line by line (its own log, distinct from the audit entries of its target)."""
    try:
        return await task_service.task_log(task_id)
    except task_service.TaskNotFound:
        raise HTTPException(status_code=404, detail="Task not found") from None


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str, force: bool = False, user: dict = Depends(get_current_user)):
    """Ask a running task to stop. An administrator may cancel any task, another user only the tasks they
    started."""
    try:
        task, outcome = await task_service.cancel(task_id, user, force)
    except (task_service.TaskNotFound, LookupError):
        raise HTTPException(status_code=404, detail="Task not found") from None
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except (ValueError, task_core.NotStoppable) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    log_action(user["username"], "cancel_task", task["cible"] or task["type"], "succes", f"{task['type']}: {outcome}")
    return {"resultat": outcome}
