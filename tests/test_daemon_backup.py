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


def test_a_backup_whose_directory_holds_other_files_keeps_its_record(client, auth_headers, database, target):
    from app.repositories import registry

    d = _backup(target, extra=("notes.txt",))
    store = registry.backups().sync
    backup_id = store.create("web", None, str(d), "cold", "2026-10-25T08:00:00+00:00", None)
    store.mark_done(backup_id, 4, None)
    answer = client.delete(f"/backups/{backup_id}?confirm=true", headers=auth_headers("root"))
    assert answer.status_code == 409 and "notes.txt" in answer.json()["detail"]
    assert store.get(backup_id) is not None and (d / "notes.txt").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [("previous", "../../etc"), ("chain", "x/y"), ("checkpoint", "hl-../x"), ("checkpoint", "anything")],
)
def test_a_replication_point_takes_the_chains_names_never_a_path(target, field, value):
    """The chain's state is the API's record: a point and a checkpoint are names of make_point's own form."""
    with pytest.raises(DaemonError) as e:
        privileged.run("replication.point", vm="web", target=str(target), **{field: value})
    assert e.value.kind == "refused"


def test_a_replication_point_is_never_written_under_a_system_directory():
    with pytest.raises(DaemonError) as e:
        privileged.run("replication.point", vm="web", target="/etc/hyperlite")
    assert e.value.kind == "failed" and "cannot be written" in e.value.detail


# --- Part 2: the backup stores ---


def test_a_remotes_token_goes_only_where_it_was_sealed_with(database, monkeypatch):
    """The records are the API's to write: an address changed there never receives the token."""
    from app.core import backup_remotes, secrets_crypto

    seen = []
    monkeypatch.setattr(
        backup_remotes, "_Client", lambda address, port, fingerprint, token: seen.append((address, port, token))
    )
    sealed = backup_remotes._seal("10.0.0.9", 8000, "a" * 64, "tok")
    row = {"name": "dr", "address": "evil.example", "port": 443, "fingerprint": "b" * 64, "token_encrypted": sealed}
    backup_remotes._client(row)
    assert seen == [("10.0.0.9", 8000, "tok")]
    # A remote recorded before: its token was sealed alone, its record's address is the one it was saved with.
    legacy = {**row, "token_encrypted": secrets_crypto.encrypt("old-token")}
    backup_remotes._client(legacy)
    assert seen[-1] == ("evil.example", 443, "old-token")


@pytest.mark.parametrize("vm_uuid", ["../../etc", "web", "6F1C2D3E-0000-4000-8000-000000000001/.."])
def test_an_import_names_a_vm_uuid_never_a_path(vm_uuid):
    with pytest.raises(DaemonError) as e:
        privileged.run(
            "store.import", path="/x/y/z/w", created_at="t", vm="web", vm_uuid=vm_uuid, store="main", task_id="t"
        )
    assert e.value.kind == "invalid_arguments"


def test_an_import_reads_only_a_backup_directory(target):
    with pytest.raises(DaemonError) as e:
        privileged.run(
            "store.import",
            path="/etc/ssh/a/b",
            created_at="t",
            vm="web",
            vm_uuid="6f1c2d3e-0000-4000-8000-000000000001",
            store="main",
            task_id="t",
        )
    assert e.value.kind == "refused"


def test_a_store_is_made_in_a_directory_of_its_pool_only(database):
    for bad in ("../etc", "a/b", ".."):
        with pytest.raises(DaemonError) as e:
            privileged.run("store.make", name="main", pool="nas", directory=bad)
        assert e.value.kind in ("refused", "invalid_arguments"), bad


def test_a_copy_job_is_read_from_the_records_by_its_id(database):
    with pytest.raises(DaemonError) as e:
        privileged.run("store.sync", job_id=4242)
    assert e.value.kind == "not_found"
    with pytest.raises(DaemonError) as e:
        privileged.run("store.sync", job_id=1, source="/etc", target="main")
    assert e.value.kind == "invalid_arguments"


# --- Part 3: the file restore ---


@pytest.mark.parametrize("bad", ["/etc/ssh/x/y", "/a/../etc/b/c", "relative/a/b/c"])
def test_a_browsing_session_opens_only_a_backups_images(bad):
    with pytest.raises(DaemonError) as e:
        privileged.run("restore.open", backup_id=1, path=bad, username="alice")
    assert e.value.kind == "refused"


def test_a_session_is_its_owners_only():
    with pytest.raises(DaemonError) as e:
        privileged.run("restore.ls", session="nope", username="bob", device="/dev/sda1", path="/")
    assert e.value.kind == "refused"
    assert privileged.run("restore.busy", backup_id=1) == {"open": False}
