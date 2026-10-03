"""A "dir" pool lists every file of its directory as a volume, and a volume can be deleted from the dashboard: a pool
on a system directory is refused (one on /etc was accepted on a real host, offering /etc/passwd for deletion), and so
is a directory another pool already covers (libvirt's own refusal surfaced as a 500)."""

import pytest

from app.routers import storage


class _Pool:
    def __init__(self, name, path, kind="dir"):
        self._name, self.path, self.kind = name, path, kind
        self.built = self.started = False

    def name(self):
        return self._name

    def XMLDesc(self, flags=0):
        return f"<pool type='{self.kind}'><name>{self._name}</name><target><path>{self.path}</path></target></pool>"

    def build(self, flags=0):
        self.built = True

    def create(self, flags=0):
        self.started = True

    def setAutostart(self, value):
        pass

    def isActive(self):
        return 1 if self.started else 0

    def info(self):
        return [2 if self.started else 0, 0, 0, 0]

    def autostart(self):
        return 1

    def UUIDString(self):
        return "u"


class _Conn:
    def __init__(self):
        self.pools = {
            "default": _Pool("default", "/var/lib/libvirt/images"),
            "nas": _Pool("nas", "/var/lib/libvirt/hyperlite-pools/nas", "netfs"),
        }

    def storagePoolLookupByName(self, name):
        import libvirt

        if name not in self.pools:
            raise libvirt.libvirtError("no pool")
        return self.pools[name]

    def listAllStoragePools(self, flags=0):
        return list(self.pools.values())

    def storagePoolDefineXML(self, xml):
        import re

        pool = _Pool(re.search(r"<name>(.*?)</name>", xml).group(1), re.search(r"<path>(.*?)</path>", xml).group(1))
        self.pools[pool.name()] = pool
        return pool

    def close(self):
        pass


@pytest.fixture()
def conn(database, monkeypatch):
    c = _Conn()
    monkeypatch.setattr(storage, "open_conn", lambda node=None: c)
    return c


@pytest.mark.parametrize(
    "path",
    ["/", "/etc", "/etc/", "//etc", "/etc/ssl", "/usr/lib", "/boot", "/root", "/var", "/var/lib/hyperlite", "/home",
     "/var/lib/libvirt", "/tmp/vms", "/srv/../etc", "/srv/./pool"],  # noqa: S108
)  # fmt: skip
def test_system_directories_are_refused(conn, client, auth_headers, path):
    r = client.post("/storage", json={"name": "lab", "type": "dir", "path": path}, headers=auth_headers("admin"))
    assert r.status_code == 422, r.text
    assert "lab" not in conn.pools


@pytest.mark.parametrize(
    ("path", "owner"),
    [
        ("/var/lib/libvirt/images", "default"),
        ("/var/lib/libvirt/images/", "default"),
        ("/var/lib/libvirt/images/sub", "default"),
        ("/var/lib/libvirt/hyperlite-pools", "nas"),
    ],
)
def test_a_directory_another_pool_covers_is_refused(conn, client, auth_headers, path, owner):
    r = client.post("/storage", json={"name": "lab", "type": "dir", "path": path}, headers=auth_headers("admin"))
    assert r.status_code == 409 and owner in r.json()["detail"]
    assert "lab" not in conn.pools


@pytest.mark.parametrize("path", [None, "/srv/vms", "/home/vms", "/mnt/disk2", "/data/pool"])
def test_ordinary_directories_are_accepted(conn, client, auth_headers, path):
    body = {"name": "lab", "type": "dir", **({"path": path} if path else {})}
    r = client.post("/storage", json=body, headers=auth_headers("admin"))
    assert r.status_code == 201, r.text
    assert conn.pools["lab"].started
