import re
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from app.core import cluster_view
from app.core import tasks as task_core
from app.core.audit import log_action
from app.core.csv_export import csv_lines
from app.core.listing import listing
from app.core.security import get_current_user
from app.services import task_service

router = APIRouter(prefix="/tasks", tags=["tasks"])
_TASK_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@router.get("")
async def list_tasks(
    status: str | None = None,
    type: str | None = None,
    username: str | None = None,
    target: str | None = None,
    node: str | None = None,
    since: str | None = None,  # ISO 8601, filters on created_at >= since
    sort: str = "created_at",
    position: str = "desc",
    limit: int = Query(200, ge=1, le=1000),
    # One object's history: its exact name (target matches any part of a name), and whether it is a container.
    object: str | None = None,
    family: Literal["vm", "container"] | None = None,
    # In a cluster: every member's tasks, each marked with the node that holds it ("on"), and the members that did
    # not answer (app/core/cluster_view.py). Outside a cluster: this node's tasks, in the same shape.
    cluster: bool = False,
    user: dict = Depends(get_current_user),
):
    filters = {"status": status, "type": type, "username": username, "target": target, "node": node, "since": since}
    filters |= {"object": object, "family": family}
    rows = await task_service.list_tasks(filters, sort, position, limit)
    if not cluster:
        return listing(rows)
    params = {**filters, "node": cluster_view.node_filter(node), "sort": sort, "position": position, "limit": limit}
    answers, missing = await run_in_threadpool(cluster_view.gather, user["username"], "/tasks", params)
    merged = cluster_view.merge(rows, answers, sort, reverse=position != "asc", limit=limit)
    return listing(merged, unreachable=missing)


async def _on_peer(on, task_id, user, method, path):
    """The answer of the member that holds the task, when `on` names another node; None for this node."""
    try:
        node = cluster_view.peer(on)
    except LookupError:
        raise HTTPException(status_code=404, detail="Task not found") from None
    if node is None:
        return None
    if not _TASK_ID.fullmatch(task_id):  # a task id is a UUID: nothing else reaches another node's API
        raise HTTPException(status_code=404, detail="Task not found")
    try:
        status, body = await run_in_threadpool(cluster_view.forward, node, user["username"], method, path)
    except cluster_view.PeerError:
        raise HTTPException(status_code=502, detail=f"{node['name']} does not answer") from None
    if status == 401 or status >= 500:  # never a 401 here: the dashboard would sign out of this node
        raise HTTPException(status_code=502, detail=f"{node['name']} refused the request ({status})")
    if status != 200:
        detail = body.get("detail") if isinstance(body, dict) else None
        raise HTTPException(status_code=status, detail=detail or "Task not found")
    return body


@router.get("/export.csv")
def export_tasks(
    status: str | None = None,
    type: str | None = None,
    username: str | None = None,
    target: str | None = None,
    node: str | None = None,
    since: str | None = None,
    user: dict = Depends(get_current_user),
):
    """EVERY task matching the same filters as GET /tasks, newest first, as CSV (the page
    only loads the most recent ones)."""
    filters = {"status": status, "type": type, "username": username, "target": target, "node": node, "since": since}
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
async def get_task(task_id: str, on: str | None = None, user: dict = Depends(get_current_user)):
    """on: the node that holds the task in a cluster (the "on" of GET /tasks?cluster=true)."""
    if (body := await _on_peer(on, task_id, user, "GET", f"/tasks/{task_id}")) is not None:
        return body
    try:
        return await task_service.get_task(task_id)
    except task_service.TaskNotFound:
        raise HTTPException(status_code=404, detail="Task not found") from None


@router.get("/{task_id}/log")
async def get_task_log(task_id: str, on: str | None = None, user: dict = Depends(get_current_user)):
    """What the task did, line by line (its own log, distinct from the audit entries of its target)."""
    if (body := await _on_peer(on, task_id, user, "GET", f"/tasks/{task_id}/log")) is not None:
        return body  # the member's own answer, already a list of v1
    try:
        return listing(await task_service.task_log(task_id))
    except task_service.TaskNotFound:
        raise HTTPException(status_code=404, detail="Task not found") from None


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str, force: bool = False, on: str | None = None, user: dict = Depends(get_current_user)):
    """Ask a running task to stop. An administrator may cancel any task, another user only the tasks they
    started. on: the node that holds it in a cluster, which checks the same rights and records the cancellation."""
    path = f"/tasks/{task_id}/cancel" + ("?force=true" if force else "")
    if (body := await _on_peer(on, task_id, user, "POST", path)) is not None:
        return body
    try:
        task, outcome = await task_service.cancel(task_id, user, force)
    except (task_service.TaskNotFound, LookupError):
        raise HTTPException(status_code=404, detail="Task not found") from None
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except (ValueError, task_core.NotStoppable) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    log_action(user["username"], "cancel_task", task["target"] or task["type"], "success", f"{task['type']}: {outcome}")
    return {"result": outcome}
