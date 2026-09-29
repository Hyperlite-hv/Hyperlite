"""Editing a NAT/isolated network (subnet, DHCP range, mode) and its DHCP reservations."""

import ipaddress
import xml.etree.ElementTree as ET

import libvirt
import pytest

from app.core import network_edit as ne

NAT = (
    "<network><name>lab</name><forward mode='nat'/><bridge name='virbr9'/>"
    "<ip address='10.9.0.1' netmask='255.255.255.0'><dhcp><range start='10.9.0.100' end='10.9.0.200'/>"
    "<host mac='52:54:00:00:00:01' ip='10.9.0.10' name='web'/></dhcp></ip></network>"
)
BRIDGE = "<network><name>lan</name><forward mode='bridge'/><bridge name='br0'/></network>"


def edit(xml=NAT, **kw):
    args = {
        "mode": "nat",
        "address": "10.9.0.1",
        "netmask": "255.255.255.0",
        "dhcp": {"debut": "10.9.0.50", "fin": "10.9.0.60"},
    }
    return ne.edit_xml(xml, **(args | kw))


def test_describe_reads_subnet_range_and_reservations():
    d = ne.describe_xml(NAT)
    assert d == {
        "modifiable": True,
        "mode": "nat",
        "adresse": "10.9.0.1",
        "masque": "255.255.255.0",
        "dhcp": {"debut": "10.9.0.100", "fin": "10.9.0.200"},
        "reservations": [{"mac": "52:54:00:00:00:01", "ip": "10.9.0.10", "nom": "web"}],
    }
    assert ne.describe_xml(BRIDGE)["modifiable"] is False


def test_edit_changes_range_and_mode_and_keeps_reservations():
    d = ne.describe_xml(edit(mode="isole", netmask="255.255.0.0"))
    assert (d["mode"], d["masque"], d["dhcp"]) == ("isole", "255.255.0.0", {"debut": "10.9.0.50", "fin": "10.9.0.60"})
    assert d["reservations"][0]["ip"] == "10.9.0.10"
    root = ET.fromstring(edit(mode="isole"))
    assert root.find("forward") is None and root.find("bridge").get("name") == "virbr9"
    assert (
        ET.fromstring(
            edit(
                ne.edit_xml(
                    NAT,
                    mode="isole",
                    address="10.9.0.1",
                    netmask="255.255.255.0",
                    dhcp={"debut": "10.9.0.50", "fin": "10.9.0.60"},
                )
            )
        )
        .find("forward")
        .get("mode")
        == "nat"
    )


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"mode": "route"}, "mode must be"),
        ({"address": "10.9.0.300"}, "Invalid gateway"),
        ({"netmask": "255.0.255.0"}, "Invalid netmask"),
        ({"netmask": "255.255.255.252", "address": "10.9.0.1", "dhcp": None}, "Reservations outside"),
        ({"dhcp": {"debut": "10.9.0.60", "fin": "10.9.0.50"}}, "starts after"),
        ({"dhcp": {"debut": "10.9.1.5", "fin": "10.9.1.9"}}, "not a usable address"),
        ({"dhcp": {"debut": "10.9.0.1", "fin": "10.9.0.9"}}, "gateway 10.9.0.1 is inside"),
        ({"address": "10.9.0.255"}, "not a usable address"),
        ({"address": "10.8.0.1", "dhcp": {"debut": "10.8.0.50", "fin": "10.8.0.60"}}, "Reservations outside"),
        ({"dhcp": None}, "DHCP cannot be turned off"),
        ({"other_subnets": [("default", ipaddress.IPv4Network("10.9.0.0/16"))]}, "overlaps the network 'default'"),
    ],
)
def test_edit_refuses_inconsistent_settings(kw, message):
    with pytest.raises(ne.EditError, match=message):
        edit(**kw)


def test_bridge_network_is_not_editable():
    with pytest.raises(ne.EditError, match="no subnet of its own"):
        edit(BRIDGE)


