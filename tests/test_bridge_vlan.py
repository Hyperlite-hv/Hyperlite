"""VLANs of VMs on a VLAN-aware Linux bridge (app/core/bridge_vlan.py): what is recorded in the VM's metadata, the
`bridge vlan` commands that set a port, the libvirt hook, and the API on such a network. Checked on a real bridge and
libvirt 10 in the lab (docs/design/network.md)."""

import io
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import libvirt
import pytest

from app.core import bridge_vlan, network_edit


class FakeBridge:
    """`bridge vlan` on ports: the VLANs of each, changed by the same commands bridge_vlan runs."""

    def __init__(self, monkeypatch):
        self.ports = {}
        self.calls = []
        self.fail = None  # a VLAN id whose `add` fails
        monkeypatch.setattr(bridge_vlan, "_run", self.run)

    def run(self, args, node=None):
        self.calls.append(args)
        if args[:3] == ["bridge", "-j", "vlan"]:
            dev = args[-1]
            entries = [{"vlan": v, "flags": f} for v, f in sorted(self.ports.get(dev, {}).items())]
            return json.dumps([{"ifname": dev, "vlans": entries}] if entries else [])
        verb, dev, vid = args[2], args[4], args[6]
        first, _, last = vid.partition("-")
        vids = range(int(first), int(last or first) + 1)
        port = self.ports.setdefault(dev, {})
        if verb == "del":
            for v in vids:
                port.pop(v, None)
        else:
            if self.fail in vids:
                raise bridge_vlan.VlanError("RTNETLINK answers: Operation not supported")
            flags = ["PVID", "Egress Untagged"] if "pvid" in args else []
            for v in vids:
                port[v] = flags
        return ""

    def kinds(self, dev):
        return {v: "access" if f else "tagged" for v, f in self.ports.get(dev, {}).items()}


@pytest.fixture()
def br(monkeypatch):
    fake = FakeBridge(monkeypatch)
    fake.ports["vnet0"] = {1: ["PVID", "Egress Untagged"]}  # a new port: VLAN 1 untagged, checked in the lab
    return fake


@pytest.mark.parametrize(
    ("text", "ranges"),
    [
        ("", []),
        ("10", [(10, 10)]),
        ("10;20-30", [(10, 10), (20, 30)]),
        ("40-42,5", [(5, 5), (40, 42)]),
        (" 10, 20 ", [(10, 10), (20, 20)]),
    ],
)
def test_trunks_are_read_as_proxmox_and_vsphere_write_them(text, ranges):
    assert bridge_vlan.parse_trunks(text) == ranges


@pytest.mark.parametrize("text", ["0", "4095", "30-20", "a", "10,,20", "1-2-3", "$(id)", "10 20"])
def test_trunks_outside_1_to_4094_or_malformed_are_refused(text):
    with pytest.raises(bridge_vlan.VlanError):
        bridge_vlan.parse_trunks(text)


def test_a_port_takes_its_access_vlan_untagged_and_its_trunks_tagged(br):
    bridge_vlan.apply_port("vnet0", 20, "30,40-42")
    assert br.kinds("vnet0") == {20: "access", 30: "tagged", 40: "tagged", 41: "tagged", 42: "tagged"}
    bridge_vlan.apply_port("vnet0", None, None)
    assert br.kinds("vnet0") == {1: "access"}, "no VLAN: the bridge's default, as on Proxmox VE"


def test_an_access_vlan_inside_a_trunk_range_stays_untagged(br):
    bridge_vlan.apply_port("vnet0", 20, "18-22")
    assert br.kinds("vnet0") == {18: "tagged", 19: "tagged", 20: "access", 21: "tagged", 22: "tagged"}


def test_a_port_that_cannot_be_set_carries_no_traffic(br):
    br.fail = 30
    with pytest.raises(bridge_vlan.VlanError, match="vnet0"):
        bridge_vlan.apply_port("vnet0", 20, "30")
    assert br.kinds("vnet0") == {}, "never left on the bridge's untagged VLAN"


