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


# --- Part 2a: the libvirt pools of this node ---


@pytest.fixture()
def pools(database, monkeypatch, daemon_libvirt):
    """This node's libvirt, as hyperlite-daemon and the API see it, and the calls the routes made to the daemon."""
    from app.routers import storage
    from tests.test_shared_pools import _Host

    hosts = {"local": _Host(), "n2": _Host()}
    monkeypatch.setattr(storage, "open_conn", lambda node=None: hosts[node or "local"])
    daemon_libvirt(hosts["local"])
    with database.get_conn() as db:
        db.execute("INSERT INTO nodes (name, hostname, added_at) VALUES ('n2', 'n2.lan', 'now')")
        db.commit()
    asked = []
    ask = privileged.ask
    monkeypatch.setattr(privileged, "ask", lambda op, *a, **k: asked.append(op) or ask(op, *a, **k))
    monkeypatch.setattr("shutil.which", lambda name: f"/sbin/{name}")
    monkeypatch.setattr("app.core.nfs_permissions.check", lambda path, export=None: {"ok": True, "message": None})
    hosts["asked"] = asked
    return hosts


@pytest.mark.parametrize(
    "fields",
    [
        {"name": "etc", "type": "dir", "path": "/etc"},
        {"name": "x y", "type": "dir", "path": "/srv/x"},
        {"name": "nas", "type": "netfs", "nfs_host": "nas.lan;reboot", "nfs_export_path": "/srv"},
        {"name": "lv", "type": "lvm", "lvm_vg": "vg0", "lvm_device": "/dev/../etc/shadow"},
    ],
)
def test_a_pools_fields_are_checked_again_by_the_daemon(pools, fields):
    with pytest.raises(DaemonError) as e:
        privileged.run("storage.pool_create", **fields)
    assert e.value.kind == "refused"
    assert pools["local"].pools == {}


def test_a_pool_takes_fields_never_a_definition(pools):
    with pytest.raises(DaemonError) as e:
        privileged.run("storage.pool_create", name="nas", type="dir", xml="<pool type='dir'/>")
    assert e.value.kind == "invalid_arguments"
    assert pools["local"].pools == {}


def test_a_pool_holding_files_is_kept_whatever_the_api_checked(pools):
    from tests.test_pool_base_image import DIR, _pool

    _pool(pools["local"], DIR, "data.qcow2")
    with pytest.raises(DaemonError) as e:
        privileged.run("storage.pool_delete", pool="nas")
    assert e.value.kind == "in_use"
    assert "nas" in pools["local"].pools


@pytest.mark.parametrize(
    "op, args",
    [("storage.pool_delete", {"pool": "default"}), ("storage.pool_rename", {"pool": "default", "new_name": "x1"})],
)
def test_the_default_pool_stays_whatever_the_api_checked(pools, op, args):
    with pytest.raises(DaemonError) as e:
        privileged.run(op, **args)
    assert e.value.kind == "refused"


def test_this_nodes_pool_is_the_daemons_and_another_nodes_keeps_its_connection(pools, client, auth_headers):
    admin = auth_headers("root")
    nfs = {"name": "nas", "type": "netfs", "nfs_host": "192.168.1.10", "nfs_export_path": "/srv/vms"}
    r = client.post("/storage", json=nfs, headers=admin)
    assert r.status_code == 201, r.text
    assert r.json()["warning"] is None and pools["asked"] == ["storage.pool_create"]
    assert "nas" in pools["local"].pools

    pools["asked"].clear()
    r = client.post("/storage?node=n2", json=nfs, headers=admin)
    assert r.status_code == 201, r.text
    assert "warning" not in r.json() and pools["asked"] == []  # n2 is reached over its own libvirt, as before
    assert "nas" in pools["n2"].pools


def test_another_node_gets_the_same_answers(pools, client, auth_headers):
    """The same checks and the same statuses on either path: a name taken, a pool missing."""
    admin = auth_headers("root")
    nfs = {"name": "nas", "type": "netfs", "nfs_host": "192.168.1.10", "nfs_export_path": "/srv/vms"}
    for node in ("local", "n2"):
        assert client.post(f"/storage?node={node}", json=nfs, headers=admin).status_code == 201
        r = client.post(f"/storage?node={node}", json=nfs, headers=admin)
        assert (r.status_code, r.json()["detail"]) == (422, "A pool 'nas' already exists"), node
        r = client.delete(f"/storage/gone?node={node}&confirm=true", headers=admin)
        assert (r.status_code, r.json()["detail"]) == (404, "Storage pool 'gone' not found"), node


def test_a_listing_asks_the_daemon_only_for_a_missing_default_pool(pools, client, auth_headers, monkeypatch):
    from tests.test_shared_pools import _Pool

    ran = []
    monkeypatch.setattr(privileged, "run", lambda op, **k: ran.append(op))
    monkeypatch.setattr(zfs_storage, "list_pools", lambda: [])
    pools["local"].listAllStoragePools = lambda flags=0: []
    viewer = auth_headers("viewer", "viewer")
    assert client.get("/storage", headers=viewer).status_code == 200
    assert ran == ["storage.ensure_default_pool"]

    default = _Pool("<pool type='dir'><name>default</name><target><path>/var/lib/libvirt/images</path></target></pool>")
    default.host, default.active = pools["local"], True
    pools["local"].pools["default"] = default
    ran.clear()
    assert client.get("/storage", headers=viewer).status_code == 200
    assert ran == []
