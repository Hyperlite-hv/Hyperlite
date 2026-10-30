"""The 'default' storage pool is made when a request uses it, not only when someone lists the pools: on a fresh node a
volume created or a disk attached there failed with "Storage pool 'default' not found" (seen by the Terraform
provider's acceptance tests on a new node)."""

import libvirt

from app.core import libvirt_utils


class Pool:
    def __init__(self):
        self.started = False

    def build(self):
        pass

    def isActive(self):
        return self.started

    def create(self):
        self.started = True

    def setAutostart(self, on):
        pass


class Volume:
    def name(self):
        return "data.qcow2"

    def path(self):
        return "/var/lib/libvirt/images/data.qcow2"

    def info(self):
        return [0, 1024**3, 0]


class Conn:
    """A libvirt connection without any storage pool until one is defined."""

    def __init__(self):
        self.pools = {}

    def storagePoolLookupByName(self, name):
        if name not in self.pools:
            raise libvirt.libvirtError("Storage pool not found")
        return self.pools[name]

    def storagePoolDefineXML(self, xml):
        self.pools["default"] = Pool()
        return self.pools["default"]


def test_the_default_pool_is_made_on_its_first_use():
    conn = Conn()
    pool = libvirt_utils.lookup_pool(conn, "default")
    assert pool is conn.pools["default"] and pool.isActive()
    assert libvirt_utils.lookup_pool(conn, "default") is pool  # made once


def test_another_pool_is_only_looked_up():
    conn = Conn()
    try:
        libvirt_utils.lookup_pool(conn, "fast")
    except libvirt.libvirtError:
        pass
    else:
        raise AssertionError("a pool that does not exist was found")
    assert conn.pools == {}


def test_a_volume_is_created_in_the_default_pool_of_a_fresh_node(
    client, auth_headers, monkeypatch, tmp_path, daemon_libvirt
):
    from app.routers import storage

    conn = Conn()
    made = []

    def define(xml):
        pool = Pool()
        pool.name = lambda: "default"
        pool.storageVolLookupByName = lambda name: (_ for _ in ()).throw(libvirt.libvirtError("no volume"))
        pool.refresh = lambda flags=0: None
        pool.XMLDesc = lambda flags=0: (
            "<pool type='dir'><name>default</name><target><path>/var/lib/libvirt/images</path></target></pool>"
        )
        pool.createXML = lambda xml, flags=0: made.append(xml) or Volume()
        conn.pools["default"] = pool
        return pool

    conn.storagePoolDefineXML = define
    conn.close = lambda: None
    monkeypatch.setattr(storage, "open_conn", lambda *a, **k: conn)
    daemon_libvirt(conn)  # hyperlite-daemon makes the pool and the volume, in this process in the tests
    monkeypatch.setattr(storage.zfs_storage, "pool_exists", lambda name: False)
    r = client.post("/storage/default/volumes", json={"name": "data", "size_gb": 1}, headers=auth_headers("root"))
    assert r.status_code == 201, r.text
    assert "default" in conn.pools and len(made) == 1