class FakeDomain:
    """A domain whose metadata is set as libvirt does: under the namespace, in the definition and the live XML."""

    def __init__(self, interfaces, active=True):
        self.interfaces = interfaces
        self.active = active
        self.metadata = None
        self.set_calls = []

    def setMetadata(self, kind, xml, key, uri, flags):
        self.set_calls.append((xml, key, uri, flags))
        self.metadata = None if xml is None else (uri, xml)

    def isActive(self):
        return self.active

    def XMLDesc(self, flags=0):
        meta = ""
        if self.metadata:
            uri, xml = self.metadata
            inner = ET.fromstring(xml)
            ports = "".join(ET.tostring(p, encoding="unicode") for p in inner)
            meta = f"<metadata><vlans xmlns='{uri}'>{ports}</vlans></metadata>"
        ifaces = "".join(self.interfaces)
        return f"<domain><name>web</name>{meta}<devices>{ifaces}</devices></domain>"


IFACE = (
    "<interface type='network'><mac address='52:54:00:00:00:01'/><source network='lan' bridge='br0'/>"
    "<target dev='vnet0'/></interface>"
)


def test_the_vlans_are_recorded_by_mac_and_forgotten_when_cleared():
    dom = FakeDomain([IFACE])
    bridge_vlan.set_port(dom, "52:54:00:00:00:01", 20, "30;40-42")
    assert bridge_vlan.ports_of_domain_xml(ET.fromstring(dom.XMLDesc())) == {
        "52:54:00:00:00:01": {"tag": 20, "trunks": "30,40-42"}
    }
    assert dom.set_calls[-1][3] == libvirt.VIR_DOMAIN_AFFECT_CONFIG | libvirt.VIR_DOMAIN_AFFECT_LIVE
    bridge_vlan.set_port(dom, "52:54:00:00:00:01", None, None)
    assert dom.metadata is None


def test_a_clone_keeps_its_interfaces_vlans_under_their_new_mac():
    dom = FakeDomain([IFACE])
    bridge_vlan.set_port(dom, "52:54:00:00:00:01", 20, None)
    root = ET.fromstring(dom.XMLDesc())
    bridge_vlan.remap_mac(root, "52:54:00:00:00:01", "52:54:00:aa:bb:cc")
    assert bridge_vlan.ports_of_domain_xml(root) == {"52:54:00:aa:bb:cc": {"tag": 20, "trunks": None}}


def test_a_running_vm_gets_its_vlans_only_on_a_bridge_that_filters(monkeypatch, br):
    dom = FakeDomain([IFACE])
    bridge_vlan.set_port(dom, "52:54:00:00:00:01", 20, None)
    monkeypatch.setattr(bridge_vlan, "filters_vlans", lambda bridge, node=None: bridge == "br0")
    assert bridge_vlan.apply_domain(ET.fromstring(dom.XMLDesc())) == []
    assert br.kinds("vnet0") == {20: "access"}
    monkeypatch.setattr(bridge_vlan, "filters_vlans", lambda bridge, node=None: False)
    errors = bridge_vlan.apply_domain(ET.fromstring(dom.XMLDesc()))
    assert errors and "does not filter VLANs" in errors[0]
    assert br.kinds("vnet0") == {}, "a VLAN that cannot be honoured: no traffic rather than the untagged LAN"


def test_the_hook_acts_on_started_only_and_never_fails_the_vm(monkeypatch, br, capsys):
    dom = FakeDomain([IFACE])
    bridge_vlan.set_port(dom, "52:54:00:00:00:01", 20, None)
    monkeypatch.setattr(bridge_vlan, "filters_vlans", lambda bridge, node=None: True)
    assert bridge_vlan.hook(["web", "prepare", "begin", "-"], io.StringIO(dom.XMLDesc())) == 0
    assert br.kinds("vnet0") == {1: "access"}, "only 'started': before it, the port does not exist"
    assert bridge_vlan.hook(["web", "started", "begin", "-"], io.StringIO(dom.XMLDesc())) == 0
    assert br.kinds("vnet0") == {20: "access"}
    assert bridge_vlan.hook(["web", "started", "begin", "-"], io.StringIO("<domain")) == 0
    assert "domain XML" in capsys.readouterr().err


def test_the_hook_script_runs_the_module_on_started_only():
    script = Path("scripts/libvirt-hook-vlan").read_text()
    assert '[ "$2" = started ] && [ "$3" = begin ] || exit 0' in script
    assert "-m app.core.bridge_vlan hook" in script


