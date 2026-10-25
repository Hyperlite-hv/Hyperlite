"""The backups run by hyperlite-daemon (app/daemon/ops/backup.py, part 1a): the daemon checks a backup's directory
itself, deletes in it only a backup's own files, and removes only Hyperlite's exports, whatever the records say."""

import pytest

from app.core import backups, paths, privileged, tasks, vm_export
from app.daemon.registry import DaemonError


@pytest.fixture()
def target(tmp_path, monkeypatch):
    """An administrator's backup directory, outside the system's own."""
    root = tmp_path / "backups-target"
    root.mkdir()
    return root


def _backup(root, vm="web", stamp="20261025T080000Z", extra=()):
    d = root / vm / stamp
    d.mkdir(parents=True)
    for name in ("vda.qcow2", "manifest.json", "vm-config.json", "nvram.fd", *extra):
        (d / name).write_text("x")
    return d


@pytest.mark.parametrize("bad", ["/etc", "/etc/hyperlite", "relative/dir", "/srv/../etc"])
def test_a_backup_is_never_written_under_a_system_directory(bad):
    with pytest.raises(DaemonError) as e:
        privileged.run("backup.run", vm="web", target=bad, task_id="t1")
    assert e.value.kind == "refused"


def test_a_backup_is_deleted_with_its_vms_empty_directory(target):
    d = _backup(target)
    privileged.run("backup.delete", path=str(d))
    assert not d.exists() and not d.parent.exists()


def test_a_directory_that_holds_other_files_is_kept(target):
    d = _backup(target, extra=("notes.txt",))
    with pytest.raises(DaemonError) as e:
        privileged.run("backup.delete", path=str(d))
    assert e.value.kind == "refused" and "notes.txt" in e.value.detail
    assert (d / "vda.qcow2").exists() and (d / "notes.txt").exists()
    sub = _backup(target, stamp="20261025T090000Z")
    (sub / "nested").mkdir()
    with pytest.raises(DaemonError):
        privileged.run("backup.delete", path=str(sub))
    assert sub.exists()


@pytest.mark.parametrize("op", ["backup.delete", "backup.verify"])
@pytest.mark.parametrize("bad", ["/etc/ssh/sshd_config.d/x", "/usr/lib/x/y", "/a/../etc/x", "relative/a/b/c"])
def test_a_path_the_records_name_outside_the_backups_is_refused(op, bad):
    args = {"path": bad} if op == "backup.delete" else {"path": bad, "task_id": "t1"}
    with pytest.raises(DaemonError) as e:
        privileged.run(op, **args)
    assert e.value.kind == "refused"


def test_a_backup_names_its_vm_and_target_never_a_disk_or_a_destination():
    for extra in ({"disks": ["/dev/sda"]}, {"dest": "/root"}, {"xml": "<domain/>"}):
        with pytest.raises(DaemonError) as e:
            privileged.run("backup.run", vm="web", task_id="t1", **extra)
        assert e.value.kind == "invalid_arguments"
    with pytest.raises(DaemonError) as e:
        privileged.run("backup.restore", path="/x/y/z/w", source_vm="web", mode="new", vm="v", task_id="t", xml="<a/>")
    assert e.value.kind == "invalid_arguments"


def test_a_copy_in_progress_stops_when_its_task_is_cancelled():
    stopped = []
    tasks.register_cancel("t9", lambda: stopped.append(True))
    try:
        assert privileged.run("backup.cancel", user="alice", task_id="t9") == {"cancelled": True}
        assert stopped == [True] and tasks.requester("t9") == "alice"
    finally:
        tasks.unregister_cancel("t9")
    assert privileged.run("backup.cancel", task_id="t-none") == {"cancelled": False}


def test_only_an_export_of_the_exports_directory_is_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(vm_export, "EXPORTS_DIR", tmp_path)
    export = tmp_path / "web--20261025T080000Z.qcow2"
    export.write_text("disk")
    other = tmp_path / "keep.txt"
    other.write_text("x")
    for bad in ("../etc/passwd", "keep.txt", ".tmp-web-20261025T080000Z"):
        with pytest.raises(DaemonError) as e:
            privileged.run("vm.export_delete", name=bad)
        assert e.value.kind == "refused", bad
    assert privileged.run("vm.export_delete", name=export.name) == {"deleted": True}
    assert privileged.run("vm.export_delete", name=export.name) == {"deleted": False}
    assert not export.exists() and other.exists()


def test_the_default_directory_is_accepted():
    assert backups.backup_target(None) == paths.BACKUPS
    assert backups.backup_dir(paths.BACKUPS / "web" / "20261025T080000Z")
