"""Renaming the objects that could not be renamed yet: storage pools (one node or every node of a shared pool),
networks, VM pools, groups, custom roles, automation jobs, API tokens, templates and ISO images. libvirt is replaced
by fakes that keep what is defined."""

import json
import xml.etree.ElementTree as ET

import libvirt
import pytest


def _gone():
    e = libvirt.libvirtError("not found")
    e.get_error_code = lambda: libvirt.VIR_ERR_NO_STORAGE_POOL
    return e


class _Dom:
    def __init__(self, name, xml, active=False):
        self._name, self.xml, self.active = name, xml, active

    def name(self):
        return self._name

    def XMLDesc(self, flags=0):
        return self.xml

    def isActive(self):
        return self.active


class _Pool:
    def __init__(self, host, xml, active=True):
        self.host, self.xml, self.active, self.auto = host, xml, active, True

    def name(self):
        return ET.fromstring(self.xml).findtext("name")

    def XMLDesc(self, flags=0):
        return self.xml

    def isActive(self):
        return self.active

    def autostart(self):
        return self.auto

    def setAutostart(self, v):
        self.auto = bool(v)

    def destroy(self):
        self.active = False

    def create(self, flags=0):
        self.active = True

    def undefine(self):
        self.host.pools.pop(self.name(), None)

    def refresh(self, flags=0):
        pass

    def listAllVolumes(self):
        return []

    def info(self):
        return [2, 10 * 1024**3, 1024**3, 9 * 1024**3]

    def UUIDString(self):
        return "u"

    def connect(self):
        return self.host


class _Host:
    def __init__(self):
        self.pools, self.domains = {}, []

    def add_pool(self, xml, active=True):
        pool = _Pool(self, xml, active)
        self.pools[pool.name()] = pool
        return pool

    def storagePoolLookupByName(self, name):
        if name in self.pools:
            return self.pools[name]
        raise _gone()

    def storagePoolDefineXML(self, xml):
        return self.add_pool(xml, active=False)

    def listAllDomains(self, flags=0):
        if flags & libvirt.VIR_CONNECT_LIST_DOMAINS_ACTIVE:
            return [d for d in self.domains if d.active]
        return list(self.domains)

    def close(self):
        pass


DIR_POOL = "<pool type='dir'><name>fast</name><uuid>1</uuid><target><path>/data/fast</path></target></pool>"


@pytest.fixture()
def hosts(database, monkeypatch):
    from app.routers import storage

    hosts = {"local": _Host()}
    monkeypatch.setattr(storage, "open_conn", lambda node=None: hosts[node or "local"])
    monkeypatch.setattr(storage.zfs_storage, "pool_exists", lambda name: name == "tank")
    return hosts


def test_a_storage_pool_is_redefined_under_its_new_name_with_the_same_path(client, auth_headers, hosts, database):
    from app.core.container_meta import get_container_storage, set_container_storage

    hosts["local"].add_pool(DIR_POOL)
    set_container_storage("web", "fast", "/data/fast/hyperlite-containers")
    with database.get_conn() as db:
        db.execute("INSERT INTO storage_samples (ts, tier, node, pool) VALUES ('t', 'raw', 'local', 'fast')")
        db.commit()
    headers = auth_headers("alice")
    r = client.post("/storage/fast/rename", json={"new_name": "ssd"}, headers=headers)
    assert r.status_code == 200 and r.json()["nom"] == "ssd"
    pool = hosts["local"].pools["ssd"]
    assert "fast" not in hosts["local"].pools and pool.isActive() and pool.autostart()
    root = ET.fromstring(pool.xml)
    assert root.findtext("target/path") == "/data/fast" and root.find("uuid") is None  # the files do not move
    assert get_container_storage("web")["pool"] == "ssd"
    with database.get_conn() as db:
        assert db.execute("SELECT pool FROM storage_samples").fetchone()[0] == "ssd"


