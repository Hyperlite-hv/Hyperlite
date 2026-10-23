"""LVM pools (app/core/lvm_storage.py): what libvirt's logical pool does not do, read from LVM's own reports. The
real behaviour (libvirt blind to thin volumes, unable to resize, its pool deletion removing the whole group) is in
docs/design/lvm-storage.md."""

import json
import subprocess
import xml.etree.ElementTree as ET

import libvirt
import pytest

from app.core import lvm_storage

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
def storage_api(monkeypatch, lvm):
    from app.routers import storage

    conn = _Conn()
    monkeypatch.setattr(storage, "open_conn", lambda *a, **k: conn)
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


def test_a_vm_on_lvm_is_told_its_snapshots_are_not_there_yet(client, auth_headers, monkeypatch):
    from app.routers.vms import snapshots

    class Domain:
        def XMLDesc(self, flags=0):
            return (
                "<domain><name>web</name><devices><disk type='block' device='disk'><source dev='/dev/hlvg/web'/>"
                "<target dev='sda' bus='scsi'/></disk></devices></domain>"
            )

        def isActive(self):
            return False

    class Conn:
        def lookupByName(self, name):
            return Domain()

        def close(self):
            pass

    monkeypatch.setattr(snapshots, "open_conn", lambda *a, **k: Conn())
    monkeypatch.setattr(snapshots, "_zvol_disks_of_domain", lambda domain: [])
    monkeypatch.setattr(snapshots.iscsi, "iscsi_disks_of_domain", lambda domain: [])
    r = client.post("/vms/web/snapshots", json={"name": "snap1"}, headers=auth_headers("root"))
    assert r.status_code == 422 and "LVM" in r.json()["detail"]


def test_a_deleted_vm_takes_its_logical_volumes_and_nothing_else(monkeypatch):
    from app.routers.vms import lifecycle

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

    disks = ["/dev/hlvg/web", "/dev/hlvg/web-2", "/dev/disk/by-path/ip-lun-1", "/dev/othervg/root"]

    class Domain:
        def XMLDesc(self, flags=0):
            body = "".join(
                f"<disk type='block' device='disk'><source dev='{d}'/><target dev='sd{c}'/></disk>"
                for d, c in zip(disks, "abcd", strict=True)
            )
            return f"<domain><name>web</name><devices>{body}</devices></domain>"

        def undefineFlags(self, flags):
            pass

    monkeypatch.setattr(lifecycle.vm_records, "reservations", lambda root: [])
    monkeypatch.setattr(lifecycle.vm_records, "release_addresses", lambda conn, ifaces: None)
    monkeypatch.setattr(lifecycle.vm_records, "forget", lambda name, node=None: None)
    monkeypatch.setattr(lifecycle, "refresh_pools_for_paths", lambda conn, paths: None)
    lifecycle._perform_vm_deletion(Conn(), Domain(), "web")
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
