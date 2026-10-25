"""Snapshot tasks are a VM's tasks (code review, #448): they named the snapshot, so the VM's Activity tab never listed
them and the audit log did not say which VM a snapshot belonged to. Any error closes them: one raised while dropping
the replication bitmaps (anything but a libvirt error) left the task running forever, with no audit entry."""

import time

import libvirt
import pytest

from app.core import audit, checkpoints, vm_snapshots


class FakeSnapshot:
    deleted = False

    def delete(self, flags=0):
        FakeSnapshot.deleted = True


class FakeDomain:
    snapshots = frozenset({"before-update"})

    def name(self):
        return "web"

    def snapshotCreateXML(self, xml, flags):
        pass

    def snapshotLookupByName(self, name):
        if name not in self.snapshots:
            raise libvirt.libvirtError("no snapshot")
        return FakeSnapshot()

    def revertToSnapshot(self, snap, flags):
        pass

    def isActive(self):
        return False

    def XMLDesc(self, flags=0):
        return "<domain><name>web</name><devices/></domain>"


class FakeConn:
    def lookupByName(self, name):
        return FakeDomain()

    def close(self):
        pass


@pytest.fixture()
def fake(database, monkeypatch, daemon_libvirt):
    daemon_libvirt(FakeConn())  # hyperlite-daemon takes, restores and deletes the snapshots

    def rows():
        audit._AUDIT_QUEUE.join()
        with database.get_conn() as db:
            tasks = [dict(r) for r in db.execute("SELECT type, target, status, error FROM tasks")]
            entries = [dict(r) for r in db.execute("SELECT action, resource, result, error_message FROM audit_log")]
        return tasks, entries

    return rows


def _broken_bitmaps(*a):
    raise RuntimeError("Cannot remove the replication bitmap hl-1 of /pool/web.qcow2: Permission denied")


def _ended(fake, action):
    """The task closed and its audit entry written: the work's thread does both, in that order."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        tasks, entries = fake()
        if tasks and tasks[0]["status"] != "running" and any(e["action"] == action for e in entries):
            return tasks, entries
        time.sleep(0.02)
    raise AssertionError("the task never ended")


@pytest.mark.parametrize(
    "path, action",
    [
        ("/vms/web/snapshots", "create_snapshot"),
        ("/vms/web/snapshots/before-update/restore?confirm=true", "restore_snapshot"),
    ],
)
def test_an_error_dropping_the_bitmaps_closes_the_task(fake, monkeypatch, client, auth_headers, path, action):
    monkeypatch.setattr(checkpoints, "drop", _broken_bitmaps)
    body = {"name": "after-update"} if action == "create_snapshot" else None
    r = client.post(path, json=body, headers=auth_headers("root"))
    assert r.status_code == 202, r.text
    tasks, entries = _ended(fake, action)
    # hyperlite-daemon keeps an unexpected error's reason in its own log: the task and the audit say it failed.
    assert tasks[0]["status"] == "failed" and f"vm.snapshot_{action.split('_')[0]} failed" in tasks[0]["error"]
    entry = next(e for e in entries if e["action"] == action)
    assert entry["resource"] == "web" and entry["result"] == "failed"
    assert ("after-update" if action == "create_snapshot" else "before-update") in entry["error_message"]


def test_a_snapshot_task_and_its_audit_entry_name_the_vm(fake, monkeypatch, client, auth_headers):
    FakeSnapshot.deleted = False
    r = client.delete("/vms/web/snapshots/before-update", headers=auth_headers("root"))
    assert r.status_code == 200 and FakeSnapshot.deleted
    tasks, entries = fake()
    assert tasks == [{"type": "delete_snapshot", "target": "web", "status": "succeeded", "error": None}]
    entry = next(e for e in entries if e["action"] == "delete_snapshot")
    assert entry["resource"] == "web" and entry["error_message"] == "Snapshot 'before-update'"


def test_an_unexpected_error_in_a_deletion_closes_its_task(fake, monkeypatch, client, auth_headers):
    def broken(root):
        raise OSError("zfs: command not found")

    monkeypatch.setattr(vm_snapshots, "zvol_disks", broken)
    r = client.delete("/vms/web/snapshots/before-update", headers=auth_headers("root"))
    assert r.status_code == 500
    tasks, _ = fake()
    assert tasks[0]["status"] == "failed" and "vm.snapshot_delete failed" in tasks[0]["error"]
