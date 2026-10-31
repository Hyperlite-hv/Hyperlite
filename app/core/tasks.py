"""Persistent task tracking (VM creation, start/stop, ISO upload, ...),
modelled on vCenter's "Recent Tasks": each task carries distinct creation,
start and end times, from which the total duration can be derived.

There is no real job queue yet, so a task starts as soon as it is created
(created_at == started_at in create_task()). The two columns are kept separate so
that a future queue can set created_at at submission and started_at when a worker
actually picks the task up, without a schema change.

"""

import contextvars
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


# The task the running code works for: what it asks hyperlite-daemon meanwhile is written into that task's log
# (app/core/privileged.py), so a task's log says what was done and why it failed, not only that it started and ended.
# Set by create_task, carried onto the task's thread by start_thread, cleared by finish_task.
_current = contextvars.ContextVar("hyperlite_current_task", default=None)


def current_task():
    """The task this code works for, while it runs in this process: a long-lived thread that created a task whose work
    ended on another thread keeps its id, and must not write its next, unrelated calls into that task's log."""
    task_id = _current.get()
    with _cancel_lock:
        return task_id if task_id in _live else None


def _working_for(fn, task_id):
    def run(*args, **kwargs):
        _current.set(task_id)
        return fn(*args, **kwargs)

    return run


def get_task_log(task_id):
    return _store().logs(task_id)


# ---- Cancellation ----
# A long task registers how to stop it (abort a libvirt job, stop between steps...). Cancelling asks it to stop;
# the task then ends as failed, recorded as cancelled by whom. A task with nobody behind it (its worker died with a
# service restart) is closed as abandoned instead of staying "running" forever.
#
# Several processes run tasks (the API, hyperlite-scheduler, HA's services: docs/design/privilege-separation.md,
# section 14, 4c). Each task records its runner, "<service>:<pid>:<start time>": a task is abandoned only when that
# process is gone (a reused pid has another start time). A cancellation for a task another process runs is written
# for it (task_cancel_requests); its watcher thread acts on it within WATCH_S, as abort_here does.

SERVICE_ENV = "HYPERLITE_SERVICE"  # set by each service's unit; "api" by default
WATCH_S = 2.0


def _start_time(pid):
    """A process's start time since boot (clock ticks), None when there is no such process."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            # The command name may hold spaces and parentheses: the fields after its last ")" are fixed.
            return int(f.read().rsplit(")", 1)[1].split()[19])
    except (OSError, IndexError, ValueError):
        return None


_runner = None


def runner():
    """This process, as a task's runner."""
    global _runner
    if _runner is None:
        import os

        pid = os.getpid()
        _runner = f"{os.environ.get(SERVICE_ENV) or 'api'}:{pid}:{_start_time(pid)}"
    return _runner


def runner_alive(name):
    """Whether the process `name` names (runner()) still runs."""
    try:
        _service, pid, started = name.rsplit(":", 2)
        return str(_start_time(int(pid))) == started
    except (ValueError, AttributeError):
        return False


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
    """This task can be cancelled; `abort` (optional) interrupts what it is doing right now. It runs with the proof of
    the work that registered it, whichever thread acts on the cancellation (this process's watch included)."""
    if abort is not None:
        from app.core import peer_auth

        abort = peer_auth.carry(abort)
    with _cancel_lock:
        _cancellers[task_id] = abort
    _watch()


_watcher = []


def _watch():
    """Start, once per process, the thread that acts on the cancellations other processes write for its tasks."""
    with _cancel_lock:
        if _watcher:
            return
        thread = threading.Thread(target=_watch_loop, daemon=True, name="task-cancel-watch")
        _watcher.append(thread)
    thread.start()


def _watch_loop():
    import time as _time

    while True:
        _time.sleep(WATCH_S)
        with _cancel_lock:
            mine = list(_cancellers)
        if not mine:
            continue
        try:
            asked = _store().take_cancel_requests(mine)
        except Exception:
            logger.exception("The cancellations of this process's tasks could not be read")
            continue
        for task_id, username in asked.items():
            task_log(task_id, f"Cancellation requested by {username}")
            abort_here(task_id, username)


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
    status = _store().status(task_id)
    if status is None:
        raise LookupError(task_id)
    if status not in ("running", "pending"):
        raise ValueError("The task is already over")
    with _cancel_lock:
        registered = task_id in _cancellers
        live = task_id in _live
        abort = _cancellers.get(task_id)
        if registered:
            _requested[task_id] = username
    if not registered:
        if not live:
            elsewhere = _store().runner(task_id)
            if elsewhere and elsewhere != runner() and runner_alive(elsewhere) and not force:
                # Another process runs it (hyperlite-scheduler, HA's services): its watcher acts on this.
                _store().ask_cancel(task_id, username, _now())
                task_log(task_id, f"Cancellation requested by {username}")
                return "requested"
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


def abort_here(task_id, username):
    """request_cancel's part in the process that runs the work, without the record: hyperlite-daemon, asked by the API
    to stop a job it runs (backup.cancel). False when this process runs nothing for that task."""
    with _cancel_lock:
        if task_id not in _cancellers:
            return False
        _requested[task_id] = username
        abort = _cancellers.get(task_id)
    if abort:
        try:
            abort()
        except Exception:
            logger.exception("Aborting task %s failed", task_id)
    return True


