"""The base image Hyperlite copies into a storage pool for the VMs built on it (vm_builder.ensure_pool_base_image) is
its own copy: a pool the user has emptied is removed, the base image with it on a pool of this node, left on a shared
one (NFS, SMB) where the other nodes' VMs may be built on it. Found by the Terraform provider's acceptance tests: a
VM made on an NFS pool, deleted, left the pool impossible to remove ("still contains 1 file")."""

import pytest

from app.core.vm_builder import POOL_BASE_NAME
from app.routers import storage
from tests.test_shared_pools import _Host, _Pool

DIR = "<pool type='dir'><name>nas</name><target><path>/srv/fast</path></target></pool>"
NFS = (
    "<pool type='netfs'><name>nas</name><source><host name='nas.lan'/><dir path='/export'/>"
    "<format type='nfs'/></source><target><path>/var/lib/libvirt/hyperlite-pools/nas</path></target></pool>"
)


class _Volume:
    def __init__(self, pool, name):
        self.pool, self._name = pool, name

    def name(self):
        return self._name

    def path(self):
        return f"/pool/{self._name}"

    def delete(self, flags=0):
        self.pool.volumes.remove(self)


class _PoolWithFiles(_Pool):
    def __init__(self, xml, names):
        super().__init__(xml)
        self.volumes = [_Volume(self, n) for n in names]

    def listAllVolumes(self):
        return list(self.volumes)


@pytest.fixture()
def host(database, monkeypatch, daemon_libvirt):
    host = _Host()
    monkeypatch.setattr(storage, "open_conn", lambda node=None, **_k: host)
    daemon_libvirt(host)  # this node's pools are hyperlite-daemon's, in this process in the tests
    return host


def _pool(host, xml, *names):
    pool = _PoolWithFiles(xml, names)
    pool.host, pool.active = host, True
    host.pools["nas"] = pool
    return pool


def test_a_pool_of_this_node_left_with_its_base_image_only_is_removed_with_it(client, auth_headers, host):
    pool = _pool(host, DIR, POOL_BASE_NAME)
    assert client.delete("/storage/nas?confirm=true", headers=auth_headers("root")).status_code == 200
    assert "nas" not in host.pools and pool.volumes == []


def test_a_shared_pool_is_removed_and_its_base_image_left_for_the_other_nodes(client, auth_headers, host):
    pool = _pool(host, NFS, POOL_BASE_NAME)
    assert client.delete("/storage/nas?confirm=true", headers=auth_headers("root")).status_code == 200
    assert "nas" not in host.pools and [v.name() for v in pool.volumes] == [POOL_BASE_NAME]


def test_a_file_of_the_users_still_keeps_the_pool(client, auth_headers, host):
    pool = _pool(host, DIR, POOL_BASE_NAME, "data.qcow2")
    r = client.delete("/storage/nas?confirm=true", headers=auth_headers("root"))
    assert r.status_code == 409 and "still contains 1 file" in r.json()["detail"]
    assert "nas" in host.pools and len(pool.volumes) == 2
