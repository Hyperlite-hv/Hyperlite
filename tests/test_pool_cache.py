"""Disks written outside libvirt (qemu-img, copies, restores) are unknown to libvirt's volume cache until their
pool is refreshed. Locating a disk's pool, looking a volume up and moving a disk must not depend on that cache."""

import libvirt
import pytest

from app.core import disk_move
from app.core.libvirt_utils import lookup_volume, pool_for_path, refresh_pools_for_paths
from app.routers.vms import devices

POOLS = {
    "default": ("dir", "/var/lib/libvirt/images"),
    "nfs-shared": ("netfs", "/mnt/nfs-shared/"),
    "stopped": ("dir", "/srv/stopped"),
    "lun": ("iscsi", "/dev/disk/by-path"),
}


class FakePool:
    def __init__(self, name, known=()):
        self._name = name
        self.known = set(known)  # volumes in libvirt's cache
        self.on_disk = set()  # volumes a refresh would discover
        self.refreshed = 0

    def name(self):
        return self._name

    def XMLDesc(self, *_):
        kind, target = POOLS[self._name]
        return f"<pool type='{kind}'><name>{self._name}</name><target><path>{target}</path></target></pool>"

    def isActive(self):
        return self._name != "stopped"

    def refresh(self, flags):
        if not self.isActive():
            raise libvirt.libvirtError("pool is not active")
        self.refreshed += 1
        self.known |= self.on_disk

    def storageVolLookupByName(self, name):
        if name not in self.known:
            raise libvirt.libvirtError("no volume")
        return name


class StaleCacheConn:
    """libvirt as it is right after Hyperlite wrote a disk itself: the file exists, the cache does not know it."""

    def __init__(self):
        self.pools = {name: FakePool(name) for name in POOLS}

    def listAllStoragePools(self, flags):
        return list(self.pools.values())

    def storageVolLookupByPath(self, path):
        raise libvirt.libvirtError("Storage volume not found: no storage vol with matching path")

    def close(self):
        pass


def test_a_disk_written_outside_libvirt_is_found_in_its_pool():
    conn = StaleCacheConn()
    assert pool_for_path(conn, "/var/lib/libvirt/images/vm1.qcow2").name() == "default"
    # A trailing slash in the pool definition does not matter.
    assert pool_for_path(conn, "/mnt/nfs-shared/vm2.qcow2").name() == "nfs-shared"


def test_a_file_outside_every_pool_or_in_a_subdirectory_is_in_no_pool():
    conn = StaleCacheConn()
    assert pool_for_path(conn, "/root/elsewhere/vm1.qcow2") is None
    assert pool_for_path(conn, "/var/lib/libvirt/images/sub/vm1.qcow2") is None
    assert pool_for_path(conn, None) is None


def test_a_block_device_is_never_matched_by_directory():
    # iSCSI pools all share /dev/disk/by-path: only libvirt's own lookup can tell which one a LUN belongs to.
    assert pool_for_path(StaleCacheConn(), "/dev/disk/by-path/ip-10.0.0.1:3260-iscsi-iqn-lun-1") is None


def test_lookup_volume_refreshes_the_pool_once_on_a_miss():
    pool = FakePool("default")
    pool.on_disk.add("vm1.qcow2")
    assert lookup_volume(pool, "vm1.qcow2") == "vm1.qcow2"
    assert pool.refreshed == 1
    with pytest.raises(libvirt.libvirtError):
        lookup_volume(pool, "missing.qcow2")


def test_refresh_pools_for_paths_refreshes_each_active_pool_once():
    conn = StaleCacheConn()
    refresh_pools_for_paths(
        conn,
        [
            "/var/lib/libvirt/images/vm1.qcow2",
            ("/var/lib/libvirt/images/vm1-2.qcow2", "file"),
            None,
            "/srv/stopped/x.qcow2",
            "/nowhere/y.qcow2",
        ],
    )
    assert conn.pools["default"].refreshed == 1
    assert conn.pools["stopped"].refreshed == 0


class FakeDomain:
    def __init__(self, path):
        self._xml = (
            "<domain><name>vm1</name><devices><disk type='file' device='disk'>"
            f"<driver name='qemu' type='qcow2'/><source file='{path}'/><target dev='vda' bus='virtio'/>"
            "</disk></devices></domain>"
        )

    def name(self):
        return "vm1"

    def XMLDesc(self, *_):
        return self._xml

    def isActive(self):
        return False

    def snapshotNum(self, flags):
        return 0

    def blockInfo(self, dev):
        return (10 * 1024**3, 0, 0)


def test_a_disk_created_by_hyperlite_can_be_moved_without_a_pool_refresh():
    conn = StaleCacheConn()
    how = disk_move.plan(conn, FakeDomain("/var/lib/libvirt/images/vm1.qcow2"), "vda", "nfs-shared")
    assert how["source_pool"].name() == "default"
    assert how["dest"] == "/mnt/nfs-shared/vm1.qcow2"


def test_the_disk_list_reports_the_pool_of_a_disk_created_by_hyperlite():
    class Domain(FakeDomain):
        def blockInfo(self, dev):
            raise libvirt.libvirtError("no block info")

    source = "/mnt/nfs-shared/vm1.qcow2"
    out = devices._disk_size(Domain(source), StaleCacheConn(), "vda", source)
    assert out["pool"] == "nfs-shared"
