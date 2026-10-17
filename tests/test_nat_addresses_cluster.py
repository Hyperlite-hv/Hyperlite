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


class StrictNetwork(FakeNetwork):
    """libvirt's own rule, seen in the lab: a reservation for an address another one holds is refused."""

    def update(self, command, section, index, xml, flags):
        el = ET.fromstring(xml)
        if command != libvirt.VIR_NETWORK_UPDATE_COMMAND_DELETE and el.get("ip") in self.hosts.values():
            raise libvirt.libvirtError("there is an existing dhcp host entry in network 'default' that matches")
        super().update(command, section, index, xml, flags)


def test_an_address_reserved_meanwhile_moves_on_to_the_next_one(monkeypatch):
    """Two creations at once read the same free addresses: the second one's reservation was refused and the VM was
    created without its fixed address, silently."""
    net = StrictNetwork()
    real_used = network_alloc._used_ips

    def stale(network):  # what was read before the other creation reserved .2
        used = real_used(network)
        network.hosts.setdefault("52:54:00:00:00:01", "192.168.122.2")
        return used

    monkeypatch.setattr(network_alloc, "_used_ips", stale)
    ip = network_alloc.allocate_static_ip(FakeConn({"default": net}), "default", "52:54:00:00:00:09", cluster=False)
    assert ip == "192.168.122.3" and net.hosts["52:54:00:00:00:09"] == "192.168.122.3"


def test_a_network_that_refuses_every_reservation_is_not_tried_address_by_address():
    class Refusing(FakeNetwork):
        calls = 0

        def update(self, *a):
            Refusing.calls += 1
            raise libvirt.libvirtError("network is not active")

    with pytest.raises(libvirt.libvirtError):
        network_alloc.allocate_static_ip(
            FakeConn({"default": Refusing()}), "default", "52:54:00:00:00:09", cluster=False
        )
    assert Refusing.calls == 3


def test_the_fixed_address_comes_from_the_ipv4_subnet_when_ipv6_is_listed_first():
    class DualStack(FakeNetwork):
        def XMLDesc(self, flags=0):
            return (
                "<network><name>default</name>"
                "<ip family='ipv6' address='fd00::1' prefix='64'><dhcp><range start='fd00::10' end='fd00::ff'/></dhcp></ip>"
                "<ip address='192.168.122.1' prefix='24'><dhcp><range start='192.168.122.2' end='192.168.122.254'/>"
                "</dhcp></ip></network>"
            )

    net = DualStack()
    assert network_alloc.allocate_static_ip(
        FakeConn({"default": net}), "default", "52:54:00:00:00:09", cluster=False
    ) == ("192.168.122.2")


def test_a_new_mac_is_never_one_of_a_stopped_vm(monkeypatch):
    """A stopped VM holds no DHCP lease: its MAC was not seen, and could be handed to a new VM."""

    class Domain:
        def XMLDesc(self, flags=0):
            return vm_xml("52:54:00:00:00:01")

    class Conn:
        def listAllDomains(self):
            return [Domain()]

        def listAllNetworks(self):
            return [FakeNetwork(hosts={"52:54:00:00:00:02": "192.168.122.9"})]

    draws = iter([0, 0, 1, 0, 0, 2, 0, 0, 3])
    monkeypatch.setattr(network_alloc.random, "randint", lambda a, b: next(draws))
    assert network_alloc.generate_mac(Conn()) == "52:54:00:00:00:03"
