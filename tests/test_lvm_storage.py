"""LVM pools (app/core/lvm_storage.py): what libvirt's logical pool does not do, read from LVM's own reports. The
real behaviour (libvirt blind to thin volumes, unable to resize, its pool deletion removing the whole group) is in
docs/design/lvm-storage.md."""

import json
import subprocess
import time
import xml.etree.ElementTree as ET

import libvirt
import pytest

from app.core import lvm_storage, vm_snapshots

LVS = {
    "report": [
        {
            "lv": [
                {
                    "lv_name": "vm1-disk",
                    "lv_size": "1073741824",
                    "lv_attr": "-wi-a-----",
                    "pool_lv": "",
                    "data_percent": "",
                    "origin": "",
                },
                {
                    "lv_name": "thinpool",
                    "lv_size": "2147483648",
                    "lv_attr": "twi-aotz--",
                    "pool_lv": "",
                    "data_percent": "3.13",
                    "origin": "",
                },
                {
                    "lv_name": "vm2-disk",
                    "lv_size": "4294967296",
                    "lv_attr": "Vwi-a-tz--",
                    "pool_lv": "thinpool",
                    "data_percent": "1.56",
                    "origin": "",
                },
            ]
        }
    ]
}


@pytest.fixture()
def lvm(monkeypatch):
    calls = []
    answers = {
        "lvs": json.dumps(LVS),
        "vgs": json.dumps(
            {"report": [{"vg": [{"vg_name": "hlvg", "vg_size": "10733223936", "vg_free": "6438256640"}]}]}
        ),
    }

    def run(args, node=None):
        calls.append((args, node))
        return answers.get(args[1], "")

    monkeypatch.setattr(lvm_storage, "_run", run)
    return {"calls": calls, "answers": answers}


def test_every_volume_is_read_from_lvm_thin_ones_included(lvm):
    volumes = {v["name"]: v for v in lvm_storage.logical_volumes("hlvg")}
    assert volumes["vm1-disk"]["kind"] == "linear" and volumes["vm1-disk"]["path"] == "/dev/hlvg/vm1-disk"
    assert volumes["thinpool"]["kind"] == "thin_pool" and volumes["thinpool"]["data_percent"] == 3.13
    assert volumes["vm2-disk"]["kind"] == "thin" and volumes["vm2-disk"]["thin_pool"] == "thinpool"
    assert lvm_storage.volume_groups() == [{"name": "hlvg", "size": 10733223936, "free": 6438256640}]


def test_a_pool_is_not_removed_while_a_volume_libvirt_does_not_see_is_in_it(lvm):
    with pytest.raises(lvm_storage.LvmError, match="still holds 3 volume"):
        lvm_storage.refuse_removal_if_not_empty("hlvg")
    lvm["answers"]["lvs"] = json.dumps({"report": [{"lv": [LVS["report"][0]["lv"][2]]}]})
    with pytest.raises(lvm_storage.LvmError, match="vm2-disk"):
        lvm_storage.refuse_removal_if_not_empty("hlvg")  # a thin volume alone: libvirt calls this pool empty
    lvm["answers"]["lvs"] = json.dumps({"report": [{"lv": []}]})
    lvm_storage.refuse_removal_if_not_empty("hlvg")


def test_a_volume_is_grown_by_lvextend_never_shrunk(lvm):
    lvm_storage.extend("hlvg", "vm1-disk", 2 * 2**30)
    assert lvm["calls"][-1][0] == ["lvm", "lvextend", "-L", "2048m", "hlvg/vm1-disk"]
    with pytest.raises(lvm_storage.LvmError, match="only grown"):
        lvm_storage.extend("hlvg", "vm1-disk", 2**30)
    with pytest.raises(lvm_storage.LvmError, match="not found"):
        lvm_storage.extend("hlvg", "nope", 2 * 2**30)


@pytest.mark.parametrize("name", ["", "-x", "a b", "a;b", "../x", "a/b", "$(id)"])
def test_names_reach_lvm_only_when_they_are_names(lvm, name):
    with pytest.raises(lvm_storage.LvmError, match="Invalid"):
        lvm_storage.logical_volumes(name)
    assert lvm["calls"] == []


