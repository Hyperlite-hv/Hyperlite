"""A backup being read (a restore, a verification, a file-restore session) is not deleted from under the reader: by
hand it is refused with what holds it, and a schedule's retention keeps it until its next run. Found in the lab
during the audit before 1.0.0 (#354): a backup deleted during its restore made the restore fail half way."""

import pytest

from app.core import backup_use, backups, file_restore
from app.repositories import registry
from app.services import backup_service


def _backup(tmp_path, name="web", when="2026-10-01T03:00:00+00:00"):
    folder = tmp_path / name / when[:10]
    folder.mkdir(parents=True)
    (folder / "sda.qcow2").write_bytes(b"disk")
    store = registry.backups().sync
    backup_id = store.create(name, None, str(folder), "froid", when, None)
    store.mark_done(backup_id, 4, "x")
    return backup_id, folder


def test_a_backup_being_restored_cannot_be_deleted(tmp_path, database):
    backup_id, folder = _backup(tmp_path)
    with backup_use.reading(backup_id, "a restore"):
        with pytest.raises(backup_use.BackupInUse, match="a restore"):
            backup_service.delete(backup_id)
        assert folder.exists()
    backup_service.delete(backup_id)  # once the restore is over
    assert not folder.exists()


def test_readers_are_counted_and_named():
    with backup_use.reading(7, "a restore"), backup_use.reading(7, "a verification"):
        with backup_use.reading(7, "a restore"):
            assert backup_use.holder(7) == "a restore and a verification"
        assert backup_use.holder(7) == "a restore and a verification"
    assert backup_use.holder(7) is None


def test_an_open_file_restore_session_holds_its_backup(monkeypatch):
    monkeypatch.setattr(file_restore, "is_open", lambda backup_id: backup_id == 9)
    assert backup_use.holder(9) == "a file-restore session"
    assert backup_use.holder(10) is None


def test_retention_keeps_a_backup_being_read_until_its_next_run(tmp_path, database):
    old, old_folder = _backup(tmp_path, when="2026-10-01T03:00:00+00:00")
    _backup(tmp_path, when="2026-10-02T03:00:00+00:00")
    policy = {"last": 1, "daily": None, "weekly": None, "monthly": None}
    with backup_use.reading(old, "a restore"):
        backups._apply_retention("web", policy)
        assert old_folder.exists() and registry.backups().sync.get(old)
    backups._apply_retention("web", policy)
    assert not old_folder.exists() and not registry.backups().sync.get(old)


def test_restore_and_verify_mark_the_backup_while_they_run(monkeypatch):
    seen = []
    monkeypatch.setattr(backups, "_restore_backup", lambda backup_id, *a: seen.append(backup_use.holder(backup_id)))
    monkeypatch.setattr(backups, "_verify_backup", lambda backup_id, *a: seen.append(backup_use.holder(backup_id)))
    backups.restore_backup(3, "new", "web-2")
    backups.verify_backup(3)
    assert seen == ["a restore", "a verification"]
    assert backup_use.holder(3) is None
