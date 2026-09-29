"""Bridged networks: the host's interfaces are listed from sysfs, and the chosen one decides bridge or macvtap."""

import xml.etree.ElementTree as ET

import pytest


def _iface(root, name, devtype=None, device=False, dirs=(), files=(), operstate="up", mac="00:11:22:33:44:55"):
    path = root / name
    path.mkdir()
    (path / "operstate").write_text(operstate + "\n")
    (path / "address").write_text(mac + "\n")
    (path / "uevent").write_text(f"INTERFACE={name}\n" + (f"DEVTYPE={devtype}\n" if devtype else ""))
    if device:
        (path / "device").mkdir()
    for d in dirs:
        (path / d).mkdir()
    for f in files:
        (path / f).write_text("")


@pytest.fixture()
def sysnet(tmp_path):
    root = tmp_path / "net"
    root.mkdir()
    _iface(root, "eno1", device=True)
    _iface(root, "br0", devtype="bridge", dirs=("bridge",))
    _iface(root, "bond0", devtype="bond", dirs=("bonding",))
    _iface(root, "eno1.20", devtype="vlan")
    _iface(root, "wlp1s0", devtype="wlan", device=True, dirs=("wireless",), operstate="down")
    _iface(root, "tailscale0", files=("tun_flags",))
    for own in ("lo", "virbr0", "virbr-hlisol", "vnet3", "veth12"):
        _iface(root, own, devtype="bridge" if own.startswith("virbr") else None)
    return root


def test_only_interfaces_a_network_can_sit_on_are_listed(sysnet):
    from app.core.host_interfaces import list_host_interfaces

    listed = list_host_interfaces(sysnet, addresses={"eno1": ["192.168.3.42/24"]})
    assert [(i["nom"], i["type"], i["utilisable"]) for i in listed] == [
        ("br0", "pont", True),
        ("eno1", "physique", True),
        ("bond0", "bond", True),
        ("eno1.20", "vlan", True),
        ("wlp1s0", "wifi", False),
    ]
    eno1 = next(i for i in listed if i["nom"] == "eno1")
    assert eno1["adresses"] == ["192.168.3.42/24"] and eno1["etat"] == "up" and eno1["mac"] == "00:11:22:33:44:55"


class _Net:
    def __init__(self, xml):
        self.xml = xml

    def name(self):
        return ET.fromstring(self.xml).findtext("name")

    def UUIDString(self):
        return "u"

    def isActive(self):
        return 1

    def autostart(self):
        return 1

    def XMLDesc(self, flags=0):
        return self.xml

    def create(self):
        pass

    def setAutostart(self, v):
        pass


class _Conn:
    def __init__(self):
        self.defined = []

    def networkLookupByName(self, name):
        import libvirt

        raise libvirt.libvirtError("no network")

    def networkDefineXML(self, xml):
        self.defined.append(xml)
        return _Net(xml)

    def close(self):
        pass


@pytest.fixture()
def api(database, sysnet, monkeypatch):
    from app.core import host_interfaces
    from app.routers import network

    conn = _Conn()
    monkeypatch.setattr(host_interfaces, "SYS_NET", sysnet)
    monkeypatch.setattr(host_interfaces, "_addresses", lambda: {})
    monkeypatch.setattr(network, "open_conn", lambda node=None: conn)
    return conn


def test_the_form_gets_the_list_from_the_api(api, client, auth_headers):
    r = client.get("/networks/host-interfaces", headers=auth_headers("admin"))
    assert r.status_code == 200
    assert [i["nom"] for i in r.json()] == ["br0", "eno1", "bond0", "eno1.20", "wlp1s0"]
    assert (
        client.get("/networks/host-interfaces", headers=auth_headers("viewer", role="observateur")).status_code == 403
    )


def test_an_existing_bridge_is_used_as_a_bridge(api, client, auth_headers):
    r = client.post(
        "/networks", json={"name": "lan-br", "mode": "bridge", "bridge_name": "br0"}, headers=auth_headers("admin")
    )
    assert r.status_code == 201, r.text
    root = ET.fromstring(api.defined[-1])
    assert root.find("bridge").get("name") == "br0" and root.find("forward/interface") is None
    assert r.json()["pont"] == "br0" and r.json()["macvtap"] is False


@pytest.mark.parametrize("nic", ["eno1", "bond0", "eno1.20"])
def test_a_nic_bond_or_vlan_is_used_through_macvtap(api, client, auth_headers, nic):
    r = client.post(
        "/networks", json={"name": "lan", "mode": "bridge", "bridge_name": nic}, headers=auth_headers("admin")
    )
    assert r.status_code == 201, r.text
    root = ET.fromstring(api.defined[-1])
    assert root.find("forward").get("mode") == "bridge" and root.find("forward/interface").get("dev") == nic
    assert root.find("bridge") is None
    assert r.json()["pont"] == nic and r.json()["macvtap"] is True


@pytest.mark.parametrize(
    ("iface", "detail"),
    [("wlp1s0", "Wi-Fi"), ("eth9", "No interface"), ("virbr0", "No interface"), ("tailscale0", "No interface")],
)
def test_unusable_interfaces_are_refused(api, client, auth_headers, iface, detail):
    r = client.post(
        "/networks", json={"name": "lan", "mode": "bridge", "bridge_name": iface}, headers=auth_headers("admin")
    )
    assert r.status_code == 422 and detail in r.text
    assert api.defined == []
