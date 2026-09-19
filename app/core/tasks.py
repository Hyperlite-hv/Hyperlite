"""Persistent task tracking (VM creation, start/stop, ISO upload, ...),
modelled on vCenter's "Recent Tasks": each task carries distinct creation,
start and end times, from which the total duration can be derived.

There is no real job queue yet, so a task starts as soon as it is created
(cree_le == debut_le in create_task()). The two columns are kept separate so
that a future queue can set cree_le at submission and debut_le when a worker
actually picks the task up, without a schema change.

"""

import uuid
from datetime import UTC, datetime

from app.core.database import get_conn


def _now():
    return datetime.now(UTC).isoformat()


def create_task(type_, cible=None, node=None, username=None):
    """Create a task and mark it 'en_cours' immediately (see the module
    docstring: there is no real queue yet)."""
    task_id = str(uuid.uuid4())
    now = _now()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO tasks (id, type, cible, node, username, statut, progres, cree_le, debut_le) "
            "VALUES (?, ?, ?, ?, ?, 'en_cours', 0, ?, ?)",
            (task_id, type_, cible, node, username, now, now),
        )
        conn.commit()
    return task_id


def update_task_progress(task_id, progres):
    with get_conn() as conn:
        conn.execute("UPDATE tasks SET progres = ? WHERE id = ?", (progres, task_id))
        conn.commit()


def _finish_task_in(conn, task_id, statut, error_message=None):
    """Close a task on an already open connection. Used by log_action() so that
    the audit_log and tasks writes share one commit instead of opening a
    second SQLite connection per call."""
    conn.execute(
        "UPDATE tasks SET statut = ?, progres = 100, fin_le = ?, erreur = ? WHERE id = ?",
        (statut, _now(), error_message, task_id),
    )


def finish_task(task_id, statut, error_message=None):
    with get_conn() as conn:
        _finish_task_in(conn, task_id, statut, error_message)
        conn.commit()
