"""Failures leave a trace (a failed task, a disk reported kept) and nothing half-made behind them."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core import backups
from app.core.database import get_conn


def _job(vm, next_run):
    with get_conn() as db:
        db.execute(
            "INSERT INTO backup_jobs (vm_name, frequency, time, target_dir, next_run) VALUES (?, 'daily', '02:00', '/b', ?)",
            (vm, next_run),
        )
        db.commit()


def _next_run(vm):
    with get_conn() as db:
        return datetime.fromisoformat(
            db.execute("SELECT next_run FROM backup_jobs WHERE vm_name = ?", (vm,)).fetchone()[0]
        )


def test_a_failed_scheduled_backup_is_retried_within_the_hour(database, monkeypatch):
    def boom(vm, *a, **k):
        raise RuntimeError("store unreachable")

    audited = []
    monkeypatch.setattr(backups, "run_backup", boom)
    monkeypatch.setattr(backups, "log_action", lambda *a, **k: audited.append(a))
    now = datetime.now(UTC)
    _job("web", (now - timedelta(minutes=5)).isoformat())
    backups.run_due_schedules(now)
    assert _next_run("web") == now + backups.SCHEDULE_RETRY
    assert audited == [("scheduler", "backup_job_failed", "web", "failed", "store unreachable")]


def test_a_successful_scheduled_backup_keeps_its_regular_time(database, monkeypatch):
    monkeypatch.setattr(backups, "run_backup", lambda vm, *a, **k: 1)
    now = datetime.now(UTC)
    _job("db", (now - timedelta(minutes=5)).isoformat())
    backups.run_due_schedules(now)
    assert _next_run("db") == backups._next_run("daily", "02:00", now)


def test_a_backup_refused_before_its_copy_is_a_failed_task(database, monkeypatch):
    class Conn:
        def lookupByName(self, name):
            import libvirt

            raise libvirt.libvirtError("no domain")

        def close(self):
            pass

    finished, audited = [], []
    monkeypatch.setattr(backups, "log_action", lambda *a, **k: audited.append(a))
    monkeypatch.setattr(backups, "open_conn", lambda readonly=False: Conn())
    monkeypatch.setattr(backups, "create_task", lambda *a, **k: "t-1")
    monkeypatch.setattr(
        backups, "finish_task", lambda task_id, status, error=None: finished.append((task_id, status, error))
    )
    with pytest.raises(RuntimeError, match="not found"):
        backups._run_backup_locked("gone", None, None, "alice")
    assert finished == [("t-1", "failed", "VM 'gone' not found")]
    assert audited == [("alice", "backup_vm", "gone", "failed", "VM 'gone' not found")]


def test_a_disk_left_on_another_node_is_reported_kept(monkeypatch):
    import subprocess
    from types import SimpleNamespace

    from app.core import cluster, vm_delete

    calls = iter(
        [SimpleNamespace(returncode=0, stderr=""), SimpleNamespace(returncode=1, stderr="Read-only file system")]
    )
    monkeypatch.setattr(cluster, "get_node", lambda name: {"name": name})
    monkeypatch.setattr(cluster, "_run_ssh", lambda node, args: next(calls))
    left = vm_delete._remove_on_node("n2", ["/v/a.qcow2", "/v/b.qcow2"], "web")
    assert list(left) == ["/v/b.qcow2"] and "Read-only file system" in left["/v/b.qcow2"]

    def timeout(node, args):
        raise subprocess.TimeoutExpired("ssh", 15)

    monkeypatch.setattr(cluster, "_run_ssh", timeout)
    assert list(vm_delete._remove_on_node("n2", ["/v/c.qcow2"], "web")) == ["/v/c.qcow2"]


def test_a_creation_failing_halfway_removes_the_disks_it_made(tmp_path, monkeypatch):
    from app.core import vm_builder, zfs_storage

    deleted = []
    monkeypatch.setattr(zfs_storage, "delete_zvol", lambda pool, name: deleted.append((pool, name)))
    first = tmp_path / "web.qcow2"
    with pytest.raises(RuntimeError, match="second disk"), vm_builder.undone_on_failure():
        vm_builder._new_file(first)
        first.write_bytes(b"qcow2")
        vm_builder._record("zvol", "tank", "web-2")
        raise RuntimeError("second disk failed")
    assert not first.exists() and deleted == [("tank", "web-2")]

    kept = tmp_path / "db.qcow2"
    with vm_builder.undone_on_failure():
        vm_builder._new_file(kept)
        kept.write_bytes(b"qcow2")
    assert kept.exists()  # a creation that succeeds keeps what it made