def test_a_remote_node_is_reached_over_ssh(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "app.core.cluster.get_node", lambda name: {"hostname": "10.0.0.2", "ssh_port": 22, "ssh_user": "root"}
    )
    monkeypatch.setattr("app.core.cluster.node_ssh_options", lambda extra=None: ["-o", "BatchMode=yes", *(extra or [])])

    class Done:
        returncode, stdout, stderr = 0, json.dumps({"report": [{"lv": []}]}), ""

    monkeypatch.setattr(lvm_storage.subprocess, "run", lambda args, **kw: seen.append(args) or Done())
    assert lvm_storage.logical_volumes("hlvg", node="node-b") == []
    assert seen[0][0] == "ssh" and seen[0][-2] == "root@10.0.0.2" and seen[0][-1].startswith("lvm lvs ")


# ---- The API: LVM pools, VM disks on them, their growth and their snapshots ----


class _Vol:
    def __init__(self, name, vg="hlvg"):
        self._name, self.vg, self.deleted = name, vg, False

    def name(self):
        return self._name

    def path(self):
        return f"/dev/{self.vg}/{self._name}"

    def info(self):
        return [0, 2**30, 2**30]

    def delete(self, flags=0):
        self.deleted = True


class _Pool:
    def __init__(self, xml, volumes=()):
        self.xml, self.volumes, self.built, self.active, self.events = xml, list(volumes), False, False, []

    def build(self, flags=0):
        self.built = True

    def create(self, flags=0):
        self.active = True

    def setAutostart(self, on):
        pass

    def autostart(self):
        return 1

    def isActive(self):
        return self.active

    def refresh(self, flags=0):
        pass

    def listAllVolumes(self, flags=0):
        return list(self.volumes)

    def XMLDesc(self, flags=0):
        return self.xml

    def info(self):
        return [2, 10 * 2**30, 2**30, 9 * 2**30]

    def name(self):
        return "lvmpool"

    def UUIDString(self):
        return "00000000-0000-4000-8000-000000000001"

    def connect(self):
        return self.conn

    def destroy(self):
        self.events.append("destroy")
        self.active = False

    def undefine(self):
        self.events.append("undefine")

    def delete(self, flags=0):
        raise AssertionError("libvirt's pool deletion removes the whole volume group: never called")


class _Conn:
    def __init__(self):
        self.pools = {}

    def storagePoolLookupByName(self, name):
        if name not in self.pools:
            raise libvirt.libvirtError("Storage pool not found")
        return self.pools[name]

    def storagePoolDefineXML(self, xml):
        pool = _Pool(xml)
        pool.conn = self
        self.pools[ET.fromstring(xml).findtext("name")] = pool
        return pool

    def listAllDomains(self, flags=0):
        return []

    def close(self):
        pass


@pytest.fixture()
def storage_api(monkeypatch, lvm, daemon_libvirt):
    from app.routers import storage

    conn = _Conn()
    monkeypatch.setattr(storage, "open_conn", lambda *a, **k: conn)
    daemon_libvirt(conn)  # this node's pools are hyperlite-daemon's, in this process in the tests
    monkeypatch.setattr(storage.zfs_storage, "pool_exists", lambda name: False)
    monkeypatch.setattr(storage.shutil, "which", lambda cmd: f"/usr/sbin/{cmd}")
    monkeypatch.setattr(storage, "get_disk_paths_in_use", lambda conn: set())
    return conn


