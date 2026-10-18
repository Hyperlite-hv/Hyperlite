"""Snapshot tasks are a VM's tasks (code review, #448): they named the snapshot, so the VM's Activity tab never listed
them and the audit log did not say which VM a snapshot belonged to. Any error closes them: one raised while dropping
the replication bitmaps (anything but a libvirt error) left the task running forever, with no audit entry."""

import pytest

from app.core import audit, checkpoints
from app.core.tasks import create_task
from app.routers.vms import snapshots


class FakeSnapshot:
    deleted = False

    def delete(self, flags=0):
        FakeSnapshot.deleted = True


class FakeDomain:
    def snapshotCreateXML(self, xml, flags):
        pass

    def snapshotLookupByName(self, name):
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
def fake(database, monkeypatch):
    monkeypatch.setattr(snapshots, "open_conn", lambda node=None: FakeConn())

    def rows():
        audit._AUDIT_QUEUE.join()
        with database.get_conn() as db:
            tasks = [dict(r) for r in db.execute("SELECT type, cible, statut, erreur FROM tasks")]
            entries = [dict(r) for r in db.execute("SELECT action, resource, result, error_message FROM audit_log")]
        return tasks, entries

    return rows


def _broken_bitmaps(*a):
    raise RuntimeError("Cannot remove the replication bitmap hl-1 of /pool/web.qcow2: Permission denied")


@pytest.mark.parametrize(
    "job, action, args",
    [
        (snapshots._create_snapshot_job, "create_snapshot", ("web", "before-update", "<domainsnapshot/>")),
        (snapshots._restore_snapshot_job, "restore_snapshot", ("web", "before-update")),
    ],
)
def test_an_error_dropping_the_bitmaps_closes_the_task(fake, monkeypatch, job, action, args):
    monkeypatch.setattr(checkpoints, "release", _broken_bitmaps)
    task_id = create_task(action, "web", username="admin")
    job(task_id, "admin", *args)
    tasks, entries = fake()
    assert tasks[0]["statut"] == "failed" and "replication bitmap" in tasks[0]["erreur"]
    assert entries[0]["resource"] == "web" and entries[0]["result"] == "failed"
    assert "before-update" in entries[0]["error_message"]


def test_a_snapshot_task_and_its_audit_entry_name_the_vm(fake, monkeypatch, client, auth_headers):
    FakeSnapshot.deleted = False
    r = client.delete("/vms/web/snapshots/before-update", headers=auth_headers("root"))
    assert r.status_code == 200 and FakeSnapshot.deleted
    tasks, entries = fake()
    assert tasks == [{"type": "delete_snapshot", "cible": "web", "statut": "succeeded", "erreur": None}]
    entry = next(e for e in entries if e["action"] == "delete_snapshot")
    assert entry["resource"] == "web" and entry["error_message"] == "Snapshot 'before-update'"


def test_an_unexpected_error_in_a_deletion_closes_its_task(fake, monkeypatch, client, auth_headers):
    def broken(domain):
        raise OSError("zfs: command not found")

    monkeypatch.setattr(snapshots, "_zvol_disks_of_domain", broken)
    with pytest.raises(OSError):
        client.delete("/vms/web/snapshots/before-update", headers=auth_headers("root"))
    tasks, _ = fake()
    assert tasks[0]["statut"] == "failed" and "command not found" in tasks[0]["erreur"]
