"""The network's root work in hyperlite-daemon (app/daemon/ops/network.py, docs/design/privilege-separation.md section
13): the iptables of the network firewall and the VLANs of Linux bridges, asked for by a network's or a VM's name. The
daemon reads the bridge and the ports itself, so the API never names an interface; the database stays the API's."""

import xml.etree.ElementTree as ET

import libvirt
import pytest

from app.core import bridge_vlan, network_firewall, privileged
from app.daemon.registry import DaemonError

RULES = {"default_policy": "drop", "rules": [{"direction": "in", "protocol": "tcp", "port": 22, "action": "accept"}]}


class Net:
    def __init__(self, xml):
        self.xml = xml

    def XMLDesc(self, flags=0):
        return self.xml


class Domain:
    def __init__(self, name, xml, active):
        self.name_, self.xml, self.active = name, xml, active

    def name(self):
        return self.name_

    def isActive(self):
        return self.active

    def XMLDesc(self, flags=0):
        return self.xml


NETS = {
    "lan": "<network><name>lan</name><forward mode='bridge'/><bridge name='br0'/></network>",
    "nat": "<network><name>nat</name><forward mode='nat'/><bridge name='virbr0'/></network>",
}
WEB_XML = (
    "<domain><name>web</name><metadata><hl:vlans xmlns:hl='urn:hyperlite:vlan:1'>"
    "<hl:port mac='52:54:00:00:00:01' tag='20'/></hl:vlans></metadata><devices>"
    "<interface type='network'><mac address='52:54:00:00:00:01'/><source network='lan' bridge='br0'/>"
    "<target dev='vnet7'/></interface></devices></domain>"
)


class Conn:
    """The libvirt connection hyperlite-daemon opens itself (here, in the tests' own process)."""

    def __init__(self):
        self.domains = {}

    def networkLookupByName(self, name):
        if name not in NETS:
            raise libvirt.libvirtError("no network")
        return Net(NETS[name])

    def lookupByName(self, name):
        if name not in self.domains:
            raise libvirt.libvirtError("no domain")
        return self.domains[name]

    def close(self):
        pass


@pytest.fixture()
def host(daemon_libvirt):
    return daemon_libvirt(Conn())


@pytest.fixture()
def iptables(monkeypatch):
    calls = []

    class Ok:
        returncode, stderr = 0, ""

    def run(*args):
        calls.append(args)
        return Ok()

    monkeypatch.setattr(network_firewall, "_run", run)
    return calls


def test_no_operation_takes_an_interface_from_the_api(host, iptables):
    """A bridge or a port named by the caller is refused: the daemon reads them from libvirt."""
    for op, args in [
        ("network.firewall_apply", {"network": "lan", "config": RULES, "bridge": "eth0"}),
        ("network.firewall_remove", {"network": "lan", "bridge": "eth0"}),
        ("network.vlan_aware_start", {"network": "lan", "bridge": "eth0"}),
        ("vm.set_interface", {"vm": "web", "network": "lan", "tap": "eth0"}),
    ]:
        with pytest.raises(DaemonError) as e:
            privileged.run(op, **args)
        assert e.value.kind == "invalid_arguments", op
    assert iptables == []


@pytest.mark.parametrize(
    "rule",
    [
        {"direction": "in", "protocol": "tcp; reboot", "action": "accept"},
        {"direction": "sideways", "protocol": "tcp", "action": "accept"},
        {"direction": "in", "protocol": "tcp", "port": 70000, "action": "accept"},
        {"direction": "in", "protocol": "tcp", "action": "accept", "jump": "LOG"},
    ],
)
def test_a_rule_is_checked_again_by_the_daemon(host, iptables, rule):
    with pytest.raises(DaemonError) as e:
        privileged.run("network.firewall_apply", network="lan", config={"default_policy": "drop", "rules": [rule]})
    assert e.value.kind == "invalid_arguments"
    assert iptables == []


def test_the_firewall_is_applied_on_the_bridge_the_daemon_reads(host, iptables, database):
    result = network_firewall.apply_network_firewall("lan", RULES, user="ana")
    assert result == {"bridge": "br0", "applied_rules": 1}
    rules = [args for args in iptables if args[0] == "-A" and args[1].startswith("HLNET")]
    assert rules and all("br0" in args for args in rules)
    assert network_firewall.get_network_firewall("lan") == RULES  # kept by the API, to replay after a reboot


def test_an_unknown_network_is_refused_and_nothing_is_kept(host, iptables, database):
    with pytest.raises(network_firewall.Refused):
        network_firewall.apply_network_firewall("nowhere", RULES)
    assert network_firewall.get_network_firewall("nowhere")["rules"] == []
    assert iptables == []