def test_an_lvm_pool_on_an_existing_group_or_a_disk(client, auth_headers, storage_api):
    admin = auth_headers("root")
    r = client.post("/storage", json={"name": "lvmpool", "type": "lvm", "lvm_vg": "hlvg"}, headers=admin)
    assert r.status_code == 201, r.text
    pool = storage_api.pools["lvmpool"]
    root = ET.fromstring(pool.xml)
    assert root.get("type") == "logical" and root.findtext("source/name") == "hlvg"
    assert root.find("source/device") is None and root.findtext("target/path") == "/dev/hlvg"
    assert not pool.built and pool.active, "an existing group is not built again"

    r = client.post(
        "/storage", json={"name": "lvmdisk", "type": "lvm", "lvm_vg": "vg2", "lvm_device": "/dev/vdb"}, headers=admin
    )
    assert r.status_code == 201, r.text
    pool = storage_api.pools["lvmdisk"]
    assert ET.fromstring(pool.xml).find("source/device").get("path") == "/dev/vdb" and pool.built

    for bad in (
        {"lvm_vg": "a;b"},
        {"lvm_vg": "vg3", "lvm_device": "/dev/../etc/passwd"},
        {"lvm_vg": "vg3", "lvm_device": "sdb"},
    ):
        r = client.post("/storage", json={"name": "badpool", "type": "lvm", **bad}, headers=admin)
        assert r.status_code == 422, (bad, r.text)


def test_an_lvm_pool_is_kept_while_lvm_shows_a_volume_libvirt_does_not(client, auth_headers, storage_api, lvm):
    admin = auth_headers("root")
    client.post("/storage", json={"name": "lvmpool", "type": "lvm", "lvm_vg": "hlvg"}, headers=admin)
    pool = storage_api.pools["lvmpool"]
    listed = client.get("/storage/lvmpool/volumes", headers=admin).json()["items"]
    assert {v["name"]: v.get("lvm_kind") for v in listed} == {
        "vm1-disk": "linear",
        "thinpool": "thin_pool",
        "vm2-disk": "thin",
    }

    lvm["answers"]["lvs"] = json.dumps({"report": [{"lv": [LVS["report"][0]["lv"][2]]}]})  # one thin volume only
    r = client.delete("/storage/lvmpool?confirm=true", headers=admin)
    assert r.status_code == 409 and "vm2-disk" in r.json()["detail"], "libvirt sees no volume, LVM does"
    assert pool.events == [] and pool.active

    lvm["answers"]["lvs"] = json.dumps({"report": [{"lv": []}]})
    assert client.delete("/storage/lvmpool?confirm=true", headers=admin).status_code == 200
    assert pool.events == ["destroy", "undefine"], "stopped and forgotten; the group itself is left on the disk"


def test_a_vm_disk_is_a_logical_volume_with_the_image_written_raw(monkeypatch):
    ran = []
    monkeypatch.setattr(
        lvm_storage.subprocess,
        "run",
        lambda args, **kw: ran.append(args) or type("R", (), {"stdout": '{"virtual-size": 3221225472}'})(),
    )
    made = []

    class Pool:
        def createXML(self, xml, flags=0):
            made.append(xml)
            return _Vol(ET.fromstring(xml).findtext("name"))

    assert lvm_storage.create_vm_disk(Pool(), "web", 0, 10, base_image="/base.qcow2") == "/dev/hlvg/web"
    assert ET.fromstring(made[0]).find("capacity").text == "10"
    assert ran[-1] == ["qemu-img", "convert", "-O", "raw", "/base.qcow2", "/dev/hlvg/web"]
    assert lvm_storage.create_vm_disk(Pool(), "web", 1, 5) == "/dev/hlvg/web-2" and len(ran) == 1, (
        "a data disk: nothing written"
    )
    lvm_storage.create_vm_disk(Pool(), "imp", 0, 1, import_source="/up.vmdk")
    assert ET.fromstring(made[-1]).find("capacity").text == "3", "an import takes its source's size"

    vols = []

    class Failing:
        def createXML(self, xml, flags=0):
            vols.append(_Vol("web"))
            return vols[-1]

    def broken(args, **kw):
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(lvm_storage.subprocess, "run", broken)
    with pytest.raises(subprocess.CalledProcessError):
        lvm_storage.create_vm_disk(Failing(), "web", 0, 10, base_image="/base.qcow2")
    assert vols[0].deleted, "a disk the image could not be written to is removed"


