"""Tasks run by several processes (app/core/tasks.py; docs/design/privilege-separation.md, section 14, 4c): the API,
hyperlite-scheduler and HA's services. A task another live process runs is never closed as abandoned; its
cancellation is written for that process, whose watcher acts on it."""

import subprocess
import time

import pytest

from app.core import tasks


@pytest.fixture()
def other_process():
    """Another live process, standing for hyperlite-scheduler: its runner name."""
    proc = subprocess.Popen(["sleep", "60"])
    yield proc, f"scheduler:{proc.pid}:{tasks._start_time(proc.pid)}"
    proc.kill()
    proc.wait()


def _task_of(runner, task_id="t-sched"):
    tasks._store().create(task_id, "backup", "web", None, "system", tasks._now(), runner)
    return task_id


def test_a_task_another_process_runs_is_asked_to_stop_not_abandoned(database, other_process):
    proc, runner = other_process
    task_id = _task_of(runner)
    assert tasks.request_cancel(task_id, "alice") == "requested"
    assert tasks.task_status(task_id) == "running"
    assert tasks._store().take_cancel_requests([task_id]) == {task_id: "alice"}
    proc.kill()
    proc.wait()
    # Its process gone, nobody runs it any more.
    assert tasks.request_cancel(task_id, "alice") == "abandoned"
    assert tasks.task_status(task_id) == "failed"


def test_a_restart_closes_only_the_tasks_whose_process_is_gone(database, other_process):
    _proc, runner = other_process
    alive = _task_of(runner, "t-alive")
    gone = _task_of("api:999999999:1", "t-gone")
    legacy = _task_of(None, "t-legacy")  # recorded before runners were
    assert tasks.close_interrupted_tasks() == 2
    assert tasks.task_status(alive) == "running"
    assert tasks.task_status(gone) == tasks.task_status(legacy) == "failed"


def test_a_reused_pid_is_not_the_runner():
    me = tasks.runner()
    service, pid, started = me.rsplit(":", 2)
    assert tasks.runner_alive(me)
    assert not tasks.runner_alive(f"{service}:{pid}:{int(started) + 1}")
    assert not tasks.runner_alive("garbage")


def test_the_runner_acts_on_a_cancellation_written_for_it(database):
    """What the scheduler's watcher does with the API's request: the task's abort runs, and it ends as cancelled."""
    task_id = tasks.create_task("backup", "web", username="system")
    stopped = []
    tasks.register_cancel(task_id, lambda: stopped.append(True))
    try:
        tasks._store().ask_cancel(task_id, "alice", tasks._now())
        deadline = time.monotonic() + tasks.WATCH_S + 5
        while not stopped and time.monotonic() < deadline:
            time.sleep(0.1)
        assert stopped == [True] and tasks.requester(task_id) == "alice"
        tasks.finish_task(task_id, "failed", "stopped")
        assert tasks._store().get(task_id)["cancelled_by"] == "alice"
        assert tasks._store().runner(task_id) is None
    finally:
        tasks.unregister_cancel(task_id)


def test_a_task_records_this_process_as_its_runner(database):
    task_id = tasks.create_task("backup", "web", username="alice")
    assert tasks._store().runner(task_id) == tasks.runner()
    tasks.finish_task(task_id, "succeeded")
    assert tasks._store().runner(task_id) is None