def test_without_the_daemon_the_firewall_route_says_so(client, auth_headers, monkeypatch, tmp_path, database):
    from app.routers import network

    class ApiConn(Conn):
        pass

    monkeypatch.setattr(network, "open_conn", lambda *a, **k: ApiConn())
    monkeypatch.delenv(privileged.INPROCESS_ENV)
    monkeypatch.setenv("HYPERLITE_DAEMON_SOCKET", str(tmp_path / "none.sock"))
    r = client.put("/networks/lan/firewall", json=RULES, headers=auth_headers("ana", "admin"))
    assert r.status_code == 503 and "not running" in r.json()["detail"]
    assert network_firewall.get_network_firewall("lan")["rules"] == []


def test_a_bridge_is_made_vlan_aware_only_through_its_network(host, monkeypatch):
    started = []
    monkeypatch.setattr(bridge_vlan, "is_linux_bridge", lambda name: name == "br0")
    monkeypatch.setattr(bridge_vlan, "start_trial", lambda bridge, seconds=60: started.append(bridge) or "t0k")
    assert privileged.run("network.vlan_aware_start", network="lan") == {
        "bridge": "br0",
        "token": "t0k",
        "expires_in": 60,
    }
    for network in ("nat", "nowhere"):  # libvirt's own bridge; no such network
        with pytest.raises(DaemonError) as e:
            privileged.run("network.vlan_aware_start", network=network)
        assert e.value.kind == "refused"
    assert started == ["br0"]


def test_a_vms_vlans_are_set_from_the_ports_the_daemon_reads(host, monkeypatch):
    applied = []
    monkeypatch.setattr(bridge_vlan, "filters_vlans", lambda bridge, node=None: bridge == "br0")
    monkeypatch.setattr(bridge_vlan, "apply_port", lambda tap, tag, trunks, node=None: applied.append((tap, tag)))
    host.domains = {"web": Domain("web", WEB_XML, active=True), "off": Domain("off", WEB_XML, active=False)}
    # What hyperlite-daemon runs once a VM's interface changed (app/core/vm_devices.py), reading the ports itself.
    assert bridge_vlan.apply_vm("web") == []
    assert applied == [("vnet7", 20)]
    assert bridge_vlan.apply_vm("off") == []  # the hook sets it when it starts
    assert applied == [("vnet7", 20)]
    with pytest.raises(bridge_vlan.VlanError):
        bridge_vlan.apply_vm("nobody")


def test_the_service_start_sets_the_vlans_of_the_running_vms(host, monkeypatch):
    seen = []
    monkeypatch.setattr(bridge_vlan, "boot", lambda: seen.append("boot") or [])
    monkeypatch.setattr(bridge_vlan, "apply_running", lambda conn=None, node=None: seen.append("running") or ["x"])
    assert privileged.run("network.vlan_boot") == {"errors": ["x"]}
    assert seen == ["boot", "running"]


def test_the_ports_are_read_from_the_live_definition():
    root = ET.fromstring(WEB_XML)
    assert bridge_vlan.interfaces_to_apply(root) == [("vnet7", "br0", 20, None)]


# --- Part 2a: libvirt's networks ---


class Lib:
    """libvirt as hyperlite-daemon sees it: networks defined, started, autostarted; a VM on one of them."""

    def __init__(self):
        self.nets = {}
        self.domains = []

    def networkLookupByName(self, name):
        if name not in self.nets:
            raise libvirt.libvirtError("no network")
        return self.nets[name]

    def networkDefineXML(self, xml):
        net = LibNet(self, xml)
        self.nets[net.name()] = net
        return net

    def listAllDomains(self, flags=0):
        return self.domains

    def close(self):
        pass


class LibNet:
    def __init__(self, lib, xml):
        self.lib, self.xml, self.active, self.auto = lib, xml, False, False

    def name(self):
        return ET.fromstring(self.xml).findtext("name")

    def create(self):
        self.active = True

    def destroy(self):
        self.active = False

    def undefine(self):
        del self.lib.nets[self.name()]

    def isActive(self):
        return self.active

    def setAutostart(self, on):
        self.auto = bool(on)

    def autostart(self):
        return self.auto

    def XMLDesc(self, flags=0):
        return self.xml


@pytest.fixture()
def lib(monkeypatch, daemon_libvirt):
    lib = Lib()
    daemon_libvirt(lib)
    monkeypatch.setattr("app.core.libvirt_utils.open_lxc_conn_if_any", lambda: None)
    return lib


ROUTED = {"mode": "nat", "subnet_address": "10.77.0.1", "subnet_netmask": "255.255.255.0"}