def test_a_vlan_aware_linux_bridge_network_carries_vlans(monkeypatch):
    linux = ET.fromstring("<network><forward mode='bridge'/><bridge name='br0'/></network>")
    nat = ET.fromstring("<network><forward mode='nat'/><bridge name='virbr0'/></network>")
    monkeypatch.setattr(bridge_vlan, "filters_vlans", lambda bridge, node=None: True)
    assert network_edit.vlan_mode(linux) == "bridge"
    assert network_edit.vlan_mode(nat) is None, "a NAT network's own bridge is libvirt's"
    monkeypatch.setattr(bridge_vlan, "filters_vlans", lambda bridge, node=None: False)
    assert network_edit.vlan_mode(linux) is None


def test_the_bridge_name_is_checked_before_sysfs_is_read():
    assert bridge_vlan.filters_vlans("../../etc") is False
    assert bridge_vlan.filters_vlans("") is False


# --- The API on a VLAN-aware bridge network ---

NETWORKS = {
    "lan": "<network><name>lan</name><forward mode='bridge'/><bridge name='br0'/></network>",
    "ovs": "<network><name>ovs</name><forward mode='bridge'/><bridge name='ovsbr0'/>"
    "<virtualport type='openvswitch'/></network>",
    "nat": "<network><name>nat</name><forward mode='nat'/><bridge name='virbr0'/></network>",
}


class ApiDomain(FakeDomain):
    def __init__(self):
        super().__init__(
            [
                "<interface type='network'><mac address='52:54:00:00:00:01'/><source network='nat'/>"
                "<model type='virtio'/></interface>"
            ],
            active=False,
        )
        self.attached = []
        self.updated = []
        self.detached = []

    def attachDeviceFlags(self, xml, flags):
        self.attached.append(xml)

    def updateDeviceFlags(self, xml, flags):
        self.updated.append(xml)

    def detachDeviceFlags(self, xml, flags):
        self.detached.append(xml)


@pytest.fixture()
def api(monkeypatch):
    from app.routers.vms import devices

    dom = ApiDomain()

    class Net:
        def __init__(self, name):
            self.name_ = name

        def XMLDesc(self, flags=0):
            return NETWORKS[self.name_]

    class Conn:
        def lookupByName(self, name):
            return dom

        def networkLookupByName(self, name):
            if name not in NETWORKS:
                raise libvirt.libvirtError("no network")
            return Net(name)

        def close(self):
            pass

    monkeypatch.setattr(devices, "open_conn", lambda *a, **k: Conn())
    monkeypatch.setattr(devices, "generate_mac", lambda conn: "52:54:00:00:00:02")
    monkeypatch.setattr(bridge_vlan, "filters_vlans", lambda bridge, node=None: bridge == "br0")
    return dom


def test_a_tag_and_trunks_on_a_vlan_aware_bridge_go_to_the_metadata(client, auth_headers, api):
    admin = auth_headers("admin")
    r = client.put(
        "/vms/web/network", json={"network": "lan", "vlan_tag": 20, "vlan_trunks": "30;40-42"}, headers=admin
    )
    assert r.status_code == 200, r.text
    assert ET.fromstring(api.updated[-1]).find("vlan") is None, "libvirt refuses <vlan> on a Linux bridge"
    ports = bridge_vlan.ports_of_domain_xml(ET.fromstring(api.XMLDesc()))
    assert ports == {"52:54:00:00:00:01": {"tag": 20, "trunks": "30,40-42"}}

    r = client.post("/vms/web/interfaces", json={"network": "lan", "vlan_tag": 30}, headers=admin)
    assert r.status_code == 201, r.text
    assert ET.fromstring(api.attached[-1]).find("mac").get("address") == "52:54:00:00:00:02"
    assert bridge_vlan.ports_of_domain_xml(ET.fromstring(api.XMLDesc()))["52:54:00:00:00:02"]["tag"] == 30

    # Moved to a network without bridge VLANs: its recorded VLANs are forgotten.
    assert client.put("/vms/web/network", json={"network": "nat"}, headers=admin).status_code == 200
    assert "52:54:00:00:00:01" not in bridge_vlan.ports_of_domain_xml(ET.fromstring(api.XMLDesc()))