def test_a_logical_volume_disk_is_grown_with_lvextend(monkeypatch, lvm):
    from app.core import disk_resize

    disk = ET.fromstring(
        "<disk type='block' device='disk'><driver name='qemu' type='raw'/><source dev='/dev/hlvg/vm1-disk'/>"
        "<target dev='sda' bus='scsi'/></disk>"
    )
    how, refusal = disk_resize.plan(disk)
    assert refusal is None and how == {"kind": "lv", "vg": "hlvg", "lv": "vm1-disk", "path": "/dev/hlvg/vm1-disk"}
    other = ET.fromstring(
        "<disk type='block' device='disk'><source dev='/dev/sdc'/><target dev='sdb' bus='scsi'/></disk>"
    )
    assert disk_resize.plan(other)[1][1].startswith("This block device is not managed by Hyperlite")

    class Domain:
        def XMLDesc(self, flags=0):
            return f"<domain><name>web</name><devices>{ET.tostring(disk, encoding='unicode')}</devices></domain>"

        def name(self):
            return "web"

        def blockInfo(self, dev):
            return [2**30, 2**30, 2**30]

        def isActive(self):
            return False

    monkeypatch.setattr(disk_resize, "refresh_pools_for_paths", lambda conn, paths: None)
    assert disk_resize.grow(None, Domain(), "sda", 2) == (2**30, 2 * 2**30, False)
    assert lvm["calls"][-1][0] == ["lvm", "lvextend", "-L", "2048m", "hlvg/vm1-disk"]


def test_a_deleted_vm_takes_its_logical_volumes_and_nothing_else(monkeypatch):
    from app.core import vm_delete

    deleted = []

    class Pool:
        def __init__(self, kind):
            self.kind = kind

        def XMLDesc(self, flags=0):
            return f"<pool type='{self.kind}'/>"

    class Vol:
        def __init__(self, path, kind):
            self.path, self.kind = path, kind

        def storagePoolLookupByVolume(self):
            return Pool(self.kind)

        def delete(self, flags=0):
            deleted.append(self.path)

    known = {"/dev/hlvg/web": "logical", "/dev/hlvg/web-2": "logical", "/dev/disk/by-path/ip-lun-1": "iscsi"}

    class Conn:
        def storageVolLookupByPath(self, path):
            if path not in known:
                raise libvirt.libvirtError("no storage vol with matching path")
            return Vol(path, known[path])

        def listAllStoragePools(self, flags=0):
            class LogicalPool:
                def XMLDesc(self, flags=0):
                    return "<pool type='logical'><source><name>hlvg</name></source></pool>"

            return [LogicalPool()]

        def lookupByName(self, name):
            return Domain()

        def listAllDomains(self, flags=0):
            return []

    disks = ["/dev/hlvg/web", "/dev/hlvg/web-2", "/dev/disk/by-path/ip-lun-1", "/dev/othervg/root"]

    class Domain:
        def XMLDesc(self, flags=0):
            body = "".join(
                f"<disk type='block' device='disk'><source dev='{d}'/><target dev='sd{c}'/></disk>"
                for d, c in zip(disks, "abcd", strict=True)
            )
            return f"<domain><name>web</name><devices>{body}</devices></domain>"

        def isActive(self):
            return False

        def undefineFlags(self, flags):
            pass

    monkeypatch.setattr(vm_delete.vm_records, "release_addresses", lambda conn, ifaces: None)
    vm_delete.delete(Conn(), "web")
    assert deleted == ["/dev/hlvg/web", "/dev/hlvg/web-2"], "an iSCSI LUN or a group outside a pool stays"


def test_a_vm_on_lvm_is_told_apart_from_the_xml_alone():
    def domain(*devs):
        disks = "".join(f"<disk type='block' device='disk'><source dev='{d}'/></disk>" for d in devs)
        return ET.fromstring(
            f"<domain><devices>{disks}<disk type='file' device='disk'><source file='/a.qcow2'/></disk></devices></domain>"
        )

    assert lvm_storage.on_lvm(domain("/dev/hlvg/web"))
    assert not lvm_storage.on_lvm(domain("/dev/zvol/tank/web", "/dev/disk/by-path/ip-lun-1", "/dev/sdb"))
    assert not lvm_storage.on_lvm(domain())


