"""Live restore (app/core/live_restore.py, backups._live_copy): the new VM starts on its backup, the copy runs while it
does, the backup's images get their owner back, and a copy that fails takes the VM away."""

import os

import libvirt
import pytest

from app.core import backups, live_restore


class Domain:
    """A running VM whose block pull ends after `steps` reads of its progress, or fails."""

    def __init__(self, steps=2, fail=None):
        self.started = self.destroyed = self.undefined = False
        self.pulled, self.steps, self.fail = [], steps, fail

    def create(self):
        self.started = True

    def blockPull(self, dev, bandwidth, flags):
        self.pulled.append(dev)

    def blockJobInfo(self, dev, flags):
        if self.fail == dev:
            raise libvirt.libvirtError("I/O error reading the backing file")
        if self.steps <= 0:
            return {}
        self.steps -= 1
        return {"cur": 50, "end": 100}

    def isActive(self):
        return self.started and not self.destroyed

    def destroy(self):
        self.destroyed = True

    def undefineFlags(self, flags):
        self.undefined = True


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(live_restore, "POLL_S", 0)
    monkeypatch.setattr(live_restore, "check_standalone", lambda paths: None)
    progress = []
    monkeypatch.setattr(backups, "update_task_progress", lambda task, pct: progress.append(pct))
    return progress


def test_the_vm_starts_then_its_disks_are_copied_and_the_backup_gets_its_owner_back(tmp_path, fast):
    image = tmp_path / "vda.qcow2"
    image.write_bytes(b"backup")
    os.chmod(image, 0o640)
    domain = Domain()
    backups._live_copy(domain, [("vda", image), ("vdb", image)], [tmp_path / "new.qcow2"], "task")
    assert domain.started and domain.pulled == ["vda", "vdb"]
    assert fast[0] == 20 and fast[-1] == 95  # started, then the copy's share of the task
    assert os.stat(image).st_mode & 0o777 == 0o640


def test_a_copy_that_fails_says_so_and_still_gives_the_owner_back(tmp_path, fast, monkeypatch):
    image = tmp_path / "vda.qcow2"
    image.write_bytes(b"backup")
    given_back = []
    monkeypatch.setattr(live_restore.Owners, "restore", lambda self: given_back.append(True))
    with pytest.raises(live_restore.LiveRestoreError, match="The copy of vda stopped"):
        backups._live_copy(Domain(fail="vda"), [("vda", image)], [tmp_path / "new.qcow2"], "task")
    assert given_back == [True]


def test_a_failed_live_restore_takes_its_vm_away():
    domain = Domain()
    domain.started = True

    class Conn:
        def lookupByName(self, name):
            assert name == "web-restored"
            return domain

    backups._drop_new_vm(Conn(), "web-restored")
    assert domain.destroyed and domain.undefined


def test_a_disk_still_reading_its_backup_is_not_called_restored(tmp_path, monkeypatch):
    monkeypatch.setattr(live_restore, "image_info", lambda p: {"backing-filename": "/backups/vda.qcow2"})
    with pytest.raises(live_restore.LiveRestoreError, match=r"still reads from /backups/vda\.qcow2"):
        live_restore.check_standalone([tmp_path / "new.qcow2"])


def test_only_a_new_vm_restores_live(client, auth_headers, database, tmp_path):
    from tests.test_backup_restore_safety import _backup_row

    headers = auth_headers("alice")
    backup_id = _backup_row(database, tmp_path)
    r = client.post(f"/backups/{backup_id}/restore", json={"mode": "overwrite", "live": True}, headers=headers)
    assert r.status_code == 422 and "A live restore makes a new VM" in r.json()["detail"]
    with pytest.raises(RuntimeError, match="A live restore makes a new VM"):
        backups.restore_files(str(tmp_path), True, "vm1", "overwrite", "vm1", None, "task", live=True)