def test_a_pool_a_running_vm_uses_keeps_its_name(client, auth_headers, hosts):
    hosts["local"].add_pool(DIR_POOL)
    disk = "<domain><devices><disk><source file='/data/fast/web.qcow2'/></disk></devices></domain>"
    hosts["local"].domains.append(_Dom("web", disk, active=True))
    r = client.post("/storage/fast/rename", json={"new_name": "ssd"}, headers=auth_headers("alice"))
    assert r.status_code == 409 and "web" in r.json()["detail"]
    assert "fast" in hosts["local"].pools


@pytest.mark.parametrize(
    ("pool", "new", "code"), [("default", "x-pool", 400), ("tank", "fast2", 409), ("fast", "bad name", 422)]
)
def test_pools_that_keep_their_name(client, auth_headers, hosts, pool, new, code):
    hosts["local"].add_pool(DIR_POOL)
    assert (
        client.post(f"/storage/{pool}/rename", json={"new_name": new}, headers=auth_headers("alice")).status_code
        == code
    )


def test_a_renamed_iscsi_pool_keeps_its_chap_secret_and_deleting_it_removes_that_secret(hosts):
    from app.routers import storage

    xml = (
        "<pool type='iscsi'><name>san</name><source><host name='nas'/><device path='iqn.2005-10.org.x:vms'/>"
        "<auth type='chap' username='u'><secret usage='hyperlite-iscsi-san'/></auth></source>"
        "<target><path>/dev/disk/by-path</path></target></pool>"
    )
    hosts["local"].add_pool(xml)
    storage._rename_on("san", "san2", None, {"username": "alice"})
    renamed = ET.fromstring(hosts["local"].pools["san2"].xml)
    assert storage._chap_usage(renamed) == "hyperlite-iscsi-san"  # the secret is private: it cannot be copied


def test_a_shared_pool_is_renamed_on_every_node_and_its_definition_follows(client, auth_headers, hosts, database):
    from app.core import shared_pools

    with database.get_conn() as db:
        db.execute("INSERT INTO nodes (name, hostname, added_at) VALUES ('n2', 'n2.lan', 'now')")
        db.commit()
    hosts["n2"] = _Host()
    nfs = "<pool type='netfs'><name>nas</name><target><path>/var/lib/libvirt/hyperlite-pools/nas</path></target></pool>"
    hosts["local"].add_pool(nfs)
    hosts["n2"].add_pool(nfs)
    shared_pools.save(
        {"name": "nas", "type": "netfs", "nfs_host": "h", "nfs_export_path": "/e"}, True, ["local", "n2"], "a"
    )
    r = client.post("/storage/nas/rename", json={"new_name": "nas2", "partout": True}, headers=auth_headers("alice"))
    assert r.status_code == 200 and {x["noeud"]: x["etat"] for x in r.json()["resultats"]} == {
        "local": "renomme",
        "n2": "renomme",
    }
    assert "nas2" in hosts["n2"].pools and shared_pools.get("nas") is None
    assert shared_pools.get("nas2")["definition"]["name"] == "nas2"


# ---- Networks ----


class _Net:
    def __init__(self, host, xml, active=True):
        self.host, self.xml, self.active, self.auto = host, xml, active, True

    def name(self):
        return ET.fromstring(self.xml).findtext("name")

    def XMLDesc(self, flags=0):
        return self.xml

    def isActive(self):
        return self.active

    def autostart(self):
        return self.auto

    def setAutostart(self, v):
        self.auto = bool(v)

    def destroy(self):
        self.active = False

    def create(self):
        self.active = True

    def undefine(self):
        self.host.nets.pop(self.name(), None)

    def bridgeName(self):
        return "virbr7"