class FakeLvm:
    """LVM as the thin functions drive it: volumes with their content, created, snapshotted, renamed and removed
    through the same commands, so a test sees what a sequence of them leaves."""

    def __init__(self, monkeypatch):
        self.vgs = {"hlvg": {}}
        self.fail = set()  # (verb, volume name) that fails
        self.calls = []
        monkeypatch.setattr(lvm_storage, "_run", self.run)

    def add(self, name, kind="thin", data="", tags=(), size=2**30, vg="hlvg"):
        attr = {"thin": "Vwi-a-tz--", "thin_pool": "twi-aotz--", "linear": "-wi-a-----"}[kind]
        self.vgs[vg][name] = {"attr": attr, "data": data, "tags": list(tags), "size": size, "pct": "1.50"}

    def run(self, args, node=None):
        self.calls.append(args)
        verb = args[1]
        if verb == "lvs":
            vg = self.vgs[args[-1]]
            rows = [
                {
                    "lv_name": n,
                    "lv_size": str(v["size"]),
                    "lv_attr": v["attr"],
                    "pool_lv": "",
                    "data_percent": v["pct"] if v["attr"][0] in "tV" else "",
                    "origin": "",
                    "lv_tags": ",".join(v["tags"]),
                    "lv_time": "2026-10-23 10:51:28 +0000",
                }
                for n, v in vg.items()
            ]
            return json.dumps({"report": [{"lv": rows}]})
        target = args[-1]
        name = args[args.index("-n") + 1] if "-n" in args else target.split("/")[-1]
        if (verb, name) in self.fail:
            raise lvm_storage.LvmError(f"{verb} {name} failed")
        if verb == "lvcreate" and "thin-pool" in args:
            self.add(name, "thin_pool", tags=[args[args.index("--addtag") + 1]], vg=target)
        elif verb == "lvcreate" and "-s" in args:
            vg, origin = target.split("/")
            self.vgs[vg][name] = dict(self.vgs[vg][origin], tags=[])
        elif verb == "lvcreate":
            self.add(
                name,
                size=int(args[args.index("-V") + 1][:-1]) * 2**30,
                vg=args[args.index("--thinpool") + 1].split("/")[0],
            )
        elif verb == "lvchange":
            vg, lv = target.split("/")
            self.vgs[vg][lv]["tags"].append(args[args.index("--addtag") + 1])
        elif verb == "lvrename":
            vg, old = args[2].split("/")
            self.vgs[vg][args[3].split("/")[1]] = self.vgs[vg].pop(old)
        elif verb == "lvremove":
            vg, lv = target.split("/")
            del self.vgs[vg][lv]
        return ""

    def names(self, vg="hlvg"):
        return sorted(self.vgs[vg])

    def data(self, lv, vg="hlvg"):
        return self.vgs[vg][lv]["data"]


@pytest.fixture()
def thin(monkeypatch):
    fake = FakeLvm(monkeypatch)
    fake.add("vmthin", "thin_pool", tags=[lvm_storage.THIN_TAG], size=8 * 2**30)
    fake.add("web", data="v1")
    fake.add("web-2", data="d1")
    return fake


DISKS = [("hlvg", "web"), ("hlvg", "web-2")]


def test_a_groups_thin_pool_is_created_adopted_and_only_one(monkeypatch):
    fake = FakeLvm(monkeypatch)
    lvm_storage.ensure_thin_pool("hlvg", "vmthin")
    assert fake.vgs["hlvg"]["vmthin"]["tags"] == [lvm_storage.THIN_TAG]
    assert "95%FREE" in fake.calls[-1], "room left for LVM's spare metadata"
    lvm_storage.ensure_thin_pool("hlvg", "vmthin")
    assert fake.names() == ["vmthin"], "adopting it again changes nothing"
    with pytest.raises(lvm_storage.LvmError, match="already has"):
        lvm_storage.ensure_thin_pool("hlvg", "other")

    fake2 = FakeLvm(monkeypatch)
    fake2.add("existing", "thin_pool")
    fake2.add("plain", "linear")
    with pytest.raises(lvm_storage.LvmError, match="not a thin pool"):
        lvm_storage.ensure_thin_pool("hlvg", "plain")
    lvm_storage.ensure_thin_pool("hlvg", "existing")
    assert fake2.vgs["hlvg"]["existing"]["tags"] == [lvm_storage.THIN_TAG], "an existing thin pool is adopted"


