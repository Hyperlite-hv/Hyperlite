"""A snapshot or a pool deleted while its list is built is skipped: the list is still returned."""

import libvirt


def _gone(code):
    e = libvirt.libvirtError("gone")
    e.get_error_code = lambda: code
    return e


class _Snap:
    def __init__(self, name, gone=False):
        self.name, self.gone = name, gone

    def getXMLDesc(self, flags=0):
        if self.gone:
            raise _gone(libvirt.VIR_ERR_NO_DOMAIN_SNAPSHOT)
        return f"<domainsnapshot><name>{self.name}</name><state>running</state></domainsnapshot>"

    def getName(self):
        return self.name

    def getParent(self):
        raise _gone(libvirt.VIR_ERR_NO_DOMAIN_SNAPSHOT)

    def isCurrent(self, flags=0):
        return False


class _Dom:
    def listAllSnapshots(self, flags=0):
        return [_Snap("kept"), _Snap("before-upgrade", gone=True)]

    def XMLDesc(self, flags=0):
        return "<domain><name>vm1</name><devices/></domain>"

    def name(self):
        return "vm1"


class _Pool:
    def __init__(self, name, gone=False):
        self._name, self.gone = name, gone

    def info(self):
        if self.gone:
            raise _gone(libvirt.VIR_ERR_NO_STORAGE_POOL)
        return [2, 10 * 1024**3, 1024**3, 9 * 1024**3]

    def XMLDesc(self, flags=0):
        return f"<pool type='dir'><name>{self._name}</name><target><path>/p</path></target></pool>"

    def name(self):
        return self._name

    def UUIDString(self):
        return "u"

    def autostart(self):
        return 1


class _Conn:
    def lookupByName(self, name):
        return _Dom()

    def listAllStoragePools(self, flags=0):
        return [_Pool("default"), _Pool("e2e-pool-517256", gone=True)]

    def storagePoolLookupByName(self, name):
        return _Pool(name)

    def close(self):
        pass


def test_a_vanished_snapshot_does_not_break_the_list(database, client, auth_headers, monkeypatch):
    from app.routers.vms import snapshots

    monkeypatch.setattr(snapshots, "open_conn", lambda node=None: _Conn())
    monkeypatch.setattr(snapshots, "_zvol_disks_of_domain", lambda domain: [], raising=False)
    r = client.get("/vms/vm1/snapshots", headers=auth_headers("admin"))
    assert r.status_code == 200, r.text
    assert [s["nom"] for s in r.json()] == ["kept"]


def test_a_vanished_pool_does_not_break_the_list(database, client, auth_headers, monkeypatch):
    from app.routers import storage

    monkeypatch.setattr(storage, "open_conn", lambda node=None: _Conn())
    monkeypatch.setattr(storage, "ensure_default_pool", lambda conn: None)
    monkeypatch.setattr(storage.zfs_storage, "list_pools", lambda: [])
    r = client.get("/storage", headers=auth_headers("admin"))
    assert r.status_code == 200, r.text
    assert [p["nom"] for p in r.json()] == ["default"]
