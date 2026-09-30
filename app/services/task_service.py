"""Tasks as the API shows them: the list with whether each can be cancelled, one task with the audit entries of its
target, its log, the CSV export, and cancellation (app/core/tasks.py keeps the in-process runtime part)."""

import asyncio

from app.core import tasks as task_core
from app.repositories import registry


class TaskNotFound(LookupError):
    pass


def _with_cancel(task):
    """Whether a Cancel button makes sense: a running task that can be stopped, or one nobody runs any more."""
    running = task["statut"] in ("en_cours", "en_attente")
    # A clean stop exists; or nobody runs it any more (closing is safe); otherwise it only ends by itself.
    task["arret_propre"] = running and task_core.cancellable(task["id"])
    task["orpheline"] = running and not task_core.is_live(task["id"])
    task["annulable"] = task["arret_propre"] or task["orpheline"]
    return task


async def list_tasks(filters, sort, order, limit):
    return [_with_cancel(t) for t in await registry.tasks().list(filters, sort, order, limit)]


async def get_task(task_id):
    task = await registry.tasks().get(task_id)
    if not task:
        raise TaskNotFound(task_id)
    # The audit entries of the same target, so the detail view shows the full story on click (exact failure
    # reason, related actions...).
    task["logs"] = await registry.audit().for_resource(task["cible"])
    return _with_cancel(task)


async def task_log(task_id):
    if not await registry.tasks().get(task_id):
        raise TaskNotFound(task_id)
    return await registry.tasks().logs(task_id)


def export_rows(filters):
    return registry.tasks().export_rows(filters)


async def cancel(task_id, user, force):
    """(task, outcome). PermissionError: not the admin nor the task's author; the core errors otherwise."""
    task = await registry.tasks().get(task_id)
    if not task:
        raise TaskNotFound(task_id)
    if user["role"] != "admin" and task["username"] != user["username"]:
        raise PermissionError("Only an administrator or the user who started it may cancel a task")
    if force and user["role"] != "admin":
        raise PermissionError("Only an administrator may close a task by hand")
    outcome = await asyncio.to_thread(task_core.request_cancel, task_id, user["username"], force)
    return task, outcome
