"""ZFS pools on the node's disks (#419, docs/design/zfs-disks.md): only blank disks are offered and taken, each layout
gets its disks, the health is read from `zpool status -j`, disks are attached, added and replaced, a destroyed pool
gives its disks back blank, and a pool that degrades is notified once."""

import json
import subprocess

import pytest

from app.core import host_disks, privileged, zfs_storage
from app.daemon.ops import storage as ops_storage
from app.daemon.registry import DaemonError


def _dev(name, **over):
    return {"name": name, "type": "disk", "size": 500 * 1024**3, "model": "WDC WD5000", "serial": "S1", "rota": True,
            "rm": False, "ro": False, "fstype": None, "mountpoints": [None], **over}  # fmt: skip


def test_only_blank_whole_disks_no_vm_uses_are_offered(tmp_path):
    by_id = tmp_path / "by-id"
    by_id.mkdir()
    devices = []
    for name in ("sda", "sdb", "sdc", "sdd", "sde", "sdf", "sdg", "sdh", "nvme0n1"):
        (tmp_path / name).write_bytes(b"")
        devices.append(str(tmp_path / name))
    (by_id / "ata-WDC_WD5000_S2").symlink_to(tmp_path / "sdb")
    (by_id / "wwn-0x5000c5").symlink_to(tmp_path / "sdb")
    (by_id / "ata-WDC_WD5000_S2-part1").symlink_to(tmp_path / "sdb")
    found = host_disks.free_disks(
        in_use=[str(tmp_path / "sdf")],
        devices=[
            _dev(devices[0], children=[{"name": "sda1", "type": "part"}]),  # the system's, partitioned
            _dev(devices[1]),  # blank
            _dev(devices[2], fstype="LVM2_member"),
            _dev(devices[3], fstype="zfs_member"),
            _dev(devices[4], mountpoints=["/srv"]),
            _dev(devices[5]),  # a VM's disk (passed through whole)
            _dev(devices[6], rm=True),  # a USB stick
            _dev(devices[7], size=512 * 1024**2),  # too small
            _dev(devices[8], rota=False, model="Samsung 990"),
            {"name": "/dev/loop0", "type": "loop", "size": 10 * 1024**3},
        ],
        by_id=by_id,
    )
    assert [d["path"] for d in found] == [str(by_id / "ata-WDC_WD5000_S2"), devices[8]]
    assert found[1]["rotational"] is False and found[0]["size"] == 500 * 1024**3


FREE = [{"path": f"/dev/disk/by-id/ata-D{i}"} for i in range(6)]


