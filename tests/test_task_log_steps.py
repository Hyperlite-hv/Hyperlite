"""What a task asks hyperlite-daemon is written into its log (app/core/privileged.py, app/core/tasks.py current_task):
a VM's start logged "Started by" and "Finished" only, which said nothing when it failed."""

import threading
import time

import pytest

from app.core import privileged, tasks
from app.daemon.registry import DaemonError


@pytest.fixture(autouse=True)
def no_current_task():
    token = tasks._current.set(None)
    yield
    tasks._current.reset(token)


@pytest.fixture()
def daemon(monkeypatch):
    calls = []

    def dispatch(op, user, timeout, args):
        calls.append(op)
        if op == "vm.fail":
            raise DaemonError("failed", "libvirt: domain is locked by another node")
        return {"ok": True}, []

    monkeypatch.setattr(privileged, "_dispatch", dispatch)
    return calls


def _log(task_id):
    return [entry["message"] for entry in tasks.get_task_log(task_id)]


def test_each_operation_of_a_task_is_in_its_log_with_its_result(database, daemon):
    task_id = tasks.create_task("start_vm", "web1", username="alice")
    privileged.run("vm.power", user="alice", vm="web1", action="start", node="antho")
    with pytest.raises(DaemonError):
        privileged.run("vm.fail", user="alice", vm="web1")
    tasks.finish_task(task_id, "failed", "the start failed")
    log = _log(task_id)
    assert "hyperlite-daemon: vm.power (vm=web1, node=antho, action=start)" in log
    assert any(m.startswith("vm.power: done in ") for m in log)
    assert any(m.startswith("vm.fail: failed after ") and "domain is locked by another node" in m for m in log)
    assert tasks.current_task() is None  # cleared with the task


def test_no_secret_and_no_plumbing_reaches_a_tasks_log(database, daemon):
    task_id = tasks.create_task("create_user", "bob", username="alice")
    privileged.run("access.create", user="alice", name="bob", password="hunter2", token="t0k3n", key="ssh-ed25519 AAA")
    privileged.run("auth.peer", user="alice")
    privileged.run("cfs.confirm", user="alice")
    tasks.finish_task(task_id, "succeeded")
    text = "\n".join(_log(task_id))
    assert "access.create (name=bob)" in text
    for secret in ("hunter2", "t0k3n", "ssh-ed25519", "auth.peer", "cfs.confirm"):
        assert secret not in text


def test_outside_a_task_nothing_is_logged(database, daemon):
    assert tasks.current_task() is None
    privileged.run("vm.power", user="alice", vm="web1", action="start")
    assert daemon == ["vm.power"]


def test_a_task_ended_elsewhere_no_longer_takes_its_creators_calls(database, daemon):
    """A scheduler's loop creates a task whose work ends on another thread: its next calls are not that task's."""
    task_id = tasks.create_task("backup", "web1", username="system")
    threading.Thread(target=tasks.finish_task, args=(task_id, "succeeded")).start()
    for _ in range(100):
        if tasks.task_status(task_id) == "succeeded":
            break
        time.sleep(0.01)
    privileged.run("vm.power", user="system", vm="other", action="stop")
    assert not any("vm=other" in m for m in _log(task_id))


def test_the_thread_of_a_task_works_for_it(database, daemon):
    task_id = tasks.create_task("delete_vm", "web2", username="alice")
    tasks._current.set(None)  # the request's context moved on
    done = threading.Event()

    def work():
        privileged.run("vm.delete", user="alice", vm="web2")
        done.set()

    tasks.start_thread(work, task_id=task_id)
    assert done.wait(5)
    assert "hyperlite-daemon: vm.delete (vm=web2)" in _log(task_id)