def close_interrupted_tasks():
    """At start-up: the tasks still 'running' whose process is gone (this service's previous one, or another that
    stopped); those another process still runs are left to it."""
    gone = [t for t, name in _store().running_with_runners().items() if not name or not runner_alive(name)]
    return _store().close_interrupted(gone, _now())


def create_task(type_, target=None, node=None, username=None):
    """Create a task and mark it 'running' immediately (see the module
    docstring: there is no real queue yet)."""
    from app.core import self_node

    task_id = str(uuid.uuid4())
    # One name per node, whatever the caller had at hand: this node's name for None and "local", a registered node's
    # name otherwise (never the host name libvirt reports, which is neither).
    _store().create(task_id, type_, target, self_node.to_db(node), username, _now(), runner())
    with _cancel_lock:
        _live.add(task_id)
    _current.set(task_id)
    if _bind_tasks.get():
        from app.core import peer_auth

        ticket = _ticket(task_id)
        if ticket is not None:
            peer_auth.CREDENTIAL.set(ticket)
    return task_id


def task_status(task_id):
    return _store().status(task_id)


def update_task_progress(task_id, progress):
    _store().update_progress(task_id, progress)


# The tasks run by answer_later, each with the end its work asked for (None until then): applied once the work let go
# of everything it held, so a client that sees the task end can at once act on the same VM.
_held = {}
_held_lock = threading.Lock()


def finish_task(task_id, status, error_message=None):
    """Close a task: 'succeeded' or 'failed'. A task someone asked to stop ends as cancelled by them."""
    with _held_lock:
        if task_id in _held:
            _held[task_id] = (status, error_message)
            return
    with _cancel_lock:
        cancelled_by = _requested.get(task_id) if status == "failed" else None
        _live.discard(task_id)
    if cancelled_by:
        # Keep what the task itself reported (how far it got), after who stopped it.
        error_message = f"Cancelled by {cancelled_by}" + (f": {error_message}" if error_message else "")
    _store().finish(task_id, status, error_message, cancelled_by, _now())
    _store().forget_runner(task_id)
    unregister_cancel(task_id)
    if _current.get() == task_id:
        _current.set(None)


def start_thread(fn, *args, task_id=None, name=None):
    """Start `fn(*args)` on a thread that keeps the proof of the request that starts it (a new thread starts with none:
    peer_auth.CREDENTIAL is a context variable). The work of a task runs long after its request answered, so it shows
    hyperlite-daemon a ticket bound to that task (peer_auth.bound_ticket), valid while the task runs, rather than the
    request's session, which expires or is revoked meanwhile. Outside a request (a scheduler, a service, the tests),
    the thread starts as it is. The only way the API starts a thread that calls hyperlite-daemon
    (tests/test_threads_keep_the_proof.py)."""
    from app.core import peer_auth

    work = fn if task_id is None else _working_for(fn, task_id)
    target = work
    if peer_auth.CREDENTIAL.get() is not None:
        # Without its task yet (a backup, each VM of a group), each task the thread makes takes the proof then.
        target = peer_auth.carry(_binding(work)) if task_id is None else peer_auth.carry(work, _ticket(task_id))
    worker = threading.Thread(target=target, args=args, daemon=True, name=name)
    worker.start()
    return worker


# Set on a thread started by start_thread() before its task exists: each task it creates binds its proof (create_task).
_bind_tasks = contextvars.ContextVar("hyperlite_bind_tasks", default=False)


def _binding(fn):
    def run(*args, **kwargs):
        _bind_tasks.set(True)
        return fn(*args, **kwargs)

    return run


def _ticket(task_id):
    """A ticket bound to `task_id` for the proof this thread holds, or None (it keeps that proof)."""
    from app.core import peer_auth

    try:
        return peer_auth.task_ticket_for(task_id)
    except peer_auth.NoTicket as e:
        logger.warning("Task %s: no task ticket (%s): its work keeps the proof it had", task_id, e)
        return None


def answer_later(task_id, work, **answer):
    """The `?wait=false` form of a long endpoint (API v1, docs/api.md "Long operations"): `work()` runs on a thread
    and closes the task itself (log_action with its task_id); the request answers 202 at once with the task, as every
    other long operation does. A client then follows GET /tasks/{id} instead of holding a request for the whole copy,
    which a proxy cuts long before a large disk is copied.

    The task ends once `work` returned, so after the claims it releases on its way out (vm_locks.released_after): a
    client that saw the task end and starts the VM at once was refused as busy. A task `work` leaves open (an error
    raised without being recorded on the task) is closed here, with the error: a task never stays "running" with
    nothing behind it."""
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse

    from app.core.error_messages import describe_exception

    def body():
        error = None
        try:
            work()
        except HTTPException as e:
            error = str(e.detail)
        except Exception as e:
            logger.exception("Task %s failed", task_id)
            error = describe_exception(e)
        finally:
            with _held_lock:
                asked = _held.pop(task_id, None)
        if asked is not None:
            finish_task(task_id, *asked)
        elif task_status(task_id) == "running":
            finish_task(task_id, "failed" if error else "succeeded", error)

    with _held_lock:
        _held[task_id] = None
    start_thread(body, task_id=task_id)
    return JSONResponse(status_code=202, content={"task_id": task_id, "status": "running", **answer})
