"""A backup never loses a good copy (#448, findings F5-F7):
- an error in the retention that follows a backup leaves that backup as it is (it used to be marked failed and its
  folder deleted, the old ones possibly already removed);
- corrupted backups never take the place of good ones in the retention;
- a backup that would not fit on its target is refused before anything is written (the default target is the
  system disk, whose filling stops the database, the journal and libvirt)."""

import pytest

from app.core import backup_integrity, backups, firmware
from app.repositories import registry

SIMPLE = "<domain><name>web</name><devices><disk device='disk'><source file='{src}'/><target dev='vda'/></disk></devices></domain>"


class FakeDomain:
    def __init__(self, src):
        self.xml = SIMPLE.format(src=src)

    def isActive(self):
        return False

    def XMLDesc(self, flags=0):
        return self.xml


class FakeConn:
    def __init__(self, domain):
        self.domain = domain

    def lookupByName(self, name):
        return self.domain

    def close(self):
        pass


@pytest.fixture()
def vm(database, tmp_path, monkeypatch):
    src = tmp_path / "pool" / "web.qcow2"
    src.parent.mkdir()
    src.write_bytes(b"disk")
    monkeypatch.setattr(backups, "open_conn", lambda *a: FakeConn(FakeDomain(src)))
    monkeypatch.setattr(backups, "_write_vm_config", lambda domain, dest: None)
    monkeypatch.setattr(backup_integrity, "save_firmware_state", lambda domain, dest: None)
    monkeypatch.setattr(firmware, "of_domain", lambda root: "bios")

    def cold(domain, vm_name, dest_dir, task_id, disks=None):
        out = dest_dir / "vda.qcow2"
        out.write_bytes(b"copy")
        return [out]

    monkeypatch.setattr(backups, "backup_cold", cold)
    monkeypatch.setattr(backups, "_allocated_bytes", lambda path: 4, raising=False)
    return tmp_path / "backups"


def test_an_error_in_the_retention_leaves_the_backup_just_made(vm, monkeypatch):
    store = registry.backups().sync
    store.upsert_schedule("web", "daily", "03:00", str(vm), 1, None, None, None, "2999-01-01T00:00:00+00:00")

    def broken(vm_name, policy):
        raise OSError("permission denied on an old backup's folder")

    monkeypatch.setattr(backups, "_apply_retention", broken)
    backup_id = backups._run_backup_locked("web", str(vm), None, "admin")
    row = store.get(backup_id)
    assert row["status"] == "succeeded"  # not marked failed
    assert (vm / "web").exists() and any((vm / "web").iterdir())  # its folder is still there


def _finished(tmp_path, when, verification=None):
    folder = tmp_path / "web" / when
    folder.mkdir(parents=True)
    store = registry.backups().sync
    backup_id = store.create("web", None, str(folder), "cold", when, None)
    store.mark_done(backup_id, 4, "x")
    if verification:
        store.set_verification(
            backup_id, verification, when, "checksum mismatch" if verification == "corrupted" else None
        )
    return backup_id


def test_corrupted_backups_never_push_out_the_last_good_one(tmp_path, database):
    old_bad = _finished(tmp_path, "2026-09-30T03:00:00+00:00", "corrupted")
    good = _finished(tmp_path, "2026-10-01T03:00:00+00:00", "verified")
    bad1 = _finished(tmp_path, "2026-10-02T03:00:00+00:00", "corrupted")
    bad2 = _finished(tmp_path, "2026-10-03T03:00:00+00:00", "corrupted")
    backups._apply_retention("web", {"last": 2, "daily": None, "weekly": None, "monthly": None})
    left = {r["id"] for r in registry.backups().sync.finished_of("web")}
    # Without the rule, "the last 2" were the two corrupted ones and the only good copy went.
    assert good in left
    assert {bad1, bad2} <= left  # the newest corrupted ones stay, to be looked at
    assert old_bad not in left  # a corrupted one beyond the policy goes