class _NetHost:
    def __init__(self):
        self.nets, self.domains, self.defined = {}, [], []

    def networkLookupByName(self, name):
        if name in self.nets:
            return self.nets[name]
        raise libvirt.libvirtError("no network")

    def networkDefineXML(self, xml):
        net = _Net(self, xml, active=False)
        self.nets[net.name()] = net
        return net

    def listAllDomains(self, flags=0):
        return list(self.domains)

    def defineXML(self, xml):
        self.defined.append(xml)

    def listAllNetworks(self, flags=0):
        return list(self.nets.values())

    def close(self):
        pass


def _guest(name, net, active):
    return _Dom(
        name,
        f"<domain><name>{name}</name><devices><interface type='network'><source network='{net}'/></interface></devices></domain>",
        active,
    )


@pytest.fixture()
def nets(database, monkeypatch):
    from app.routers import network

    qemu, lxc = _NetHost(), _NetHost()
    qemu.nets["lab"] = _Net(qemu, "<network><name>lab</name><bridge name='virbr7'/><ip address='10.9.0.1'/></network>")
    monkeypatch.setattr(network, "open_conn", lambda node=None: qemu)
    monkeypatch.setattr(network, "open_lxc_conn", lambda: lxc)
    monkeypatch.setattr(network, "_network_summary", lambda net: {"nom": net.name()})
    monkeypatch.setattr(network, "remove_network_firewall", lambda conn, name: None)
    applied = []
    monkeypatch.setattr(network, "apply_network_firewall", lambda conn, name, config: applied.append((name, config)))
    return qemu, lxc, applied


def test_a_network_is_renamed_and_its_stopped_guests_follow(client, auth_headers, nets, database):
    from app.core.container_meta import get_container_app, set_container_app

    qemu, lxc, applied = nets
    qemu.domains.append(_guest("web", "lab", active=False))
    lxc.domains.append(_guest("pg", "lab", active=False))
    set_container_app("pg", "postgres:16", {"args": ["postgres"]}, "10.9.0.20", "lab")
    with database.get_conn() as db:
        db.execute(
            "INSERT INTO network_firewall (network_name, default_policy, rules_json) VALUES ('lab', 'drop', '[]')"
        )
        db.commit()
    r = client.post("/networks/lab/rename", json={"new_name": "labo"}, headers=auth_headers("alice"))
    assert r.status_code == 200 and r.json()["invites_mis_a_jour"] == ["pg", "web"]
    assert "labo" in qemu.nets and "lab" not in qemu.nets and qemu.nets["labo"].isActive()
    assert '<source network="labo" />' in qemu.defined[0] and '<source network="labo" />' in lxc.defined[0]
    assert get_container_app("pg")["network"] == "labo"
    assert applied == [("labo", {"default_policy": "drop", "rules": []})]


def test_a_network_with_a_running_guest_or_a_system_network_keeps_its_name(client, auth_headers, nets):
    qemu, _lxc, _applied = nets
    qemu.domains.append(_guest("web", "lab", active=True))
    headers = auth_headers("alice")
    r = client.post("/networks/lab/rename", json={"new_name": "labo"}, headers=headers)
    assert r.status_code == 409 and "web" in r.json()["detail"]
    assert client.post("/networks/default/rename", json={"new_name": "x-net"}, headers=headers).status_code == 403


# ---- Objects known by an id ----