@pytest.fixture()
def zpool(monkeypatch):
    """zpool and zfs as zfs_storage runs them: each call recorded, `status` its JSON."""
    calls = []
    state = {"exists": False, "status": None}

    def run(*args, check=True):
        calls.append(args)
        if args[:2] == ("zpool", "list"):
            return subprocess.CompletedProcess(args, 0 if state["exists"] else 1, "", "")
        if args[:2] == ("zpool", "status"):
            return subprocess.CompletedProcess(args, 0, json.dumps(state["status"]), "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(zfs_storage, "_run", run)
    monkeypatch.setattr(zfs_storage, "list_pool", lambda name: {"name": name, "type": "zfs"})
    return calls, state


@pytest.mark.parametrize(
    "layout, n, vdevs",
    [
        ("single", 1, ["/dev/disk/by-id/ata-D0"]),
        ("mirror", 2, ["mirror", "/dev/disk/by-id/ata-D0", "/dev/disk/by-id/ata-D1"]),
        ("raidz2", 4, ["raidz2", *(f"/dev/disk/by-id/ata-D{i}" for i in range(4))]),
    ],
)
def test_a_pool_is_made_on_its_disks_with_4k_sectors(zpool, layout, n, vdevs):
    calls, _ = zpool
    zfs_storage.create_pool_on_disks("tank", layout, [d["path"] for d in FREE[:n]], FREE)
    create = calls[-1]
    assert create[:2] == ("zpool", "create") and "ashift=12" in create and "compression=lz4" in create
    assert list(create[create.index("tank") + 1 :]) == vdevs


@pytest.mark.parametrize(
    "layout, disks, message",
    [
        ("raidz1", ["/dev/disk/by-id/ata-D0", "/dev/disk/by-id/ata-D1"], "needs at least 3 disks"),
        ("mirror", ["/dev/disk/by-id/ata-D0", "/dev/sda"], "not a free disk"),
        ("mirror", ["/dev/disk/by-id/ata-D0", "/dev/disk/by-id/ata-D0"], "named twice"),
        ("raid5", ["/dev/disk/by-id/ata-D0"], "Unknown layout"),
    ],
)
def test_a_pool_is_refused_on_what_it_must_not_take(zpool, layout, disks, message):
    calls, _ = zpool
    with pytest.raises(zfs_storage.ZfsError, match=message):
        zfs_storage.create_pool_on_disks("tank", layout, disks, FREE)
    assert not any(c[:2] == ("zpool", "create") for c in calls)


def _disk(path, state="ONLINE", **errors):
    return {"name": path, "vdev_type": "disk", "path": path, "state": state, "read_errors": errors.get("r", 0),
            "write_errors": 0, "checksum_errors": errors.get("c", 0)}  # fmt: skip


def _status(state="ONLINE", second="ONLINE", scan=None):
    """`zpool status -j --json-int -P` of a RAIDZ1 on three disks, as OpenZFS 2.3 writes it (zpool-status(8))."""
    disks = {f"/dev/disk/by-id/ata-D{i}-part1": _disk(f"/dev/disk/by-id/ata-D{i}-part1") for i in range(3)}
    disks["/dev/disk/by-id/ata-D1-part1"] = _disk("/dev/disk/by-id/ata-D1-part1", second, r=3, c=1)
    pool = {
        "name": "tank",
        "state": state,
        "error_count": 0,
        "vdevs": {"tank": {"name": "tank", "vdev_type": "root", "vdevs": {
            "raidz1-0": {"name": "raidz1-0", "vdev_type": "raidz", "state": state, "read_errors": 0, "write_errors": 0,
                         "checksum_errors": 0, "vdevs": disks}}}},
        "spares": {"/dev/disk/by-id/ata-D5-part1": _disk("/dev/disk/by-id/ata-D5-part1", "AVAIL")},
    }  # fmt: skip
    if scan:
        pool["scan_stats"] = scan
    return {"output_version": {"command": "zpool status"}, "pools": {"tank": pool}}


def test_the_health_is_read_with_each_disk_and_its_errors(zpool):
    _, state = zpool
    state.update(exists=True, status=_status("DEGRADED", "FAULTED", {"function": "SCRUB", "state": "FINISHED",
                                                                       "end_time": 1700000000, "errors": 0}))  # fmt: skip
    health = zfs_storage.pool_status("tank")
    assert health["state"] == "DEGRADED" and health["scan"]["function"] == "SCRUB"
    (raidz, spare) = health["vdevs"]
    assert raidz["type"] == "raidz" and len(raidz["children"]) == 3 and spare["class"] == "spares"
    failed = raidz["children"][1]
    assert failed["state"] == "FAULTED" and failed["read_errors"] == 3 and failed["checksum_errors"] == 1
    assert zfs_storage.disks_of("tank") == [f"/dev/disk/by-id/ata-D{i}" for i in (0, 1, 2, 5)]


def test_disks_are_attached_added_and_replaced_only_where_they_belong(zpool):
    calls, state = zpool
    state.update(exists=True, status=_status())
    zfs_storage.attach_disk("tank", "raidz1-0", "/dev/disk/by-id/ata-D3", FREE)
    assert ("zpool", "attach", "tank", "raidz1-0", "/dev/disk/by-id/ata-D3") in calls  # RAIDZ expansion
    zfs_storage.add_vdev("tank", "raidz1", [f"/dev/disk/by-id/ata-D{i}" for i in (3, 4, 5)], FREE)
    assert ("zpool", "add", "tank", "raidz1", *(f"/dev/disk/by-id/ata-D{i}" for i in (3, 4, 5))) in calls
    zfs_storage.replace_disk("tank", "/dev/disk/by-id/ata-D1-part1", "/dev/disk/by-id/ata-D4", FREE)
    assert ("zpool", "replace", "tank", "/dev/disk/by-id/ata-D1-part1", "/dev/disk/by-id/ata-D4") in calls
    with pytest.raises(zfs_storage.ZfsError, match="not a disk or a vdev"):
        zfs_storage.attach_disk("tank", "/dev/sda", "/dev/disk/by-id/ata-D3", FREE)
    with pytest.raises(zfs_storage.ZfsError, match="not a disk of pool"):
        zfs_storage.replace_disk("tank", "/dev/sda1", "/dev/disk/by-id/ata-D4", FREE)
    with pytest.raises(zfs_storage.ZfsError, match="not a free disk"):
        zfs_storage.replace_disk("tank", "/dev/disk/by-id/ata-D1-part1", "/dev/disk/by-id/ata-D0-part1", FREE)


def test_a_destroyed_pool_gives_its_disks_back_blank(zpool, monkeypatch):
    calls, state = zpool
    state.update(exists=True, status=_status())
    monkeypatch.setattr(zfs_storage, "list_zvols", lambda name: [])
    monkeypatch.setattr(zfs_storage, "_backing_file_of", lambda name: None)
    zfs_storage.delete_pool("tank")
    after = calls[calls.index(("zpool", "destroy", "tank")) :]
    assert ("zpool", "labelclear", "-f", "/dev/disk/by-id/ata-D1-part1") in after
    assert [c[2] for c in after if c[0] == "wipefs"] == [f"/dev/disk/by-id/ata-D{i}" for i in (0, 1, 2, 5)]


def test_a_pool_that_degrades_is_notified_once_and_once_healthy_again(monkeypatch):
    sent = []
    states = iter(["active", "degraded", "degraded", "active", "active"])
    monkeypatch.setattr(zfs_storage, "module_loaded", lambda: True)
    monkeypatch.setattr(zfs_storage, "list_pools", lambda: [{"name": "tank", "state": next(states)}])
    monkeypatch.setattr(zfs_storage, "_watched", {})
    notify = lambda event, title, message, result="success": sent.append((event, title, result))  # noqa: E731
    for _ in range(5):
        zfs_storage.watch_once(notify)
    assert sent == [
        ("zfs_pool", "ZFS pool tank is DEGRADED", "failed"),
        ("zfs_pool", "ZFS pool tank is healthy again", "success"),
    ]


def test_the_daemon_takes_only_free_disks_whatever_the_api_sent(monkeypatch):
    made = []
    monkeypatch.setattr(ops_storage, "_disk_paths_in_use", lambda: set())
    monkeypatch.setattr(host_disks, "free_disks", lambda in_use=(): FREE[:2])
    monkeypatch.setattr(zfs_storage, "pool_exists", lambda name: False)
    monkeypatch.setattr(zfs_storage, "_run", lambda *a, check=True: made.append(a))
    monkeypatch.setattr(zfs_storage, "list_pool", lambda name: {"name": name})
    with pytest.raises(DaemonError) as e:
        privileged.run(
            "storage.zfs_pool_create", name="tank", layout="mirror", disks=["/dev/disk/by-id/ata-D0", "/dev/sda"]
        )
    assert e.value.kind == "refused" and made == []
    with pytest.raises(DaemonError) as e:
        privileged.run("storage.zfs_pool_create", name="tank", layout="raid5", disks=["/dev/disk/by-id/ata-D0"])
    assert e.value.kind == "invalid_arguments"
    privileged.run("storage.zfs_pool_create", name="tank", layout="mirror", disks=[d["path"] for d in FREE[:2]])
    assert made[-1][-3:] == ("mirror", "/dev/disk/by-id/ata-D0", "/dev/disk/by-id/ata-D1")


def test_the_routes_create_on_disks_and_read_the_health(client, auth_headers, monkeypatch):
    asked = []

    def ask(op, user, action, target, /, **kw):
        asked.append((op, {k: v for k, v in kw.items() if k not in ("refused", "failed")}))
        return {"loaded": True} if op == "storage.zfs_load_module" else {"name": target, "state": "ONLINE"}

    monkeypatch.setattr(privileged, "ask", ask)
    monkeypatch.setattr(zfs_storage, "pool_exists", lambda name: name == "tank")
    headers = auth_headers("root")
    body = {
        "name": "fast",
        "type": "zfs",
        "zfs_layout": "mirror",
        "zfs_disks": ["/dev/disk/by-id/ata-D0", "/dev/disk/by-id/ata-D1"],
    }
    assert client.post("/storage", json=body, headers=headers).status_code == 201
    assert asked[-1] == ("storage.zfs_pool_create", {"name": "fast", "layout": "mirror", "disks": body["zfs_disks"]})
    r = client.post("/storage", json={**body, "zfs_disks": []}, headers=headers)
    assert r.status_code == 422 and "layout and its disks" in r.json()["detail"]
    assert client.get("/storage/tank/zfs", headers=headers).json()["state"] == "ONLINE"
    assert client.get("/storage/nope/zfs", headers=headers).status_code == 404
    r = client.post(
        "/storage/tank/zfs/disks",
        json={"action": "replace", "disks": ["/dev/disk/by-id/ata-D2"], "target": "/dev/x"},
        headers=headers,
    )
    assert r.status_code == 200 and asked[-1][0] == "storage.zfs_pool_disks"
    assert client.post("/storage/tank/zfs/scrub", json={}, headers=headers).status_code == 200