def test_the_pools_own_empty_thin_pool_does_not_keep_the_pool(thin):
    with pytest.raises(lvm_storage.LvmError, match="web"):
        lvm_storage.refuse_removal_if_not_empty("hlvg")
    del thin.vgs["hlvg"]["web"], thin.vgs["hlvg"]["web-2"]
    lvm_storage.refuse_removal_if_not_empty("hlvg")
    thin.add("foreign", "thin_pool")
    with pytest.raises(lvm_storage.LvmError, match="foreign"):
        lvm_storage.refuse_removal_if_not_empty("hlvg")


def test_only_thin_disks_get_lvm_snapshots(thin):
    assert lvm_storage.thin_disks(["/dev/hlvg/web", "/dev/hlvg/web-2"]) == DISKS
    thin.add("thick", "linear")
    assert lvm_storage.thin_disks(["/dev/hlvg/web", "/dev/hlvg/thick"]) is None
    assert lvm_storage.thin_disks(["/dev/hlvg/web", "/dev/sdb"]) is None


def test_a_snapshot_takes_every_disk_or_none(thin):
    lvm_storage.snapshot(DISKS, "s1")
    assert {"snap_web_s1", "snap_web-2_s1"} <= set(thin.names())
    assert lvm_storage.list_snapshots(DISKS) == [{"name": "s1", "created_at": "1792752688"}]
    with pytest.raises(lvm_storage.LvmError, match="already exists"):
        lvm_storage.snapshot(DISKS, "s1")
    thin.fail.add(("lvcreate", "snap_web-2_s2"))
    with pytest.raises(lvm_storage.LvmError):
        lvm_storage.snapshot(DISKS, "s2")
    assert "snap_web_s2" not in thin.names(), "the first disk's snapshot is removed when the second fails"


def test_a_snapshot_of_another_vm_with_a_longer_name_is_not_listed(thin):
    thin.add("web-2_x")  # cannot be a VM disk (no underscore in VM names); shown for the prefix only
    lvm_storage.snapshot(DISKS, "s1")
    thin.add("snap_webx_s9")
    assert [s["name"] for s in lvm_storage.list_snapshots(DISKS)] == ["s1"]


def test_a_rollback_brings_every_disk_back_and_keeps_the_snapshot(thin):
    lvm_storage.snapshot(DISKS, "s1")
    thin.vgs["hlvg"]["web"]["data"] = "v2"
    thin.vgs["hlvg"]["web-2"]["data"] = "d2"
    lvm_storage.rollback(DISKS, "s1")
    assert (thin.data("web"), thin.data("web-2")) == ("v1", "d1")
    assert "snap_web_s1" in thin.names() and not any(n.endswith("_rollback") for n in thin.names())
    assert any("-kn" in c and c[c.index("-n") + 1] == "web" for c in thin.calls), "the disk is active again"
    thin.vgs["hlvg"]["web"]["data"] = "v3"
    lvm_storage.rollback(DISKS, "s1")
    assert thin.data("web") == "v1", "the same snapshot serves again"


def test_a_rollback_that_fails_puts_every_disk_back_as_it_was(thin):
    lvm_storage.snapshot(DISKS, "s1")
    thin.vgs["hlvg"]["web"]["data"] = "v2"
    thin.vgs["hlvg"]["web-2"]["data"] = "d2"
    thin.fail.add(("lvcreate", "web-2"))
    with pytest.raises(lvm_storage.LvmError):
        lvm_storage.rollback(DISKS, "s1")
    assert (thin.data("web"), thin.data("web-2")) == ("v2", "d2")
    assert not any(n.endswith("_rollback") for n in thin.names())
    before = thin.names()
    with pytest.raises(lvm_storage.LvmError, match="not found"):
        lvm_storage.rollback(DISKS, "nope")
    assert thin.names() == before, "an unknown snapshot is refused before anything moves"


def test_a_snapshot_is_deleted_on_every_disk(thin):
    lvm_storage.snapshot(DISKS, "s1")
    del thin.vgs["hlvg"]["snap_web-2_s1"]  # a disk added after the snapshot has none
    lvm_storage.delete_snapshot(DISKS, "s1")
    assert not any(n.startswith("snap_") for n in thin.names())


