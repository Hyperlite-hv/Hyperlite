"""Growing a VM disk: qcow2/raw files (qemu-img when stopped, blockResize when running), ZFS zvols (volsize, then
blockResize when running), and clear refusals for iSCSI LUNs, CD-ROMs, shrinking and unchanged sizes."""

import subprocess

import libvirt
import pytest

from app.core import disk_resize, zfs_storage
from app.routers.vms import devices

GIB = 1024**3
ISCSI_DEV = "/dev/disk/by-path/ip-192.0.2.5:3260-iscsi-iqn.2026-01.example:store-lun-1"

DISKS = {
    "vda": "<disk type='file' device='disk'><driver name='qemu' type='qcow2'/>"
    "<source file='/var/lib/libvirt/images/vm1.qcow2'/><target dev='vda' bus='virtio'/></disk>",
    "vdb": "<disk type='file' device='disk'><driver name='qemu' type='raw'/>"
    "<source file='/var/lib/libvirt/images/vm1-data.img'/><target dev='vdb' bus='virtio'/></disk>",
    "vdc": "<disk type='block' device='disk'><driver name='qemu' type='raw'/>"
    "<source dev='/dev/zvol/tank/vm1-disk1'/><target dev='vdc' bus='virtio'/></disk>",
    "vdd": f"<disk type='block' device='disk'><driver name='qemu' type='raw'/>"
    f"<source dev='{ISCSI_DEV}'/><target dev='vdd' bus='virtio'/></disk>",
    "vde": "<disk type='file' device='disk'><driver name='qemu' type='vmdk'/>"
    "<source file='/var/lib/libvirt/images/old.vmdk'/><target dev='vde' bus='virtio'/></disk>",
    "sdz": "<disk type='file' device='cdrom'><driver name='qemu' type='raw'/><target dev='sdz' bus='sata'/></disk>",
}


