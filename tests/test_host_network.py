"""The host's bridges, bonds and VLAN interfaces (app/core/host_network.py): the managed file, the checks against the
host and the administrator's own files, and the apply, confirm and rollback cycle with ifup/ifdown and the timer
faked."""

import json
import time

import pytest

from app.core import host_network as hn

NIC = {"type": "physical", "state": "up", "mac": "52:54:00:00:00:01", "addresses": [], "usable": True}
HOST = [
    {**NIC, "name": "eno1"},
    {**NIC, "name": "eno2"},
    {**NIC, "name": "eno3"},
    {**NIC, "name": "wlp1", "type": "wifi"},
]
KIT = {"ifupdown": True, "ifupdown2": False, "bridge": True, "bond": True, "vlan": True}
ADMIN = {"eno1": "/etc/network/interfaces"}

BOND = {"type": "bond", "name": "bond0", "slaves": ["eno2", "eno3"], "mode": "802.3ad", "hash_policy": "layer3+4"}
VLAN = {"type": "vlan", "raw_device": "bond0", "vid": 20}
BRIDGE = {"type": "bridge", "name": "vmbr1", "ports": ["bond0.20"], "address": "10.20.0.2/24", "mtu": 1500}


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A node: /etc/network with the administrator's file, Hyperlite's state directory, and ifup, ifdown and
    systemd-run recorded instead of run."""
    etc = tmp_path / "etc-network"
    (etc / "interfaces.d").mkdir(parents=True)
    (etc / "interfaces").write_text("auto lo\niface lo inet loopback\nallow-hotplug eno1\niface eno1 inet dhcp\n")
    (etc / "if-pre-up.d").mkdir()
    (etc / "if-pre-up.d" / "bridge").write_text("")
    (etc / "if-pre-up.d" / "ifenslave").write_text("")
    monkeypatch.setattr(hn, "ETC_NETWORK", etc)
    monkeypatch.setattr(hn, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(hn.host_interfaces, "list_host_interfaces", lambda: [dict(i) for i in HOST])
    monkeypatch.setattr(hn, "_used_by_networks", lambda: {})
    monkeypatch.setattr(hn, "_default_route_elsewhere", lambda managed: False)
    ran, fail = [], set()

    class Done:
        def __init__(self, argv):
            self.returncode = 1 if tuple(argv[:2]) in fail else 0
            self.stdout, self.stderr = "", "no carrier" if self.returncode else ""

    def run(argv, **kwargs):
        ran.append(list(argv))
        return Done(argv)

    monkeypatch.setattr(hn.subprocess, "run", run)
    return {"etc": etc, "ran": ran, "fail": fail}


def _ifcmds(ran):
    return [" ".join(a[:3] if a[0] == "ifdown" else a[:2]) for a in ran if a[0] in ("ifup", "ifdown")]


def test_the_file_reads_back_as_it_was_written():
    wanted = [hn.normalize(i) for i in (BOND, VLAN, BRIDGE)]
    text = hn.render(wanted)
    assert "iface vmbr1 inet static\n    address 10.20.0.2/24\n    bridge_ports bond0.20" in text
    assert "    bond-slaves eno2 eno3\n    bond-mode 802.3ad\n    bond-miimon 100\n" in text
    assert (
        "    bond-xmit-hash-policy layer3+4" in text and "iface bond0.20 inet manual\n    vlan-raw-device bond0" in text
    )
    assert (
        text.index("iface bond0 ") < text.index("iface bond0.20") < text.index("iface vmbr1")
    )  # the order they come up
    assert sorted(hn.parse(text), key=lambda i: i["name"]) == sorted(wanted, key=lambda i: i["name"])


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"type": "ovs", "name": "x"}, "Unknown interface type"),
        ({"type": "bridge", "name": "vmbr1; reboot"}, "not an interface name"),
        ({"type": "bridge", "name": "averyveryverylongname"}, "not an interface name"),
        ({"type": "bond", "name": "bond0", "slaves": []}, "at least one"),
        ({**BOND, "mode": "fastest"}, "Unknown bond mode"),
        ({"type": "vlan", "raw_device": "eno2", "vid": 4095}, "between 1 and 4094"),
        ({"type": "vlan", "raw_device": "eno2", "vid": True}, "between 1 and 4094"),
        ({**BRIDGE, "address": "10.20.0.2"}, "needs its prefix"),
        ({**BRIDGE, "address": "fd00::1/64"}, "Only IPv4"),
        ({**BRIDGE, "gateway": "10.99.0.1"}, "not in 10.20.0.2/24"),
        ({**BRIDGE, "address": "dhcp", "gateway": "10.20.0.1"}, "static address"),
        ({**BRIDGE, "mtu": 100}, "MTU"),
    ],
)
def test_a_wrong_interface_is_refused_alone(spec, message):
    with pytest.raises(hn.NetworkError, match=message):
        hn.normalize(spec)


@pytest.mark.parametrize(
    ("interfaces", "message"),
    [
        ([{"type": "bridge", "name": "vmbr1", "ports": ["eno1"]}], "eno1 is configured in .*interfaces: take it out"),
        ([{"type": "bridge", "name": "eno1"}], "Hyperlite does not change that file"),
        ([{"type": "bridge", "name": "vmbr1", "ports": ["eno9"]}], "eno9 does not exist"),
        ([{"type": "bridge", "name": "vmbr1", "ports": ["wlp1"]}], "Wi-Fi"),
        ([BOND, {"type": "bridge", "name": "vmbr1", "ports": ["eno2"]}], "eno2 is already a member of bond0"),
        ([{"type": "bridge", "name": "vmbr1"}, {"type": "bridge", "name": "vmbr1"}], "defined twice"),
        ([{"type": "bridge", "name": "eno2"}], "already exists"),
        ([BOND, {"type": "bond", "name": "bond1", "slaves": ["bond0"]}], "a bond takes network cards only"),
        (
            [{"type": "bridge", "name": "vmbr1"}, {"type": "bridge", "name": "vmbr2", "ports": ["vmbr1"]}],
            "a bridge cannot",
        ),
        ([VLAN, BOND, {"type": "vlan", "raw_device": "bond0.20", "vid": 30}], "needs the device it runs on"),
        (
            [
                {**BRIDGE, "ports": [], "gateway": "10.20.0.1"},
                {"type": "bridge", "name": "vmbr2", "address": "10.30.0.2/24", "gateway": "10.30.0.1"},
            ],
            "Only one interface can have a gateway",
        ),
    ],
)
def test_the_definition_is_checked_against_the_node(host, interfaces, message):
    with pytest.raises(hn.NetworkError, match=message):
        hn.check(interfaces, kit=KIT)


def test_a_bond_needs_ifenslave(host):
    with pytest.raises(hn.NetworkError, match="ifenslave"):
        hn.check([BOND], kit={**KIT, "bond": False})


def test_the_administrators_files_are_read_for_every_name_they_use(host):
    (host["etc"] / "interfaces.d" / "storage").write_text("auto br9\niface br9 inet manual\n    bridge_ports eno3\n")
    configured = hn.admin_configured()
    assert configured["eno1"].endswith("/interfaces") and configured["br9"].endswith("/storage")
    assert configured["eno3"].endswith("/storage")  # a port of the administrator's bridge
    assert "lo" not in configured


def test_a_change_brings_down_and_up_only_what_it_changes_and_what_sits_on_it():
    old = [hn.normalize(i) for i in (BOND, VLAN, BRIDGE)]
    new = [hn.normalize(i) for i in ({**BOND, "mode": "active-backup"}, VLAN, BRIDGE)]
    assert hn.changed(old, new) == {"bond0", "bond0.20", "vmbr1"}
    new = [hn.normalize(i) for i in (BOND, VLAN, {**BRIDGE, "mtu": 9000})]
    assert hn.changed(old, new) == {"vmbr1"}
    assert hn.changed(old, old) == set()


def test_apply_arms_the_timer_first_then_confirm_keeps_the_change(host):
    result = hn.apply([BOND, VLAN, BRIDGE], timeout_s=60)
    ran = host["ran"]
    timer = next(a for a in ran if a[0] == "systemd-run")
    assert ran.index(timer) < next(i for i, a in enumerate(ran) if a[0] == "ifup")  # armed before any change
    assert "--on-active=60s" in timer and timer[-2:] == ["rollback", result["id"]]
    assert _ifcmds(ran) == ["ifup bond0", "ifup bond0.20", "ifup vmbr1"]
    assert result["changes"] == ["bond0", "bond0.20", "vmbr1"] and "+iface vmbr1 inet static" in result["diff"]
    assert hn.pending()["id"] == result["id"] and hn.managed_path().read_text() == hn.render(hn.current())
    with pytest.raises(hn.NetworkError, match="waiting for its confirmation"):
        hn.apply([BOND], timeout_s=60)
    assert hn.confirm(result["id"]) == {"confirmed": result["id"]}
    assert hn.pending() is None and ["systemctl", "stop", "hyperlite-network-rollback.timer"] in ran
    assert {i["name"] for i in hn.current()} == {"bond0", "bond0.20", "vmbr1"}


def test_the_timer_puts_the_previous_configuration_back(host):
    hn.apply([BOND], timeout_s=60)
    hn.confirm(hn.pending()["id"])
    before = hn.managed_path().read_text()
    change = hn.apply([BOND, VLAN, BRIDGE], timeout_s=30)
    host["ran"].clear()
    assert hn.main(["rollback", change["id"]]) == 0  # what the timer runs
    assert hn.managed_path().read_text() == before and hn.pending() is None
    assert _ifcmds(host["ran"]) == ["ifdown --force vmbr1", "ifdown --force bond0.20"]  # bond0 did not change


def test_rolling_back_the_first_change_removes_the_file(host):
    change = hn.apply([{"type": "bridge", "name": "vmbr1"}], timeout_s=30)
    hn.rollback(change["id"])
    assert not hn.managed_path().exists()


def test_an_interface_that_does_not_come_up_rolls_back_at_once(host):
    host["fail"].add(("ifup", "vmbr1"))
    with pytest.raises(hn.NetworkError, match=r"previous configuration is back.*vmbr1: no carrier"):
        hn.apply([BOND, VLAN, BRIDGE], timeout_s=60)
    assert hn.pending() is None and not hn.managed_path().exists()


def test_a_reboot_before_the_confirmation_rolls_back_at_the_daemons_start(host, monkeypatch):
    change = hn.apply([{"type": "bridge", "name": "vmbr1"}], timeout_s=30)
    hn.finish_interrupted()  # still in time: kept
    assert hn.pending()["id"] == change["id"]
    monkeypatch.setattr(hn.time, "time", lambda: time.time_ns() / 1e9 + 3600)
    hn.finish_interrupted()
    assert hn.pending() is None and not hn.managed_path().exists()


def test_a_bridge_a_network_uses_is_not_deleted(host, monkeypatch):
    change = hn.apply([{"type": "bridge", "name": "vmbr1"}], timeout_s=30)
    hn.confirm(change["id"])
    monkeypatch.setattr(hn, "_used_by_networks", lambda: {"vmbr1": "lan"})
    with pytest.raises(hn.NetworkError, match="used by the network lan"):
        hn.apply([], timeout_s=30)
    monkeypatch.setattr(hn, "_used_by_networks", lambda: {})
    hn.confirm(hn.apply([], timeout_s=30)["id"])
    assert not hn.managed_path().exists()  # nothing managed: no file, as before the first change


def test_the_pending_change_keeps_the_previous_file_private(host):
    hn.apply([{"type": "bridge", "name": "vmbr1"}], timeout_s=30)
    path = hn.STATE_DIR / "pending.json"
    assert path.stat().st_mode & 0o777 == 0o600 and hn.STATE_DIR.stat().st_mode & 0o777 == 0o700
    assert json.loads(path.read_text())["names"] == ["vmbr1"]


def test_the_routes_ask_the_daemon_as_the_administrator(client, auth_headers, monkeypatch):
    from app.core import privileged

    asked = []

    def run(op, user=None, **args):
        asked.append((op, user, args))
        return {"id": "0123456789abcdef", "changes": ["vmbr1"], "diff": "", "deadline": 1}

    monkeypatch.setattr(privileged, "run", run)
    admin = auth_headers("root")
    assert client.get("/host/network", headers=admin).status_code == 200
    body = {"interfaces": [{"type": "bridge", "name": "vmbr1"}], "timeout_s": 45}
    assert client.post("/host/network/preview", json=body, headers=admin).status_code == 200
    assert client.post("/host/network/apply", json=body, headers=admin).status_code == 200
    assert client.post("/host/network/confirm", json={"id": "0123456789abcdef"}, headers=admin).status_code == 200
    assert client.post("/host/network/rollback", json={"id": "nope"}, headers=admin).status_code == 422
    assert [a[0] for a in asked] == [
        "host.network_state",
        "host.network_preview",
        "host.network_apply",
        "host.network_confirm",
    ]
    assert asked[2][2] == {"interfaces": body["interfaces"], "timeout_s": 45}
    viewer = auth_headers("eve", role="viewer")
    assert client.get("/host/network", headers=viewer).status_code == 403


class _Net:
    def __init__(self, name, xml):
        self._name, self._xml = name, xml

    def name(self):
        return self._name

    def XMLDesc(self, flags):
        return self._xml


class _Conn:
    def __init__(self, nets):
        self.nets = nets

    def listAllNetworks(self):
        return self.nets


NETS = [
    _Net("default", "<network><forward mode='nat'/><bridge name='virbr0'/></network>"),
    _Net("isolated", "<network><bridge name='virbr1'/></network>"),
    _Net("lan", "<network><forward mode='bridge'/><bridge name='vmbr1'/></network>"),
    _Net("direct", "<network><forward mode='bridge'><interface dev='bond0'/></forward></network>"),
]


def test_the_networks_on_an_interface_are_read_from_the_daemons_libvirt(daemon_libvirt):
    daemon_libvirt(_Conn(NETS))
    assert hn._used_by_networks() == {"virbr0": "default", "virbr1": "isolated", "vmbr1": "lan", "bond0": "direct"}