def test_labels_are_renamed_unique_and_for_administrators(client, auth_headers, database):
    admin = auth_headers("alice")
    pool = client.post("/pools", json={"name": "prod"}, headers=admin).json()["id"]
    client.post("/pools", json={"name": "lab"}, headers=admin)
    group = client.post("/groups", json={"name": "ops"}, headers=admin).json()["id"]
    role = client.post("/acl/custom-roles", json={"name": "viewer", "privileges": ["vm.view"]}, headers=admin).json()[
        "id"
    ]
    with database.get_conn() as db:
        job = db.execute("INSERT INTO jobs (name, created_at) VALUES ('nightly', 'now')").lastrowid
        db.commit()
    assert client.patch(f"/pools/{pool}", json={"name": "production"}, headers=admin).json()["name"] == "production"
    assert client.patch(f"/pools/{pool}", json={"name": "lab"}, headers=admin).status_code == 409
    assert client.patch(f"/groups/{group}", json={"name": "operations"}, headers=admin).status_code == 200
    assert client.patch(f"/acl/custom-roles/{role}", json={"name": "readers"}, headers=admin).status_code == 200
    assert client.patch(f"/jobs/{job}", json={"name": "every night"}, headers=admin).status_code == 200
    assert client.patch("/pools/999", json={"name": "x"}, headers=admin).status_code == 404
    viewer = auth_headers("olga", role="observateur")
    assert client.patch(f"/groups/{group}", json={"name": "x"}, headers=viewer).status_code == 403
    with database.get_conn() as db:
        names = [
            db.execute("SELECT name FROM groups").fetchone()[0],
            db.execute("SELECT name FROM custom_roles").fetchone()[0],
            db.execute("SELECT name FROM jobs").fetchone()[0],
        ]
    assert names == ["operations", "readers", "every night"]


def test_an_api_token_is_renamed_by_its_owner_only(client, auth_headers):
    alice, bob = auth_headers("alice"), auth_headers("bob")
    token = client.post("/auth/tokens", json={"name": "ci"}, headers=alice).json()["id"]
    assert client.patch(f"/auth/tokens/{token}", json={"name": "x"}, headers=bob).status_code == 404
    assert client.patch(f"/auth/tokens/{token}", json={"name": "gitlab-ci"}, headers=alice).status_code == 200
    assert client.get("/auth/tokens", headers=alice).json()[0]["name"] == "gitlab-ci"


# ---- Files ----


def test_a_template_takes_its_new_name(client, auth_headers, tmp_path, monkeypatch):
    from app.core import templates_store

    monkeypatch.setattr(templates_store, "TEMPLATES_DIR", tmp_path)
    for ext, content in (("json", json.dumps({"nom": "deb"})), ("xml", "<domain/>"), ("qcow2", "disk")):
        (tmp_path / f"deb.{ext}").write_text(content)
    headers = auth_headers("alice")
    r = client.post("/templates/deb/rename", json={"new_name": "debian-13"}, headers=headers)
    assert r.status_code == 200 and r.json()["nom"] == "debian-13"
    assert (tmp_path / "debian-13.qcow2").read_text() == "disk" and not (tmp_path / "deb.json").exists()
    assert client.post("/templates/nope/rename", json={"new_name": "x-y"}, headers=headers).status_code == 404


def test_an_iso_is_renamed_unless_a_vm_has_it_in_its_drive(client, auth_headers, tmp_path, monkeypatch):
    from app.core import iso_share
    from app.routers import isos

    monkeypatch.setattr(iso_share, "isos_dir", lambda: tmp_path)
    monkeypatch.setattr(isos, "_refresh_iso_pool", lambda path: None)
    host = _Host()
    monkeypatch.setattr(isos, "open_conn", lambda node=None: host)
    (tmp_path / "debian.iso").write_text("iso")
    (tmp_path / "used.iso").write_text("iso")
    host.domains.append(
        _Dom(
            "web",
            f"<domain><devices><disk device='cdrom'><source file='{tmp_path}/used.iso'/></disk></devices></domain>",
        )
    )
    headers = auth_headers("alice")
    r = client.post("/isos/debian.iso/rename", json={"new_name": "debian-13.1-netinst"}, headers=headers)
    assert r.status_code == 200 and r.json()["nom"] == "debian-13.1-netinst.iso"
    assert (tmp_path / "debian-13.1-netinst.iso").exists()
    assert client.post("/isos/used.iso/rename", json={"new_name": "other"}, headers=headers).status_code == 409
    assert client.post("/isos/used.iso/rename", json={"new_name": "../x"}, headers=headers).status_code in (409, 422)