class FakeDomain:
    def listAllCheckpoints(self):
        return []  # no replication checkpoint (app/core/checkpoints.py)

    def __init__(self, active=False, size_gb=10, job=None):
        self.active = active
        self.size = size_gb * GIB
        self.job = job or {}
        self.resized = []
        self._xml = f"<domain><name>vm1</name><devices>{''.join(DISKS.values())}</devices></domain>"

    def name(self):
        return "vm1"

    def XMLDesc(self, *_):
        return self._xml

    def isActive(self):
        return self.active

    def blockInfo(self, target):
        return (self.size, self.size // 2, self.size)

    def blockJobInfo(self, target, flags):
        return self.job

    def blockResize(self, target, size, flags):
        assert flags == libvirt.VIR_DOMAIN_BLOCK_RESIZE_BYTES
        self.resized.append((target, size))


class FakeConn:
    def __init__(self, domain):
        self.domain = domain

    def lookupByName(self, name):
        if name != "vm1":
            raise libvirt.libvirtError("no domain")
        return self.domain

    def storageVolLookupByPath(self, path):
        raise libvirt.libvirtError("not in a pool")

    def listAllStoragePools(self, flags):
        return []

    def close(self):
        pass


@pytest.fixture()
def commands(monkeypatch):
    """Records qemu-img and zfs calls instead of running them."""
    calls = {"qemu_img": [], "zfs": []}
    real_run = subprocess.run

    def fake_run(args, **kwargs):
        # subprocess is shared by the whole application: only qemu-img is faked.
        if list(args)[:1] != ["qemu-img"]:
            return real_run(args, **kwargs)
        calls["qemu_img"].append(list(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(disk_resize.subprocess, "run", fake_run)
    monkeypatch.setattr(zfs_storage, "grow_zvol", lambda pool, zvol, size: calls["zfs"].append((pool, zvol, size)))
    return calls


def _disk(target):
    return disk_resize.find_disk(FakeDomain(), target)


def test_each_kind_of_disk_gets_the_right_plan_or_a_clear_refusal():
    assert disk_resize.plan(_disk("vda")) == (
        {"kind": "file", "path": "/var/lib/libvirt/images/vm1.qcow2", "format": "qcow2"},
        None,
    )
    assert disk_resize.plan(_disk("vdb"))[0]["format"] == "raw"
    assert disk_resize.plan(_disk("vdc")) == ({"kind": "zvol", "pool": "tank", "zvol": "vm1-disk1"}, None)
    assert disk_resize.not_growable_code(_disk("vdd")) == "iscsi"
    assert "storage server" in disk_resize.plan(_disk("vdd"))[1][1]
    assert disk_resize.not_growable_code(_disk("vde")) == "non_gere"
    assert "CD-ROM" in disk_resize.plan(_disk("sdz"))[1][1]
    assert disk_resize.find_disk(FakeDomain(), "vdq") is None


def test_a_stopped_file_disk_is_grown_with_qemu_img(commands):
    domain = FakeDomain(active=False)
    old, new, live = disk_resize.grow(FakeConn(domain), domain, "vda", 15)
    assert (old, new, live) == (10 * GIB, 15 * GIB, False)
    assert commands["qemu_img"] == [
        ["qemu-img", "resize", "-f", "qcow2", "/var/lib/libvirt/images/vm1.qcow2", str(15 * GIB)]
    ]
    assert domain.resized == []


def test_a_running_file_disk_is_grown_live_without_qemu_img(commands):
    domain = FakeDomain(active=True)
    assert disk_resize.grow(FakeConn(domain), domain, "vdb", 12)[2] is True
    assert domain.resized == [("vdb", 12 * GIB)] and commands["qemu_img"] == []


def test_a_zvol_gets_its_volsize_raised_then_the_running_guest_is_told(commands):
    stopped = FakeDomain(active=False)
    disk_resize.grow(FakeConn(stopped), stopped, "vdc", 20)
    assert commands["zfs"] == [("tank", "vm1-disk1", 20 * GIB)] and stopped.resized == []
    running = FakeDomain(active=True)
    disk_resize.grow(FakeConn(running), running, "vdc", 20)
    assert running.resized == [("vdc", 20 * GIB)]


@pytest.mark.parametrize(
    ("target", "size", "status", "text"),
    [
        ("vda", 5, 422, "Shrinking a disk is not supported"),
        ("vda", 10, 422, "already 10 GB"),
        ("vdd", 50, 422, "iSCSI LUN"),
        ("sdz", 50, 422, "Only hard disks"),
        ("vdq", 50, 404, "not found"),
    ],
)
def test_refusals_change_nothing(commands, target, size, status, text):
    domain = FakeDomain(active=True)
    with pytest.raises(disk_resize.ResizeError) as err:
        disk_resize.grow(FakeConn(domain), domain, target, size)
    assert err.value.status == status and text in err.value.message
    assert domain.resized == [] and commands == {"qemu_img": [], "zfs": []}


def test_a_running_block_job_blocks_the_live_resize(commands):
    domain = FakeDomain(active=True, job={"type": 1, "cur": 5, "end": 10})
    with pytest.raises(disk_resize.ResizeError) as err:
        disk_resize.grow(FakeConn(domain), domain, "vda", 20)
    assert err.value.status == 409 and domain.resized == []


def test_a_qemu_img_failure_is_reported_not_hidden(monkeypatch):
    def failing(args, **kwargs):
        raise subprocess.CalledProcessError(1, args, "", 'Failed to get "write" lock')

    monkeypatch.setattr(disk_resize.subprocess, "run", failing)
    domain = FakeDomain(active=False)
    with pytest.raises(disk_resize.ResizeError) as err:
        disk_resize.grow(FakeConn(domain), domain, "vda", 20)
    assert err.value.status == 500 and "write" in err.value.message


def test_grow_zvol_validates_names_before_calling_zfs(monkeypatch):
    calls = []
    monkeypatch.setattr(zfs_storage, "_run", lambda *a, **k: calls.append(a) or subprocess.CompletedProcess(a, 0))
    with pytest.raises(zfs_storage.ZfsError):
        zfs_storage.grow_zvol("tank", "bad name;rm", GIB)
    assert calls == []
    zfs_storage.grow_zvol("tank", "vm1-disk1", 2 * GIB)
    assert calls[-1] == ("zfs", "set", f"volsize={2 * GIB}", "tank/vm1-disk1")


@pytest.fixture()
def api(client, auth_headers, monkeypatch, commands):
    monkeypatch.setenv("HYPERLITE_VM_MAX_DISK_GB", "100")
    domain = FakeDomain(active=False)
    monkeypatch.setattr(devices, "open_conn", lambda *a, **k: FakeConn(domain))
    return {"client": client, "headers": auth_headers("admin1"), "domain": domain, "commands": commands}


def test_the_endpoint_grows_the_disk_and_reports_old_and_new_sizes(api):
    r = api["client"].post("/vms/vm1/disks/vda/resize", json={"size_gb": 32}, headers=api["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["ancienne_taille_go"] == 10 and r.json()["taille_go"] == 32 and r.json()["a_chaud"] is False
    assert api["commands"]["qemu_img"][0][-1] == str(32 * GIB)


def test_the_endpoint_refuses_a_shrink_a_bad_target_and_a_size_over_the_limit(api):
    post = lambda path, size: api["client"].post(path, json={"size_gb": size}, headers=api["headers"])  # noqa: E731
    assert post("/vms/vm1/disks/vda/resize", 4).status_code == 422
    assert post("/vms/vm1/disks/VDA;x/resize", 20).status_code == 422
    assert post("/vms/vm1/disks/vda/resize", 101).status_code == 422
    assert post("/vms/nope/disks/vda/resize", 20).status_code == 404
    assert api["commands"]["qemu_img"] == []


def test_an_observer_cannot_resize(api, auth_headers):
    headers = auth_headers("watcher", role="observateur")
    r = api["client"].post("/vms/vm1/disks/vda/resize", json={"size_gb": 20}, headers=headers)
    assert r.status_code == 403 and api["commands"]["qemu_img"] == []


def test_the_disk_list_says_which_disks_can_be_grown(api):
    r = api["client"].get("/vms/vm1/disks", headers=api["headers"])
    assert r.status_code == 200, r.text
    codes = {d["cible"]: d["non_agrandissable"] for d in r.json()}
    assert codes == {"vda": None, "vdb": None, "vdc": None, "vdd": "iscsi", "vde": "non_gere", "sdz": "non_gere"}