def test_a_backup_that_would_not_fit_is_refused_before_anything_is_written(vm, monkeypatch):
    monkeypatch.setattr(backups, "_allocated_bytes", lambda path: 50 * 1024**3)
    monkeypatch.setattr(backups.shutil, "disk_usage", lambda path: type("U", (), {"free": 20 * 1024**3})())
    with pytest.raises(RuntimeError, match=r"Not enough space for this backup.*20\.0 GiB free, about 50\.0 GiB needed"):
        backups._run_backup_locked("web", str(vm), None, "admin")
    assert registry.backups().sync.finished_of("web") == []
    assert not (vm / "web").exists() or not any((vm / "web").iterdir())


def test_the_estimate_counts_the_backing_chain(tmp_path, monkeypatch):
    import subprocess

    chain = '[{"filename": "web.qcow2", "actual-size": 1000}, {"filename": "base.qcow2", "actual-size": 5000}]'
    monkeypatch.setattr(backups.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, chain, ""))
    assert backups._allocated_bytes(tmp_path / "web.qcow2") == 6000


def test_a_backup_refused_for_space_is_a_failed_task_never_a_silent_one(vm, monkeypatch):
    """Seen in the lab: refused before its task existed, a backup failed with no task, no audit entry and no
    notification, after the API had answered that it started."""
    from app.core.database import get_conn

    monkeypatch.setattr(backups, "_allocated_bytes", lambda path: 50 * 1024**3)
    monkeypatch.setattr(backups.shutil, "disk_usage", lambda path: type("U", (), {"free": 20 * 1024**3})())
    with pytest.raises(RuntimeError, match="Not enough space"):
        backups._run_backup_locked("web", str(vm), None, "admin")
    with get_conn() as db:
        task = db.execute("SELECT status, error FROM tasks WHERE type = 'backup_vm'").fetchone()
        row = db.execute("SELECT status FROM backups WHERE vm_name = 'web'").fetchone()
    assert task["status"] == "failed" and "Not enough space" in task["error"]
    assert row["status"] == "failed"


def test_a_manual_backup_that_would_not_fit_is_refused_by_the_api(vm, monkeypatch, client, auth_headers):
    monkeypatch.setattr(backups, "_allocated_bytes", lambda path: 50 * 1024**3)
    monkeypatch.setattr(backups.shutil, "disk_usage", lambda path: type("U", (), {"free": 20 * 1024**3})())
    from app.routers import backups as backups_router

    monkeypatch.setattr(backups_router, "refuse_vm_with_block_disks", lambda name, action: None)
    r = client.post("/vms/web/backups", json={}, headers=auth_headers("root"))
    assert r.status_code == 409 and "Not enough space" in r.json()["detail"]


def test_the_space_check_creates_nothing_and_accepts_the_default_directory(tmp_path, monkeypatch):
    target = tmp_path / "not" / "yet" / "there"
    monkeypatch.setattr(backups, "_allocated_bytes", lambda path: 1)
    backups.check_free_space(target, ["disk"])  # measured on the nearest existing directory
    assert not (tmp_path / "not").exists()
    monkeypatch.setattr(backups, "DEFAULT_BACKUP_DIR", tmp_path / "data" / "backups")
    backups.check_free_space(None, ["disk"])  # no target: the default directory, which the validator accepts
    from app.core import paths
    from app.core.backup_groups import validate_target

    # The default directory, of 1.0 as of this version, is the default: stored empty; below it is accepted, though
    # /var/lib/hyperlite is refused.
    assert validate_target("/root/hyperlite/data/backups") is None
    assert validate_target(str(paths.BACKUPS)) is None
    assert validate_target(str(paths.BACKUPS / "weekly")) == str(paths.BACKUPS / "weekly")
    assert validate_target("/root/hyperlite/data/backups/weekly") == str(paths.BACKUPS / "weekly")


def test_the_estimate_falls_back_to_the_files_blocks_without_qemu_img(tmp_path, monkeypatch):
    """Seen on CI, which has no qemu-img: the estimate raised instead of measuring the file itself."""
    disk = tmp_path / "web.qcow2"
    disk.write_bytes(b"x" * 8192)

    def missing(*a, **k):
        raise FileNotFoundError(2, "No such file or directory", "qemu-img")

    monkeypatch.setattr(backups.subprocess, "run", missing)
    assert backups._allocated_bytes(disk) == disk.stat().st_blocks * 512 > 0