def test_trunks_are_refused_where_they_cannot_be_set(client, auth_headers, api):
    admin = auth_headers("admin")
    r = client.put("/vms/web/network", json={"network": "ovs", "vlan_tag": 20, "vlan_trunks": "30"}, headers=admin)
    assert r.status_code == 422 and "VLAN-aware Linux bridge only" in r.json()["detail"]
    r = client.put("/vms/web/network", json={"network": "nat", "vlan_trunks": "30"}, headers=admin)
    assert r.status_code == 422
    r = client.put("/vms/web/network", json={"network": "lan", "vlan_trunks": "5000"}, headers=admin)
    assert r.status_code == 422 and "4094" in r.json()["detail"]
    assert api.updated == [] and api.metadata is None


def test_the_interface_list_shows_bridge_vlans(api):
    from app.routers.vms.devices import _get_interfaces

    bridge_vlan.set_port(api, "52:54:00:00:00:01", 20, "30")
    assert _get_interfaces(api)[0]["vlan"] == 20 and _get_interfaces(api)[0]["vlan_trunks"] == "30"


# --- Making a bridge VLAN-aware (part 2) ---


@pytest.fixture()
def host(monkeypatch, tmp_path):
    """A fake /sys/class/net: br0 with a bond, a NIC, a VM tap and a container veth; `ip`/`bridge` recorded."""
    sys_net = tmp_path / "sys"

    def dev(name, devtype=None, hw=False):
        d = sys_net / name
        d.mkdir(parents=True)
        (d / "uevent").write_text(f"DEVTYPE={devtype}\n" if devtype else "")
        if hw:
            (d / "device").mkdir()
        return d

    br = dev("br0", "bridge")
    (br / "bridge").mkdir()
    (br / "bridge" / "vlan_filtering").write_text("0\n")
    for name in ("bond0", "eno1", "vnet3", "veth5"):
        (br / "brif").mkdir(exist_ok=True)
        (br / "brif" / name).mkdir()
    dev("bond0", "bond")
    dev("eno1", hw=True)
    dev("vnet3")
    dev("veth5")
    monkeypatch.setattr(bridge_vlan, "SYS_NET", sys_net)
    from app.core import host_interfaces

    monkeypatch.setattr(host_interfaces, "SYS_NET", sys_net)
    calls = []
    fail = set()

    def run(args, node=None):
        calls.append(args)
        if tuple(args[:2]) in fail or (args[0] == "bridge" and args[4] in fail):
            raise bridge_vlan.VlanError("RTNETLINK answers: Operation not supported")
        if args[:2] == ["ip", "link"] and "vlan_filtering" in args:
            (sys_net / args[4] / "bridge" / "vlan_filtering").write_text(args[-1] + "\n")
        return ""

    monkeypatch.setattr(bridge_vlan, "_run", run)
    aware = tmp_path / "etc" / "local" / "vlan-aware-bridges"
    monkeypatch.setattr(bridge_vlan, "_aware_file", lambda: aware)
    yield {"calls": calls, "fail": fail, "aware": aware, "filtering": lambda: bridge_vlan.filters_vlans("br0")}
    # A trial left waiting must not reach the next test (the module keeps them, as the service does).
    with bridge_vlan._trials_lock:
        for _token, timer in bridge_vlan._trials.values():
            timer.cancel()
        bridge_vlan._trials.clear()


def test_the_uplinks_are_the_nics_bonds_and_vlans_never_the_guests_ports(host):
    assert bridge_vlan.uplinks("br0") == ["bond0", "eno1"]


def test_a_bridge_made_vlan_aware_carries_2_to_4094_on_its_uplinks_and_itself(host):
    bridge_vlan.enable("br0")
    assert host["filtering"]()
    adds = [c for c in host["calls"] if c[:3] == ["bridge", "vlan", "add"]]
    assert adds == [
        ["bridge", "vlan", "add", "dev", "bond0", "vid", "2-4094"],
        ["bridge", "vlan", "add", "dev", "eno1", "vid", "2-4094"],
        ["bridge", "vlan", "add", "dev", "br0", "vid", "2-4094", "self"],
    ], "the guests' ports stay on VLAN 1, untagged"


def test_a_bridge_that_cannot_be_made_vlan_aware_is_put_back(host):
    host["fail"].add("eno1")
    with pytest.raises(bridge_vlan.VlanError, match="br0"):
        bridge_vlan.enable("br0")
    assert not host["filtering"]()


def test_a_trial_not_confirmed_is_undone(host):
    import time

    bridge_vlan.start_trial("br0", seconds=0.2)
    assert host["filtering"]()
    with pytest.raises(bridge_vlan.VlanError, match="waiting"):
        bridge_vlan.start_trial("br0")
    time.sleep(0.6)
    assert not host["filtering"]() and not host["aware"].exists()