def test_a_deleted_vms_thin_disks_go_with_their_snapshots(thin):
    lvm_storage.snapshot(DISKS, "s1")
    thin.add("other", data="x")
    lvm_storage.snapshot([("hlvg", "other")], "s1")

    class Conn:
        def storageVolLookupByPath(self, path):
            raise libvirt.libvirtError("libvirt does not see thin volumes")

        def listAllStoragePools(self, flags=0):
            class LogicalPool:
                def XMLDesc(self, flags=0):
                    return "<pool type='logical'><source><name>hlvg</name></source></pool>"

            return [LogicalPool()]

    assert lvm_storage.delete_vm_volume(Conn(), "/dev/hlvg/web") is True
    assert "web" not in thin.names() and "snap_web_s1" not in thin.names()
    assert {"other", "snap_other_s1", "web-2", "snap_web-2_s1"} <= set(thin.names())
    assert lvm_storage.delete_vm_volume(Conn(), "/dev/hlvg/vmthin") is False, "never the thin pool"
    thin.vgs["vg9"] = {}
    thin.add("lone", vg="vg9")
    assert lvm_storage.delete_vm_volume(Conn(), "/dev/vg9/lone") is False, "a group no pool manages"
    assert "lone" in thin.names("vg9")


def test_a_thin_vm_disk_skips_the_images_zeros(monkeypatch, thin):
    ran = []
    monkeypatch.setattr(lvm_storage.subprocess, "run", lambda args, **k: ran.append(args))

    class Pool:
        def XMLDesc(self, flags=0):
            return "<pool type='logical'><source><name>hlvg</name></source></pool>"

        def createXML(self, xml, flags=0):
            raise AssertionError("a thin disk is not made through libvirt")

    assert (
        lvm_storage.create_vm_disk(Pool(), "db", 0, 8, base_image="/base.qcow2", thin_pool="vmthin") == "/dev/hlvg/db"
    )
    assert thin.vgs["hlvg"]["db"]["size"] == 8 * 2**30
    assert ran[-1] == ["qemu-img", "convert", "-n", "--target-is-zero", "-O", "raw", "/base.qcow2", "/dev/hlvg/db"]

    def broken(args, **k):
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(lvm_storage.subprocess, "run", broken)
    with pytest.raises(subprocess.CalledProcessError):
        lvm_storage.create_vm_disk(Pool(), "db2", 0, 8, base_image="/base.qcow2", thin_pool="vmthin")
    assert "db2" not in thin.names(), "a failed copy removes the volume"


def test_an_lvm_thin_pool_is_made_with_its_pool_or_the_pool_is_not(client, auth_headers, storage_api, monkeypatch):
    admin = auth_headers("root")
    made = []
    monkeypatch.setattr(lvm_storage, "ensure_thin_pool", lambda vg, name, node=None: made.append((vg, name, node)))
    # What hyperlite-daemon's LVM report says of the group (storage.lvm_volumes): the pool's thin pool, a quarter full.
    thin_pool = {"name": "vmthin", "size": 8 * 2**30, "data_percent": 25.0, "kind": "thin_pool"}
    monkeypatch.setattr(
        lvm_storage, "logical_volumes", lambda vg, node=None: [{**thin_pool, "tags": [lvm_storage.THIN_TAG]}]
    )
    r = client.post(
        "/storage",
        json={"name": "thinpool1", "type": "lvm", "lvm_vg": "hlvg", "lvm_thin_pool": "vmthin"},
        headers=admin,
    )
    assert r.status_code == 201, r.text
    assert made == [("hlvg", "vmthin", None)]
    body = r.json()
    assert body["lvm_thin_pool"] == "vmthin" and body["capacity_gb"] == 8 and body["available_gb"] == 6, (
        "the thin pool's own fill"
    )

    def refuse(vg, name, node=None):
        raise lvm_storage.LvmError("The volume group 'hlvg' already has its thin pool 'vmthin'")

    monkeypatch.setattr(lvm_storage, "ensure_thin_pool", refuse)
    r = client.post(
        "/storage", json={"name": "thinpool2", "type": "lvm", "lvm_vg": "hlvg", "lvm_thin_pool": "other"}, headers=admin
    )
    assert r.status_code == 422 and "already has" in r.json()["detail"]
    assert storage_api.pools["thinpool2"].events == ["destroy", "undefine"], "no pool left half made"
    r = client.post(
        "/storage", json={"name": "thinpool3", "type": "lvm", "lvm_vg": "hlvg", "lvm_thin_pool": "a;b"}, headers=admin
    )
    assert r.status_code == 422


