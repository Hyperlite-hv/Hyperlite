"""The claims on VMs and containers are shared by every process (app/core/shared_claims.py): a claim another process
holds refuses one here with its reason, and a process that stops, even killed, leaves none behind."""

import os
import signal
import subprocess
import sys

import pytest

from app.core import container_snapshots, paths, shared_claims, vm_locks

HOLD = """
import sys
from app.core import container_snapshots, vm_locks
kind, name, node, what = sys.argv[1:5]
if kind == "vm":
    vm_locks.claim(name, what, node or None)
else:
    container_snapshots.claim(name, what)
print("held", flush=True)
sys.stdin.readline()
"""


@pytest.fixture()
def other_process():
    """Another process holding claims, as the schedulers will: its own interpreter, the same lock directory."""
    started = []

    def hold(name, what, node="", kind="vm"):
        proc = subprocess.Popen(
            [sys.executable, "-c", HOLD, kind, name, node, what],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            cwd=paths.CODE,
            env={**os.environ, "PYTHONPATH": str(paths.CODE), "HYPERLITE_LOCK_DIR": str(paths.LOCKS)},
        )
        started.append(proc)
        assert proc.stdout.readline().strip() == "held"
        return proc

    yield hold
    for proc in started:
        proc.kill()
        proc.wait()


def test_a_vm_another_process_works_on_is_refused_here_with_its_reason(other_process):
    proc = other_process("web", "a scheduled backup")
    with pytest.raises(vm_locks.VmBusy) as busy:
        vm_locks.claim("web", "a restore")
    assert busy.value.running == "a scheduled backup"
    assert vm_locks.running("web") == "a scheduled backup"
    assert vm_locks.claim("web", "a migration", node="node-b").release() is None  # another node's VM of that name
    proc.communicate("\n")  # its operation ends
    with vm_locks.claim("web", "a restore"):
        assert vm_locks.running("web") == "a restore"
    assert vm_locks.running("web") is None


def test_a_process_killed_while_it_works_leaves_no_claim(other_process):
    proc = other_process("db", "a replication")
    proc.send_signal(signal.SIGKILL)
    proc.wait()
    # Its file still names it: the name is stale, since nobody holds the lock.
    assert vm_locks.running("db") is None
    vm_locks.claim("db", "a backup").release()


def test_the_vms_busy_on_a_node_are_counted_in_every_process(other_process):
    other_process("web", "a migration", node="node-b")
    other_process("x" * 300, "a backup", node="node-b")  # a name too long for a file's: its file is a digest
    other_process("db", "a snapshot")
    held = vm_locks.claim("cache", "a disk move", node="node-b")
    try:
        assert sorted(vm_locks.busy_on_node("node-b")) == ["a backup", "a disk move", "a migration"]
        assert vm_locks.busy_on_node(None) == ["a snapshot"]
        with pytest.raises(vm_locks.VmBusy):
            vm_locks.claim("x" * 300, "a restore", node="node-b")
    finally:
        held.release()


def test_a_container_another_process_works_on_is_refused_here(other_process):
    other_process("ct1", "a backup", kind="container")
    with pytest.raises(container_snapshots.ContainerBusy):
        container_snapshots.claim("ct1", "a snapshot")
    assert container_snapshots.busy("ct1") == "a backup"
    assert vm_locks.running("ct1") is None  # a VM of the same name is another guest


def test_a_lock_directory_others_may_write_in_is_refused(tmp_path, monkeypatch):
    """/run/lock is writable by every account: a directory planted there is never the one root writes in."""
    planted = tmp_path / "lock"
    planted.mkdir(mode=0o777)
    planted.chmod(0o777)
    monkeypatch.setattr(paths, "LOCKS", planted)
    with pytest.raises(RuntimeError, match="no claim is taken"):
        vm_locks.claim("web", "a backup")


def test_a_link_in_the_lock_directory_is_never_followed(tmp_path, monkeypatch):
    locks = tmp_path / "lock"
    locks.mkdir(mode=0o750)
    monkeypatch.setattr(paths, "LOCKS", locks)
    target = tmp_path / "shadow"
    target.write_text("root:x:0:0")
    (locks / "vm@local@web.lock").symlink_to(target)
    with pytest.raises((OSError, ValueError)):
        vm_locks.claim("web", "a backup")
    assert target.read_text() == "root:x:0:0"


def test_a_claim_released_twice_frees_nothing_else():
    first = vm_locks.claim("vm1", "a backup")
    first.release()
    second = vm_locks.claim("vm1", "a restore")
    first.release()
    assert vm_locks.running("vm1") == "a restore"
    second.release()
    assert shared_claims.holder("vm", "local", "vm1") is None
