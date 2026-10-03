"""Creating a NAT or isolated network: an address range already in use is refused before anything is defined, and a
network that fails to start is not left behind (found on a real host: a 500 with libvirt's own message, and the
network stayed listed, defined but inactive)."""

import ipaddress

import pytest


class _Net:
    def __init__(self, xml, fail=None):
        self.xml, self.fail, self.active, self.auto, self.undefined = xml, fail, False, False, False

    def name(self):
        import re

        return re.search(r"<name>(.*?)</name>", self.xml).group(1)

    def UUIDString(self):
        return "u"

    def XMLDesc(self, flags=0):
        return self.xml

    def isActive(self):
        return 1 if self.active else 0

    def autostart(self):
        return 1 if self.auto else 0

    def create(self):
        import libvirt

        if self.fail:
            raise libvirt.libvirtError(self.fail)
        self.active = True

    def destroy(self):
        self.active = False

    def setAutostart(self, value):
        self.auto = bool(value)

    def undefine(self):
        self.undefined = True


class _Conn:
    def __init__(self):
        default = (
            "<network><name>default</name><forward mode='nat'/><bridge name='virbr0'/>"
            "<ip address='192.168.122.1' netmask='255.255.255.0'/></network>"
        )
        self.nets = {"default": _Net(default)}
        self.fail_next = None

    def networkLookupByName(self, name):
        import libvirt

        if name not in self.nets or self.nets[name].undefined:
            raise libvirt.libvirtError("no network")
        return self.nets[name]

    def listAllNetworks(self, flags=0):
        return [n for n in self.nets.values() if not n.undefined]

    def networkDefineXML(self, xml):
        net = _Net(xml, fail=self.fail_next)
        self.nets[net.name()] = net
        return net

    def close(self):
        pass


@pytest.fixture()
def conn(database, monkeypatch):
    from app.routers import network

    c = _Conn()
    monkeypatch.setattr(network, "open_conn", lambda node=None: c)
    # The host's own addresses: its LAN, and the bridge of the default network.
    monkeypatch.setattr(
        network,
        "_host_subnets",
        lambda: [
            ("eno1", ipaddress.ip_network("192.168.1.0/24")),
            ("virbr0", ipaddress.ip_network("192.168.122.0/24")),
        ],
    )
    return c


def _create(client, admin, **over):
    body = {"name": "lab", "mode": "nat", "subnet_address": "192.168.177.1", **over}
    return client.post("/networks", json=body, headers=admin)


def test_a_free_range_is_created_and_started(conn, client, auth_headers):
    r = _create(client, auth_headers("admin"), dhcp_start="192.168.177.100", dhcp_end="192.168.177.200")
    assert r.status_code == 201 and r.json()["actif"] is True and conn.nets["lab"].auto


def test_a_range_already_used_by_a_network_or_the_host_is_refused_before_anything_is_defined(
    conn, client, auth_headers
):
    admin = auth_headers("admin")
    r = _create(client, admin, subnet_address="192.168.122.1")
    assert r.status_code == 409 and "default" in r.json()["detail"]
    r = _create(client, admin, name="lab2", subnet_address="192.168.1.254")
    assert r.status_code == 409 and "eno1" in r.json()["detail"]
    # A wider range that contains an existing one overlaps it too.
    r = _create(client, admin, name="lab3", subnet_address="192.168.0.1", subnet_netmask="255.255.0.0")
    assert r.status_code == 409
    assert set(conn.nets) == {"default"}


def test_the_dhcp_range_must_lie_inside_the_subnet(conn, client, auth_headers):
    admin = auth_headers("admin")
    assert _create(client, admin, dhcp_start="192.168.177.200", dhcp_end="192.168.177.100").status_code == 422
    assert _create(client, admin, dhcp_start="10.0.0.1", dhcp_end="10.0.0.9").status_code == 422
    assert set(conn.nets) == {"default"}


def test_a_network_that_does_not_start_is_not_left_defined(conn, client, auth_headers):
    conn.fail_next = "address in use"
    r = _create(client, auth_headers("admin"))
    assert r.status_code == 500 and "address in use" in r.text
    assert conn.nets["lab"].undefined
    assert [n.name() for n in conn.listAllNetworks()] == ["default"]
