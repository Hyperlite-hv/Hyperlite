"""The storage's root work in hyperlite-daemon, part 1: the ZFS pools and volumes of the storage routes
(app/daemon/ops/storage.py, docs/design/privilege-separation.md section 13). The daemon checks the names again and
refuses to delete a volume a VM uses, whatever the API checked; the routes answer as before."""

import pytest

import app.daemon.ops.storage as ops_storage
from app.core import privileged, zfs_storage
from app.daemon.registry import DaemonError


@pytest.fixture()
def zfs(monkeypatch):
    """zfs_storage's commands recorded: a pool "tank" with a volume "web-disk"."""
    calls = []
    monkeypatch.setattr(zfs_storage, "pool_exists", lambda name: name == "tank")
    monkeypatch.setattr(
        zfs_storage, "create_pool", lambda name, size: calls.append(("create_pool", name)) or {"name": name}
    )
    monkeypatch.setattr(zfs_storage, "delete_pool", lambda name: calls.append(("delete_pool", name)))
    monkeypatch.setattr(
        zfs_storage,
        "create_zvol",
        lambda pool, name, size: calls.append(("create_zvol", name)) or f"/dev/zvol/{pool}/{name}",
    )
    monkeypatch.setattr(zfs_storage, "delete_zvol", lambda pool, name: calls.append(("delete_zvol", name)))
    monkeypatch.setattr(ops_storage, "_disk_paths_in_use", lambda: set())
    return calls


@pytest.mark.parametrize(
    "op, args",
    [
        ("storage.zfs_pool_create", {"name": "tank; zpool destroy rpool", "size_gb": 1}),
        ("storage.zfs_pool_delete", {"name": "../rpool"}),
        ("storage.zfs_volume_create", {"pool": "tank", "name": "-o", "size_gb": 1}),
        ("storage.zfs_volume_delete", {"pool": "tank", "name": "web disk"}),
    ],
)
def test_a_name_is_checked_again_by_the_daemon(zfs, op, args):
    with pytest.raises(DaemonError) as e:
        privileged.run(op, **args)
    assert e.value.kind == "refused"
    assert zfs == []


def test_no_operation_takes_a_path_or_an_option(zfs):
    for op, args in [
        ("storage.zfs_pool_create", {"name": "tank2", "size_gb": 1, "backing_file": "/etc/passwd"}),
        ("storage.zfs_volume_create", {"pool": "tank", "name": "v", "size_gb": 1, "options": ["-o", "x=y"]}),
        ("storage.zfs_pool_create", {"name": "tank2", "size_gb": 0}),
    ]:
        with pytest.raises(DaemonError) as e:
            privileged.run(op, **args)
        assert e.value.kind == "invalid_arguments", op
    assert zfs == []


def test_a_volume_a_vm_uses_is_kept_whatever_the_api_checked(zfs, monkeypatch):
    monkeypatch.setattr(ops_storage, "_disk_paths_in_use", lambda: {"/dev/zvol/tank/web-disk"})
    with pytest.raises(DaemonError) as e:
        privileged.run("storage.zfs_volume_delete", pool="tank", name="web-disk")
    assert e.value.kind == "in_use"
    assert zfs == []
    privileged.run("storage.zfs_volume_delete", pool="tank", name="old-disk")
    assert zfs == [("delete_zvol", "old-disk")]


def test_a_volume_on_an_unknown_pool_is_not_found(zfs):
    with pytest.raises(DaemonError) as e:
        privileged.run("storage.zfs_volume_create", pool="nowhere", name="v1", size_gb=1)
    assert e.value.kind == "not_found"


def test_the_routes_answer_as_before(client, auth_headers, zfs, monkeypatch):
    """A zfs failure deleting a volume: 500, as before; a pool's creation goes through the daemon."""
    from app.routers import storage

    class Empty:
        def listAllDomains(self, flags=0):
            return []

        def close(self):
            pass

    monkeypatch.setattr(storage, "open_conn", lambda *a, **k: Empty())
    monkeypatch.setattr(storage, "get_disk_paths_in_use", lambda conn: set())
    admin = auth_headers("ana", "admin")

    def fail(pool, name):
        raise zfs_storage.ZfsError("cannot destroy 'tank/web-disk': dataset is busy")

    monkeypatch.setattr(zfs_storage, "delete_zvol", fail)
    r = client.delete("/storage/tank/volumes/web-disk?confirm=true", headers=admin)
    assert r.status_code == 500 and "dataset is busy" in r.json()["detail"]

    monkeypatch.setattr(zfs_storage, "load_module", lambda: True)
    monkeypatch.setattr(storage, "validate_vm_resources", lambda **k: [])
    r = client.post("/storage", json={"name": "tank2", "type": "zfs", "size_gb": 4}, headers=admin)
    assert r.status_code in (200, 201), r.text
    assert ("create_pool", "tank2") in zfs
