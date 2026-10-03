"""The directory a backup is written in: backups run as root, so a manual backup checks it like a schedule does (it
took any directory, /etc included), and only an administrator may choose one other than the default."""

import types

import pytest

from app.core import permissions
from app.routers import backups


@pytest.fixture()
def started(database, monkeypatch):
    """The target each accepted backup would have been written in (nothing runs)."""
    runs = []
    monkeypatch.setattr(backups, "refuse_vm_with_block_disks", lambda *a: None)
    monkeypatch.setattr(backups, "run_backup", lambda name, target, **k: runs.append(target))

    class _Thread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(backups, "threading", types.SimpleNamespace(Thread=_Thread))
    permissions.create_acl("user", "bob", "gestionnaire", "vm", "web")
    return runs


@pytest.mark.parametrize("path", ["/etc", "/etc/cron.d", "/root/.ssh", "/var/lib/hyperlite", "/srv/../etc", "rel/dir"])
def test_a_system_or_relative_directory_is_refused(started, client, auth_headers, path):
    r = client.post("/vms/web/backups", json={"target_dir": path}, headers=auth_headers("admin"))
    assert r.status_code == 422, r.text
    assert started == []


def test_an_administrator_chooses_the_directory(started, client, auth_headers):
    r = client.post("/vms/web/backups", json={"target_dir": "/srv/backups/"}, headers=auth_headers("admin"))
    assert r.status_code == 202, r.text
    assert started == ["/srv/backups"]


def test_a_vm_manager_backs_up_to_the_default_directory_only(started, client, auth_headers):
    bob = auth_headers("bob", role="observateur")
    assert client.post("/vms/web/backups", json={}, headers=bob).status_code == 202
    assert (
        client.post("/vms/web/backups", json={"target_dir": str(backups.DEFAULT_BACKUP_DIR)}, headers=bob).status_code
        == 202
    )
    assert started == [None, str(backups.DEFAULT_BACKUP_DIR)]
    assert client.post("/vms/web/backups", json={"target_dir": "/home/bob"}, headers=bob).status_code == 403
    assert len(started) == 2


def test_a_vm_manager_edits_a_schedule_without_moving_its_directory(started, client, auth_headers):
    admin, bob = auth_headers("admin"), auth_headers("bob", role="observateur")
    body = {"frequence": "quotidien", "heure": "02:00", "cible_dir": "/srv/backups"}
    assert client.put("/vms/web/backup-schedule", json=body, headers=admin).status_code == 200
    # The directory set by the administrator, sent back unchanged with a new time: accepted.
    assert client.put("/vms/web/backup-schedule", json={**body, "heure": "03:00"}, headers=bob).status_code == 200
    r = client.put("/vms/web/backup-schedule", json={**body, "cible_dir": "/home/bob"}, headers=bob)
    assert r.status_code == 403
    assert client.get("/vms/web/backup-schedule", headers=admin).json()["cible_dir"] == "/srv/backups"
