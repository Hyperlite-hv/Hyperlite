"""Shared storage declared for several nodes at once (app/core/shared_pools.py), as on Proxmox: created on each
chosen node, on nodes registered later when declared for every node, and removed from all of them together. Each
node's libvirt is a fake that records what was defined there."""

import json

import libvirt
import pytest


def _missing():
    e = libvirt.libvirtError("no pool")
    e.get_error_code = lambda: libvirt.VIR_ERR_NO_STORAGE_POOL
    return e


class _Pool:
    def __init__(self, xml):
        self.xml, self.active = xml, False

    def XMLDesc(self, flags=0):
        return self.xml

    def build(self, flags=0):
        pass

    def create(self, flags=0):
        self.active = True

    def setAutostart(self, v):
        pass

    def info(self):
        return [2, 100 * 1024**3, 10 * 1024**3, 90 * 1024**3]

    def name(self):
        return "nas"

    def UUIDString(self):
        return "u"

    def autostart(self):
        return 1

    def isActive(self):
        return self.active

    def refresh(self, flags=0):
        pass

    def listAllVolumes(self):
        return []

    def connect(self):
        return self.host

    def destroy(self):
        self.active = False

    def undefine(self):
        self.host.pools.pop("nas", None)


class _Host:
    """One node's libvirt: its pools, and whether it can reach the storage."""

    def __init__(self, reachable=True):
        self.pools, self.reachable, self.secrets = {}, reachable, []

    def storagePoolLookupByName(self, name):
        if name in self.pools:
            return self.pools[name]
        raise _missing()

    def storagePoolDefineXML(self, xml):
        pool = _Pool(xml)
        pool.host = self
        self.pools["nas"] = pool
        if not self.reachable:
            pool.create = self._refuse
        return pool

    def _refuse(self, flags=0):
        raise libvirt.libvirtError("mount.nfs: access denied by server")

    def secretDefineXML(self, xml):
        host = self

        class _Secret:
            def setValue(self, value):
                host.secrets.append(value)

        return _Secret()

    def secretLookupByUsage(self, usage_type, usage):
        raise libvirt.libvirtError("no secret")

    def listAllDomains(self):
        return []

    def close(self):
        pass


@pytest.fixture()
def cluster_of_three(database, monkeypatch):
    from app.routers import storage

    with database.get_conn() as db:
        for name in ("n2", "n3"):
            db.execute("INSERT INTO nodes (name, hostname, added_at) VALUES (?, ?, 'now')", (name, f"{name}.lan"))
        db.commit()
    hosts = {"local": _Host(), "n2": _Host(), "n3": _Host(reachable=False)}
    monkeypatch.setattr(storage, "open_conn", lambda node=None: hosts[node or "local"])
    monkeypatch.setattr(storage.shutil, "which", lambda name: "/sbin/mount.nfs")
    monkeypatch.setattr(storage.iscsi, "initiator_available", lambda: True)
    monkeypatch.setattr(storage.nfs_permissions, "check", lambda path, export=None: {"ok": True, "message": None})
    return hosts


NFS = {"name": "nas", "type": "netfs", "nfs_host": "192.168.1.10", "nfs_export_path": "/srv/vms"}


def test_every_node_gets_the_pool_and_a_failing_one_is_reported(client, auth_headers, cluster_of_three):
    r = client.post("/storage", json={**NFS, "tous_les_noeuds": True}, headers=auth_headers("alice"))
    assert r.status_code == 201
    states = {x["noeud"]: x["etat"] for x in r.json()["resultats"]}
    assert states == {"local": "cree", "n2": "cree", "n3": "echec"}
    assert "access denied" in next(x for x in r.json()["resultats"] if x["noeud"] == "n3")["detail"]
    assert "nas" in cluster_of_three["local"].pools and "nas" in cluster_of_three["n2"].pools
    assert "nas" not in cluster_of_three["n3"].pools  # the ghost definition is removed
    shared = client.get("/storage/shared", headers=auth_headers("bob")).json()
    assert shared == [{"nom": "nas", "type": "netfs", "tous_les_noeuds": True, "noeuds": ["local", "n2"]}]