def test_the_daemon_writes_the_definition_itself(lib):
    privileged.run("network.create", name="lab", **ROUTED, dhcp_start="10.77.0.10", dhcp_end="10.77.0.50")
    net = lib.nets["lab"]
    assert net.isActive() and net.autostart()
    root = ET.fromstring(net.XMLDesc())
    assert root.find("forward").get("mode") == "nat" and root.find("ip").get("address") == "10.77.0.1"
    with pytest.raises(DaemonError) as e:  # libvirt XML from the caller is no argument
        privileged.run("network.create", name="x", **ROUTED, xml="<network/>")
    assert e.value.kind == "invalid_arguments"


@pytest.mark.parametrize(
    "fields",
    [
        {"name": "lab'/><forward mode='bridge"},  # a quote in the name would rewrite the definition
        {"name": "lab", "subnet_address": "10.0.0.1'/><dns"},
        {"name": "lab", "dhcp_start": "10.77.0.10' x='", "dhcp_end": "10.77.0.50"},
        {"name": "lab", "mode": "bridge", "bridge_name": "br0'/>"},
    ],
)
def test_nothing_that_could_rewrite_the_definition_reaches_libvirt(lib, fields):
    with pytest.raises(DaemonError) as e:
        privileged.run("network.create", **{**ROUTED, **fields})
    assert e.value.kind == "refused"
    assert lib.nets == {}


def test_the_daemon_keeps_the_system_networks_and_those_in_use(lib):
    for name in ("default", "hyperlite-isolated", "lan"):
        lib.networkDefineXML(f"<network><name>{name}</name></network>")
    lib.domains = [
        Domain(
            "web",
            "<domain><devices><interface type='network'><source network='lan'/></interface></devices></domain>",
            True,
        )
    ]
    for op, args in [
        ("network.delete", {"network": "default"}),
        ("network.delete", {"network": "lan"}),  # a VM still on it, whatever the API checked
        ("network.rename", {"network": "hyperlite-isolated", "new_name": "other"}),
        ("network.rename", {"network": "lan", "new_name": "bad name"}),
    ]:
        with pytest.raises(DaemonError) as e:
            privileged.run(op, **args)
        assert e.value.kind == "refused", (op, args)
    assert set(lib.nets) == {"default", "hyperlite-isolated", "lan"}


def test_start_stop_autostart_and_edit_are_the_daemons(lib):
    privileged.run("network.create", name="lab", **ROUTED)
    privileged.run("network.stop", network="lab")
    assert not lib.nets["lab"].isActive()
    privileged.run("network.start", network="lab")
    privileged.run("network.autostart", network="lab", autostart=False)
    assert lib.nets["lab"].isActive() and not lib.nets["lab"].autostart()
    with pytest.raises(DaemonError) as e:
        privileged.run("network.start", network="nowhere")
    assert e.value.kind == "not_found"


def test_listing_the_networks_asks_the_daemon_only_for_a_missing_isolated_network(monkeypatch):
    from app.routers import network

    asked = []
    monkeypatch.setattr(privileged, "run", lambda op, **kw: asked.append(op))
    present = Lib()
    present.networkDefineXML("<network><name>hyperlite-isolated</name></network>")
    present.nets["hyperlite-isolated"].create()
    present.nets["hyperlite-isolated"].setAutostart(True)
    network._ensure_isolated(present, {"username": "ana"})
    assert asked == []
    network._ensure_isolated(Lib(), {"username": "ana"})
    assert asked == ["network.ensure_isolated"]


class ReservedNet(LibNet):
    def __init__(self, lib, xml):
        super().__init__(lib, xml)
        self.updates = []

    def update(self, command, section, index, xml, flags):
        self.updates.append((command, xml))


def test_a_reservation_is_checked_again_by_the_daemon(lib):
    xml = (
        "<network><name>lab</name><forward mode='nat'/><bridge name='virbr9'/><ip address='10.77.0.1' "
        "netmask='255.255.255.0'><dhcp><range start='10.77.0.10' end='10.77.0.50'/></dhcp></ip></network>"
    )
    net = lib.nets["lab"] = ReservedNet(lib, xml)
    for fields in (
        {"mac": "not-a-mac", "ip": "10.77.0.20"},
        {"mac": "52:54:00:00:00:09", "ip": "10.99.0.20"},  # outside the network
        {"mac": "52:54:00:00:00:09", "ip": "10.77.0.1"},  # its gateway
        {"mac": "52:54:00:00:00:09", "ip": "10.77.0.20", "name": "a name'/>"},
    ):
        with pytest.raises(DaemonError) as e:
            privileged.run("network.reservation_add", network="lab", **fields)
        assert e.value.kind == "refused", fields
    assert net.updates == []
    privileged.run("network.reservation_add", network="lab", mac="52:54:00:00:00:09", ip="10.77.0.20", name="web")
    assert len(net.updates) == 1 and "10.77.0.20" in net.updates[0][1]