class _Net:
    def __init__(self, xml, active=True):
        self.config, self.live, self.active, self.updates = xml, xml, active, []

    def name(self):
        return ET.fromstring(self.config).findtext("name")

    def isActive(self):
        return 1 if self.active else 0

    def autostart(self):
        return 1

    def UUIDString(self):
        return "u"

    def DHCPLeases(self):
        return [{"mac": "52:54:00:00:00:09", "ipaddr": "10.9.0.150", "hostname": "db", "expirytime": 1900000000}]

    def XMLDesc(self, flags=0):
        return self.config if flags & libvirt.VIR_NETWORK_XML_INACTIVE else self.live

    def update(self, command, section, index, xml, flags):
        self.updates.append((command, section, xml, flags))
        el = ET.fromstring(xml)
        for target in (
            (["config"] if not flags & libvirt.VIR_NETWORK_UPDATE_AFFECT_LIVE else ["config", "live"])
            if flags & libvirt.VIR_NETWORK_UPDATE_AFFECT_CONFIG
            else ["live"]
        ):
            root = ET.fromstring(getattr(self, target))
            dhcp = root.find("ip/dhcp")
            if command == libvirt.VIR_NETWORK_UPDATE_COMMAND_ADD_LAST:
                dhcp.append(el) if el.tag == "host" else dhcp.insert(0, el)
            else:
                for old in dhcp.findall(el.tag):
                    if old.attrib == el.attrib:
                        dhcp.remove(old)
            setattr(self, target, ET.tostring(root, encoding="unicode"))


class _Conn:
    def __init__(self, net):
        self.net = net

    def networkLookupByName(self, name):
        if name != self.net.name():
            raise libvirt.libvirtError("no network")
        return self.net

    def networkDefineXML(self, xml):
        self.net.config = xml
        return self.net

    def listAllNetworks(self):
        return [self.net]

    def close(self):
        pass


@pytest.fixture()
def conn(database, monkeypatch):
    from app.routers import network

    c = _Conn(_Net(NAT))
    monkeypatch.setattr(network, "open_conn", lambda node=None: c)
    return c


def test_range_change_is_live_and_subnet_change_waits_for_restart(client, auth_headers, conn):
    admin = auth_headers("root", "admin")
    body = {
        "mode": "nat",
        "adresse": "10.9.0.1",
        "masque": "255.255.255.0",
        "dhcp": {"debut": "10.9.0.20", "fin": "10.9.0.90"},
    }
    r = client.patch("/networks/lab", json=body, headers=admin)
    assert r.status_code == 200 and r.json()["a_redemarrer"] is False
    assert ne.describe_xml(conn.net.live)["dhcp"] == {"debut": "10.9.0.20", "fin": "10.9.0.90"}

    r = client.patch("/networks/lab", json=body | {"masque": "255.255.0.0"}, headers=admin)
    assert r.json()["a_redemarrer"] is True
    assert r.json()["ipam"]["masque"] == "255.255.0.0"
    assert ne.describe_xml(conn.net.live)["masque"] == "255.255.255.0"

    r = client.patch("/networks/lab", json=body | {"adresse": "10.9.0.20"}, headers=admin)
    assert r.status_code == 422 and "inside the DHCP range" in r.json()["detail"]
    assert client.patch("/networks/lab", json=body, headers=auth_headers("watcher", "observateur")).status_code == 403


def test_reservations_are_added_live_and_removed(client, auth_headers, conn):
    admin = auth_headers("root", "admin")
    r = client.post(
        "/networks/lab/reservations", json={"mac": "52:54:00:00:00:09", "ip": "10.9.0.150", "nom": "db"}, headers=admin
    )
    assert r.status_code == 201
    assert {"mac": "52:54:00:00:00:09", "ip": "10.9.0.150", "nom": "db"} in r.json()["reservations"]
    assert conn.net.updates[-1][3] == libvirt.VIR_NETWORK_UPDATE_AFFECT_CONFIG | libvirt.VIR_NETWORK_UPDATE_AFFECT_LIVE

    for bad, message in [
        ({"mac": "52:54:00:00:00:09", "ip": "10.9.0.151"}, "already has a reservation"),
        ({"mac": "52:54:00:00:00:0a", "ip": "10.9.0.10"}, "already reserved"),
        ({"mac": "52:54:00:00:00:0a", "ip": "10.9.0.1"}, "gateway"),
        ({"mac": "52:54:00:00:00:0a", "ip": "10.9.1.1"}, "not a usable address"),
        ({"mac": "zz", "ip": "10.9.0.11"}, "Invalid MAC"),
        ({"mac": "52:54:00:00:00:0a", "ip": "10.9.0.11", "nom": "bad name'/>"}, "Invalid name"),
    ]:
        r = client.post("/networks/lab/reservations", json=bad, headers=admin)
        assert r.status_code == 422 and message in r.json()["detail"], bad

    detail = client.get("/networks/lab", headers=admin).json()
    assert detail["baux_dhcp"][0]["expire"] == 1900000000
    assert len(detail["ipam"]["reservations"]) == 2

    assert client.delete("/networks/lab/reservations/52:54:00:00:00:09", headers=admin).status_code == 200
    assert client.delete("/networks/lab/reservations/52:54:00:00:00:09", headers=admin).status_code == 404
    assert [x["mac"] for x in ne.describe_xml(conn.net.live)["reservations"]] == ["52:54:00:00:00:01"]