def test_a_pool_already_there_is_kept_and_a_choice_of_nodes_is_honoured(client, auth_headers, cluster_of_three):
    headers = auth_headers("alice")
    client.post("/storage?node=n2", json=NFS, headers=headers)
    r = client.post("/storage", json={**NFS, "noeuds": ["local", "n2"]}, headers=headers)
    assert {x["noeud"]: x["etat"] for x in r.json()["resultats"]} == {"local": "cree", "n2": "existe"}
    assert client.get("/storage/shared", headers=headers).json()[0]["tous_les_noeuds"] is False
    assert client.post("/storage", json={**NFS, "noeuds": ["n9"]}, headers=headers).status_code == 404


def test_only_shared_storage_goes_on_several_nodes(client, auth_headers, cluster_of_three):
    r = client.post(
        "/storage", json={"name": "dirs", "type": "dir", "tous_les_noeuds": True}, headers=auth_headers("a")
    )
    assert r.status_code == 422 and "NFS or iSCSI" in r.json()["detail"]


def test_nowhere_is_an_error_not_a_saved_definition(client, auth_headers, cluster_of_three):
    r = client.post("/storage", json={**NFS, "noeuds": ["n3"]}, headers=auth_headers("alice"))
    assert r.status_code == 502 and "n3:" in r.json()["detail"]
    assert client.get("/storage/shared", headers=auth_headers("bob")).json() == []


def test_a_node_registered_later_gets_the_pools_declared_for_every_node(
    client, auth_headers, cluster_of_three, database
):
    from app.core import shared_pools
    from app.routers import storage

    headers = auth_headers("alice")
    client.post(
        "/storage",
        json={
            "name": "nas",
            "type": "iscsi",
            "iscsi_host": "192.168.1.20",
            "iscsi_target": "iqn.2005-10.org.freenas.ctl:vms",
            "chap_user": "hyper",
            "chap_password": "s3cret",
            "tous_les_noeuds": True,
        },
        headers=headers,
    )
    with database.get_conn() as db:
        stored = json.loads(db.execute("SELECT definition FROM shared_pools").fetchone()[0])
    assert stored["chap_password"] != "s3cret"  # encrypted at rest
    assert "chap_password" not in json.dumps(client.get("/storage/shared", headers=headers).json())

    cluster_of_three["n4"] = _Host()
    with database.get_conn() as db:
        db.execute("INSERT INTO nodes (name, hostname, added_at) VALUES ('n4', 'n4.lan', 'now')")
        db.commit()
    result = storage.apply_shared_pools("n4", "alice")
    assert [(r["nom"], r["etat"]) for r in result] == [("nas", "cree")]
    assert cluster_of_three["n4"].secrets == [b"s3cret"]  # the CHAP password reached the new node
    assert "n4" in shared_pools.get("nas")["noeuds"]


def test_a_shared_pool_is_removed_from_every_node_with_its_definition(client, auth_headers, cluster_of_three):
    headers = auth_headers("alice")
    client.post("/storage", json={**NFS, "tous_les_noeuds": True}, headers=headers)
    r = client.delete("/storage/nas?confirm=true&partout=true", headers=headers)
    assert r.status_code == 200 and {x["noeud"] for x in r.json()["resultats"]} == {"local", "n2"}
    assert all("nas" not in h.pools for h in cluster_of_three.values())
    assert client.get("/storage/shared", headers=headers).json() == []


def test_a_renamed_node_stays_in_the_shared_pool(database):
    from app.core import renaming, shared_pools

    with database.get_conn() as db:
        db.execute("INSERT INTO nodes (name, hostname, added_at) VALUES ('n2', 'n2.lan', 'now')")
        db.commit()
    shared_pools.save({**NFS, "nfs_version": "4.2"}, True, ["local", "n2"], "alice")
    renaming.node_records("n2", "rack2")
    assert shared_pools.get("nas")["noeuds"] == ["local", "rack2"]
