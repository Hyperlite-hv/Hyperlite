"""Per-node NAT networks in a cluster (#410): an address is unique across the nodes, and it moves with its VM."""

import xml.etree.ElementTree as ET

import libvirt
import pytest

from app.core import network_alloc


class FakeNetwork:
    def __init__(self, hosts=None, leases=None, dhcp=True):
        self.hosts = dict(hosts or {})  # mac -> ip
        self.leases = list(leases or [])
        self.dhcp = dhcp

    def XMLDesc(self, flags=0):
        hosts = "".join(f"<host mac='{m}' ip='{ip}'/>" for m, ip in self.hosts.items())
        rng = "<range start='192.168.122.2' end='192.168.122.254'/>" if self.dhcp else ""
        return f"<network><name>default</name><ip address='192.168.122.1' prefix='24'><dhcp>{rng}{hosts}</dhcp></ip></network>"

    def DHCPLeases(self):
        return self.leases

    def update(self, command, section, index, xml, flags):
        el = ET.fromstring(xml)
        if command == libvirt.VIR_NETWORK_UPDATE_COMMAND_DELETE:
            self.hosts.pop(el.get("mac"), None)
        else:
            self.hosts[el.get("mac")] = el.get("ip")


class FakeConn:
    def __init__(self, networks):
        self.networks = networks
        self.closed = False

    def networkLookupByName(self, name):
        if name not in self.networks:
            raise libvirt.libvirtError(f"no network {name}")
        return self.networks[name]

    def close(self):
        self.closed = True


def vm_xml(mac, network="default"):
    return f"<domain><devices><interface type='network'><mac address='{mac}'/><source network='{network}'/></interface></devices></domain>"


def test_an_address_taken_on_another_node_is_not_handed_out_again(monkeypatch):
    here = FakeNetwork()
    other = FakeNetwork(hosts={"52:54:00:00:00:01": "192.168.122.2"}, leases=[{"ipaddr": "192.168.122.3", "mac": "x"}])
    monkeypatch.setattr("app.core.cluster.list_nodes", lambda: [{"name": "hlc2"}])
    monkeypatch.setattr(
        "app.core.libvirt_utils.open_conn", lambda name=None: FakeConn({"default": other if name else here})
    )
    ip = network_alloc.allocate_static_ip(FakeConn({"default": here}), "default", "52:54:00:00:00:09")
    assert ip == "192.168.122.4"
    assert here.hosts["52:54:00:00:00:09"] == "192.168.122.4"


def test_a_node_that_does_not_answer_never_blocks_a_creation(monkeypatch):
    here = FakeNetwork()

    def open_conn(name=None):
        if name:
            raise libvirt.libvirtError("unreachable")
        return FakeConn({"default": here})

    monkeypatch.setattr("app.core.cluster.list_nodes", lambda: [{"name": "hlc3"}])
    monkeypatch.setattr("app.core.libvirt_utils.open_conn", open_conn)
    assert (
        network_alloc.allocate_static_ip(FakeConn({"default": here}), "default", "52:54:00:00:00:09") == "192.168.122.2"
    )


def test_the_reservation_goes_with_the_vm_and_is_released_on_the_source():
    mac = "52:54:00:aa:bb:cc"
    src = FakeConn({"default": FakeNetwork(hosts={mac: "192.168.122.7"})})
    dest = FakeConn({"default": FakeNetwork()})
    moved, added = network_alloc.carry_reservations(src, dest, vm_xml(mac))
    assert dest.networks["default"].hosts[mac] == "192.168.122.7"
    assert moved == [("default", mac)] and added == [("default", mac)]
    network_alloc.release_reservations(src, moved)
    assert mac not in src.networks["default"].hosts


def test_a_migration_is_refused_before_any_change_when_the_address_is_taken_there():
    mac = "52:54:00:aa:bb:cc"
    src = FakeConn({"default": FakeNetwork(hosts={mac: "192.168.122.2"})})
    dest_net = FakeNetwork(hosts={"52:54:00:00:00:01": "192.168.122.2"})
    with pytest.raises(network_alloc.AddressInUse, match=r"192\.168\.122\.2"):
        network_alloc.carry_reservations(src, FakeConn({"default": dest_net}), vm_xml(mac))
    assert mac not in dest_net.hosts

    leased = FakeNetwork(leases=[{"ipaddr": "192.168.122.2", "mac": "52:54:00:00:00:02"}])
    with pytest.raises(network_alloc.AddressInUse):
        network_alloc.carry_reservations(src, FakeConn({"default": leased}), vm_xml(mac))


def test_a_failed_migration_takes_back_only_what_it_added():
    mac = "52:54:00:aa:bb:cc"
    src = FakeConn({"default": FakeNetwork(hosts={mac: "192.168.122.7"})})
    dest = FakeConn({"default": FakeNetwork()})
    _, added = network_alloc.carry_reservations(src, dest, vm_xml(mac))
    network_alloc.release_reservations(dest, added)
    assert mac not in dest.networks["default"].hosts
    assert src.networks["default"].hosts[mac] == "192.168.122.7"


def test_bridged_networks_and_unreserved_vms_are_left_alone():
    mac = "52:54:00:aa:bb:cc"
    src = FakeConn({"lan": FakeNetwork(dhcp=False), "default": FakeNetwork()})
    dest = FakeConn({"lan": FakeNetwork(dhcp=False), "default": FakeNetwork()})
    xml = (
        "<domain><devices>"
        + "".join(
            f"<interface type='network'><mac address='{mac}'/><source network='{n}'/></interface>"
            for n in ("lan", "default")
        )
        + "</devices></domain>"
    )
    assert network_alloc.carry_reservations(src, dest, xml) == ([], [])