def test_a_trial_confirmed_with_its_token_is_kept_for_the_boot(host):
    import time

    token = bridge_vlan.start_trial("br0", seconds=0.3)
    with pytest.raises(bridge_vlan.VlanError):
        bridge_vlan.confirm_trial("br0", "not-the-token")
    bridge_vlan.confirm_trial("br0", token)
    time.sleep(0.5)
    assert host["filtering"]() and host["aware"].read_text() == "br0\n"
    with pytest.raises(bridge_vlan.VlanError, match="already"):
        bridge_vlan.start_trial("br0")


def test_the_boot_makes_the_listed_bridges_vlan_aware_again(host):
    host["aware"].parent.mkdir(parents=True)
    host["aware"].write_text("br0\nbr9\n")
    errors = bridge_vlan.boot()
    assert host["filtering"]() and errors == ["bridge br9 is missing"]


def test_only_a_bridge_hyperlite_made_vlan_aware_is_turned_off(host):
    with pytest.raises(bridge_vlan.VlanError, match="not made VLAN-aware by Hyperlite"):
        bridge_vlan.turn_off("br0")
    token = bridge_vlan.start_trial("br0")
    bridge_vlan.confirm_trial("br0", token)
    bridge_vlan.turn_off("br0")
    assert not host["filtering"]() and host["aware"].read_text() == ""


@pytest.fixture()
def net_api(monkeypatch, host):
    from app.routers import network

    domains = []

    class Net:
        def __init__(self, name, xml):
            self.name_, self.xml = name, xml

        def name(self):
            return self.name_

        def XMLDesc(self, flags=0):
            return self.xml

    nets = {
        "lan": Net("lan", "<network><name>lan</name><forward mode='bridge'/><bridge name='br0'/></network>"),
        "nat": Net("nat", "<network><name>nat</name><forward mode='nat'/><bridge name='virbr0'/></network>"),
    }

    class Conn:
        def networkLookupByName(self, name):
            if name not in nets:
                raise libvirt.libvirtError("no network")
            return nets[name]

        def listAllNetworks(self, flags=0):
            return list(nets.values())

        def listAllDomains(self, flags=0):
            return domains

        def close(self):
            pass

    monkeypatch.setattr(network, "open_conn", lambda *a, **k: Conn())
    return domains


def test_the_api_makes_a_bridge_vlan_aware_on_trial_then_keeps_it(client, auth_headers, net_api, host):
    admin = auth_headers("admin")
    r = client.post("/networks/lan/vlan-aware", headers=admin)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["bridge"] == "br0" and body["expires_in"] == 60 and host["filtering"]()
    assert client.post("/networks/lan/vlan-aware/confirm", json={"token": "x"}, headers=admin).status_code == 409
    r = client.post("/networks/lan/vlan-aware/confirm", json={"token": body["token"]}, headers=admin)
    assert r.status_code == 200 and host["aware"].read_text() == "br0\n"
    assert client.post("/networks/nat/vlan-aware", headers=admin).status_code == 422, "NAT is libvirt's own bridge"
    assert client.post("/networks/lan/vlan-aware", headers=auth_headers("eve", role="viewer")).status_code == 403


def test_the_api_keeps_a_bridge_vlan_aware_while_a_vm_has_vlans_on_it(client, auth_headers, net_api, host):
    admin = auth_headers("admin")
    token = client.post("/networks/lan/vlan-aware", headers=admin).json()["token"]
    client.post("/networks/lan/vlan-aware/confirm", json={"token": token}, headers=admin)
    dom = FakeDomain(
        ["<interface type='network'><mac address='52:54:00:00:00:01'/><source network='lan'/></interface>"],
        active=False,
    )
    dom.name = lambda: "web"
    bridge_vlan.set_port(dom, "52:54:00:00:00:01", 20, None)
    net_api.append(dom)
    r = client.delete("/networks/lan/vlan-aware", headers=admin)
    assert r.status_code == 409 and "web" in r.json()["detail"] and host["filtering"]()
    bridge_vlan.set_port(dom, "52:54:00:00:00:01", None, None)
    assert client.delete("/networks/lan/vlan-aware", headers=admin).status_code == 200
    assert not host["filtering"]()