@pytest.fixture()
def vm_api(monkeypatch, thin, daemon_libvirt):
    from app.routers.vms import snapshots

    class Domain:
        def __init__(self, disks):
            self.disks = disks
            self.active = False
            self.events = []

        def XMLDesc(self, flags=0):
            body = "".join(f"<disk type='block' device='disk'><source dev='{d}'/></disk>" for d in self.disks)
            return f"<domain><name>web</name><devices>{body}</devices></domain>"

        def isActive(self):
            return self.active

        def suspend(self):
            self.events.append("suspend")

        def resume(self):
            self.events.append("resume")

    domains = {"web": Domain(["/dev/hlvg/web", "/dev/hlvg/web-2"])}

    class Conn:
        def lookupByName(self, name):
            return domains[name]

        def close(self):
            pass

    monkeypatch.setattr(snapshots, "open_conn", lambda *a, **k: Conn())
    daemon_libvirt(Conn())  # hyperlite-daemon takes, restores and deletes the snapshots
    monkeypatch.setattr(vm_snapshots.iscsi, "iscsi_disks_of_domain", lambda domain: [])
    return domains


def _wait(client, headers, task_id):
    for _ in range(200):
        task = client.get(f"/tasks/{task_id}", headers=headers).json()
        if task["status"] in ("succeeded", "failed"):
            return task
        time.sleep(0.05)
    raise AssertionError("the task did not end")


def test_the_snapshots_of_a_vm_on_lvm_thin(client, auth_headers, vm_api, thin):
    admin = auth_headers("root")
    vm_api["web"].active = True
    r = client.post("/vms/web/snapshots", json={"name": "s1"}, headers=admin)
    assert r.status_code == 202, r.text
    assert _wait(client, admin, r.json()["task_id"])["status"] == "succeeded"
    assert vm_api["web"].events == ["suspend", "resume"], "two disks of a running VM: paused for the same instant"
    listed = client.get("/vms/web/snapshots", headers=admin).json()["items"]
    assert [(s["name"], s["vm_state"]) for s in listed] == [("s1", "disk_only")]

    thin.vgs["hlvg"]["web"]["data"] = "v2"
    r = client.post("/vms/web/snapshots/s1/restore?confirm=true", headers=admin)
    assert r.status_code == 409, "never under a running VM"
    vm_api["web"].active = False
    assert client.post("/vms/web/snapshots/s1/restore", headers=admin).status_code == 400
    assert client.post("/vms/web/snapshots/nope/restore?confirm=true", headers=admin).status_code == 404
    r = client.post("/vms/web/snapshots/s1/restore?confirm=true", headers=admin)
    assert r.status_code == 202 and _wait(client, admin, r.json()["task_id"])["status"] == "succeeded"
    assert thin.data("web") == "v1"

    assert client.delete("/vms/web/snapshots/nope", headers=admin).status_code == 404
    assert client.delete("/vms/web/snapshots/s1", headers=admin).status_code == 200
    assert client.get("/vms/web/snapshots", headers=admin).json()["items"] == []


def test_a_vm_on_thick_lvm_is_told_snapshots_need_lvm_thin(client, auth_headers, vm_api, thin):
    admin = auth_headers("root")
    thin.add("thick", "linear")
    vm_api["web"].disks = ["/dev/hlvg/thick"]
    r = client.post("/vms/web/snapshots", json={"name": "s1"}, headers=admin)
    assert r.status_code == 422 and "LVM-thin" in r.json()["detail"]
    assert client.get("/vms/web/snapshots", headers=admin).json()["items"] == []
