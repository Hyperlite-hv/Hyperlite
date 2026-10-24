"""Importing a backup of today's format into a backup store (docs/design/backup-store.md, step 4), without QEMU: its
time, the refusals, and the route. tests/test_backup_store_qemu.py imports one on a real image."""

import pytest

from app.core import backup_store_vm, backup_stores, backups
from app.core.database import get_conn


@pytest.fixture()
def pools(tmp_path, monkeypatch):
    (tmp_path / "nas").mkdir()
    monkeypatch.setattr(backup_stores, "_pool_dir", lambda name: tmp_path / "nas")


def _row(path, store=None, status="succeeded"):
    with get_conn() as conn:
        backup_id = conn.execute(
            "INSERT INTO backups (vm_name, path, mode, created_at, status, store) VALUES ('web', ?, 'cold', ?, ?, ?)",
            (str(path), "2026-10-24T02:00:00.123+00:00", status, store),
        ).lastrowid
        conn.commit()
    return backup_id


def test_a_backup_keeps_its_time_in_the_store():
    assert backup_store_vm._stamp_of("2026-10-24T02:00:00.123+00:00") == "2026-10-24T02:00:00Z"
    assert backup_store_vm._stamp_of("2026-10-24T04:00:00+02:00") == "2026-10-24T02:00:00Z"
    assert backup_store_vm._stamp_of("2026-10-24T02:00:00") == "2026-10-24T02:00:00Z"


def test_a_damaged_or_store_backup_is_not_imported(database, pools, tmp_path):
    backup_stores.create("main", "nas")
    broken = tmp_path / "web" / "20261024T020000Z"
    broken.mkdir(parents=True)
    with pytest.raises(backup_store_vm.BackupFailed, match="integrity check"):
        backups.import_into_store(_row(broken), "main", "ana")
    with pytest.raises(RuntimeError, match="already in a backup store"):
        backups.import_into_store(_row(broken, store="main"), "main", "ana")
    with pytest.raises(RuntimeError, match="finished backup"):
        backups.import_into_store(_row(broken, status="failed"), "main", "ana")


def test_the_route_checks_the_store_and_the_backup(client, auth_headers, pools, tmp_path):
    admin = auth_headers("ana")
    backup_stores.create("main", "nas")
    assert client.post("/backups/999/import", json={"store": "main"}, headers=admin).status_code == 404
    assert (
        client.post(
            f"/backups/{_row(tmp_path, store='main')}/import", json={"store": "main"}, headers=admin
        ).status_code
        == 409
    )
    assert client.post(f"/backups/{_row(tmp_path)}/import", json={"store": "nope"}, headers=admin).status_code == 404
    viewer = auth_headers("vic", role="viewer")
    assert client.post(f"/backups/{_row(tmp_path)}/import", json={"store": "main"}, headers=viewer).status_code == 403
