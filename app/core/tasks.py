"""Persistent task tracking (VM creation, start/stop, ISO upload, ...),
modelled on vCenter's "Recent Tasks": each task carries distinct creation,
start and end times, from which the total duration can be derived.

There is no real job queue yet, so a task starts as soon as it is created
(cree_le == debut_le in create_task()). The two columns are kept separate so
that a future queue can set cree_le at submission and debut_le when a worker
actually picks the task up, without a schema change.

"""

import logging
import threading
import uuid
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


def _now():
    return datetime.now(UTC).isoformat()


# ---- Task log: what a long task did, step by step, readable from the dashboard while it runs ----


def _store():
    # The task repository's synchronous bridge: long operations run in threads.
    from app.repositories import registry

    return registry.tasks().sync


def task_log(task_id, message):
    if not task_id:
        return
    _store().append_log(task_id, message, _now())


def get_task_log(task_id):
    return _store().logs(task_id)


# ---- Cancellation ----
# A long task registers how to stop it (abort a libvirt job, stop between steps...). Cancelling asks it to stop;
# the task then ends as failed, recorded as cancelled by whom. A task with nobody behind it (its worker died with a
# service restart) is closed as abandoned instead of staying "en_cours" forever.


class TaskCancelled(Exception):
    """Raised by a task that noticed it was asked to stop."""


_cancellers = {}
_requested = {}
# Tasks started by this process and not finished yet: the others have nobody behind them.
_live = set()
_cancel_lock = threading.Lock()


class NotStoppable(Exception):
    """The task runs but has no clean way to stop midway."""


def register_cancel(task_id, abort=None):
    """This task can be cancelled; `abort` (optional) interrupts what it is doing right now."""
    with _cancel_lock:
        _cancellers[task_id] = abort


def unregister_cancel(task_id):
    with _cancel_lock:
        _cancellers.pop(task_id, None)
        _requested.pop(task_id, None)


def cancel_requested(task_id):
    with _cancel_lock:
        return task_id in _requested


def raise_if_cancelled(task_id):
    if cancel_requested(task_id):
        raise TaskCancelled(f"Cancelled by {_requested.get(task_id)}")


def requester(task_id):
    """Who asked this task to stop (None when nobody did)."""
    with _cancel_lock:
        return _requested.get(task_id)


def cancellable(task_id):
    with _cancel_lock:
        return task_id in _cancellers


def is_live(task_id):
    with _cancel_lock:
        return task_id in _live


def _close_by(task_id, username, reason):
    _store().close(task_id, username, reason, _now())
    with _cancel_lock:
        _live.discard(task_id)


def request_cancel(task_id, username, force=False):
    """'requested' when the running task was asked to stop; 'abandoned' when nobody was running it (or `force`)
    and it was closed here. LookupError: unknown task; ValueError: already over; NotStoppable: it runs and cannot
    stop midway (it ends by itself; `force` only closes the record)."""
    statut = _store().status(task_id)
    if statut is None:
        raise LookupError(task_id)
    if statut not in ("en_cours", "en_attente"):
        raise ValueError("The task is already over")
    with _cancel_lock:
        registered = task_id in _cancellers
        live = task_id in _live
        abort = _cancellers.get(task_id)
        if registered:
            _requested[task_id] = username
    if not registered:
        if not live:
            _close_by(task_id, username, "no process was running it any more")
            return "abandoned"
        if not force:
            raise NotStoppable("This operation cannot be stopped midway; it ends by itself")
        _close_by(task_id, username, "closed by hand while it may still be running")
        return "abandoned"
    task_log(task_id, f"Cancellation requested by {username}")
    if abort:
        try:
            abort()
        except Exception:
            # The task will notice the request at its next step; the abort only makes it quicker.
            logger.exception("Aborting task %s failed", task_id)
    return "requested"


def close_interrupted_tasks():
    """At start-up: a task still 'en_cours' was run by the previous process, which is gone."""
    return _store().close_interrupted(_now())


def create_task(type_, cible=None, node=None, username=None):
    """Create a task and mark it 'en_cours' immediately (see the module
    docstring: there is no real queue yet)."""
    from app.core import self_node

    task_id = str(uuid.uuid4())
    # One name per node, whatever the caller had at hand: this node's name for None and "local", a registered node's
    # name otherwise (never the host name libvirt reports, which is neither).
    _store().create(task_id, type_, cible, self_node.to_db(node), username, _now())
    with _cancel_lock:
        _live.add(task_id)
    return task_id


def task_status(task_id):
    return _store().status(task_id)


def update_task_progress(task_id, progres):
    _store().update_progress(task_id, progres)


def finish_task(task_id, statut, error_message=None):
    """Close a task: 'termine' or 'echec'. A task someone asked to stop ends as cancelled by them."""
    with _cancel_lock:
        cancelled_by = _requested.get(task_id) if statut == "echec" else None
        _live.discard(task_id)
    if cancelled_by:
        # Keep what the task itself reported (how far it got), after who stopped it.
        error_message = f"Cancelled by {cancelled_by}" + (f": {error_message}" if error_message else "")
    _store().finish(task_id, statut, error_message, cancelled_by, _now())
    unregister_cancel(task_id)
