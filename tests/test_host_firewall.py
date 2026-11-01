"""The nodes' own firewall (app/core/host_firewall.py): the document's checks, the nftables script, and the apply,
confirm, rollback and follow cycle with nft and the timer recorded instead of run."""

import json
import time

import pytest

from app.core import host_firewall as fw

ADMINS = {"name": "admins", "cidrs": ["192.168.1.0/24", "10.8.0.5"]}
WEB = {"name": "web", "rules": [{"action": "accept", "protocol": "tcp", "port": "80,443"}]}
DOC = {
    "enabled": True,
    "policy": "drop",
    "ipsets": [ADMINS],
    "groups": [WEB],
    "rules": [
        {"action": "accept", "protocol": "tcp", "port": "8000"},
        {"group": "web"},
        {"action": "accept", "protocol": "tcp", "port": "5900-5999", "source": "+admins", "comment": "VNC"},
        {"action": "accept", "protocol": "tcp", "port": "9", "enabled": False},
    ],
    "nodes": {"pve-a": {"rules": [{"action": "reject", "protocol": "udp", "port": "161", "source": "10.0.0.0/8"}]}},
}


@pytest.fixture
def node(tmp_path, monkeypatch):
    """This node: nft and systemd-run recorded, the setting kept in memory, two members and one NAT bridge."""
    monkeypatch.setattr(fw, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(fw, "_node", lambda: "pve-a")
    monkeypatch.setattr(fw, "_members", lambda: ["192.0.2.11", "192.0.2.12"])
    monkeypatch.setattr(fw, "_vm_bridges", lambda: ["virbr0"])
    ran, refuse, table = [], [], {"text": ""}
    saved = {"value": None}

    class Done:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode, self.stdout, self.stderr = returncode, stdout, stderr

    def run(argv, input=None, **kwargs):
        ran.append((list(argv), input))
        if argv[:2] == ["nft", "list"]:
            return Done(0 if table["text"] else 1, table["text"])
        if argv[0] == "nft":
            if refuse:
                return Done(1, "", refuse[0])
            if argv[1] != "-c":
                table["text"] = input.split(f"delete table inet {fw.TABLE}\n", 1)[1]
        return Done()

    monkeypatch.setattr(fw.subprocess, "run", run)
    synced = []
    monkeypatch.setattr(fw, "sync_groups", lambda doc: synced.append([g["name"] for g in doc["groups"]]))
    monkeypatch.setattr(
        fw, "saved", lambda: fw.normalize(json.loads(saved["value"])) if saved["value"] else dict(fw.DEFAULT)
    )
    return {"ran": ran, "refuse": refuse, "table": table, "saved": saved, "synced": synced}


def test_the_script_pins_what_keeps_the_cluster_and_the_vms_working_then_the_rules():
    doc = fw.normalize(DOC)
    script = fw.render(doc, "pve-a", members=["192.0.2.11"], vm_bridges=["virbr0"])
    assert script.startswith("table inet hyperlite {}\ndelete table inet hyperlite\n")  # one transaction, from scratch
    lines = [line.strip() for line in script.splitlines()]
    order = [
        "type filter hook input priority filter; policy drop;",
        'iif "lo" accept comment "pinned: loopback"',
        'ip saddr @members accept comment "pinned: the cluster\'s members"',
        'iifname { "virbr0" } udp dport { 53, 67 } accept comment "pinned: DHCP and DNS for the VMs"',
        "tcp dport { 8000 } accept",
        "tcp dport { 80, 443 } accept",  # the group, where it is referenced
        'ip saddr @ipset_admins tcp dport { 5900-5999 } accept comment "VNC"',
        "ip saddr 10.0.0.0/8 udp dport { 161 } reject",  # this node's own, after the cluster's
    ]
    assert [lines.index(line) for line in order] == sorted(lines.index(line) for line in order)
    assert "set ipset_admins { type ipv4_addr; flags interval; elements = { 10.8.0.5/32, 192.168.1.0/24 } }" in lines
    assert "dport { 9 }" not in script  # a disabled rule
    other = fw.render(doc, "pve-b", members=[], vm_bridges=[])
    assert "udp dport { 161 }" not in other and "virbr0" not in other


def test_turned_off_the_table_is_removed():
    assert (
        fw.render(fw.normalize({"enabled": False}), "pve-a") == "table inet hyperlite {}\ndelete table inet hyperlite\n"
    )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"rules": [{"action": "allow"}]}, "accept, drop or reject"),
        ({"rules": [{"action": "accept", "protocol": "sctp"}]}, "tcp, udp, icmp or all"),
        ({"rules": [{"action": "accept", "protocol": "icmp", "port": "22"}]}, "a port goes with tcp or udp"),
        ({"rules": [{"action": "accept", "protocol": "tcp", "port": "22; drop"}]}, "is not a port"),
        ({"rules": [{"action": "accept", "protocol": "tcp", "port": "70000"}]}, "between 1 and 65535"),
        ({"rules": [{"action": "accept", "protocol": "tcp", "port": "90-80"}]}, "between 1 and 65535"),
        ({"rules": [{"action": "accept", "source": "+nope"}]}, "IP set « nope » does not exist"),
        ({"rules": [{"action": "accept", "source": "fd00::/8"}]}, "only IPv4"),
        ({"rules": [{"action": "accept", "source": "a.b.c.d"}]}, "not an address"),
        ({"rules": [{"group": "nope"}]}, "security group « nope » does not exist"),
        ({"groups": [WEB, WEB]}, "not a free security group name"),
        ({"groups": [{"name": "g", "rules": [{"group": "web"}]}, WEB]}, "cannot include another"),
        ({"ipsets": [{"name": "bad name", "cidrs": []}]}, "not a free IP set name"),
        ({"ipsets": [{"name": "s", "cidrs": ["any"]}]}, "not an address or a network"),
        ({"policy": "reject"}, "accept or drop"),
        ({"nodes": {"pve a": {"rules": []}}}, "not a node name"),
        ({"rules": [{"action": "accept"}] * 201}, "At most 200"),
    ],
)
def test_a_wrong_document_is_refused(change, message):
    with pytest.raises(fw.FirewallError, match=message):
        fw.normalize({**DOC, **change})


def test_where_a_group_or_an_ip_set_is_used():
    doc = fw.normalize({**DOC, "groups": [WEB, {"name": "ssh", "rules": [{"action": "accept", "source": "+admins"}]}]})
    assert fw.used(doc, "web", "group") == ["cluster"]
    assert fw.used(doc, "admins", "ipset") == ["cluster", "group ssh"]


def test_apply_arms_the_timer_first_and_confirm_hands_the_document_to_save(node):
    result = fw.apply(DOC, 60)
    argvs = [a for a, _ in node["ran"]]
    timer = next(i for i, a in enumerate(argvs) if a[0] == "systemd-run")
    applied = next(i for i, a in enumerate(argvs) if a[:3] == ["nft", "-f", "-"])
    assert timer < applied and argvs[timer][-2:] == ["rollback", result["id"]]
    assert ["nft", "-c", "-f", "-"] in argvs[:applied]  # checked before anything changes
    assert "ipset_admins" in node["table"]["text"]
    with pytest.raises(fw.FirewallError, match="waiting for its confirmation"):
        fw.apply(DOC, 60)
    assert fw.follow_once() is False  # the saved rules wait while a change is pending here
    confirmed = fw.confirm(result["id"])
    assert confirmed["document"] == fw.normalize(DOC) and fw.pending() is None


def test_the_timer_puts_the_previous_rules_back(node):
    node["table"]["text"] = "table inet hyperlite {\n  chain input { }\n}\n"
    change = fw.apply(DOC, 30)
    assert fw.main(["rollback", change["id"]]) == 0
    assert node["table"]["text"] == "table inet hyperlite {\n  chain input { }\n}\n" and fw.pending() is None


def test_rules_nftables_refuses_change_nothing(node):
    node["refuse"].append("Error: syntax error")
    with pytest.raises(fw.FirewallError, match="nftables refused the ruleset: Error: syntax error"):
        fw.apply(DOC, 60)
    assert fw.pending() is None and not any(a[0] == "systemd-run" for a, _ in node["ran"])


def test_every_member_follows_the_saved_rules_once(node):
    assert fw.follow_once() is False  # never turned on: nothing to remove
    node["saved"]["value"] = json.dumps(DOC)
    assert fw.follow_once() is True and "ipset_admins" in node["table"]["text"]
    assert fw.follow_once() is False  # unchanged since
    node["saved"]["value"] = json.dumps({**DOC, "enabled": False})
    assert fw.follow_once() is True and node["table"]["text"] == ""


def test_a_reboot_before_the_confirmation_drops_the_change(node, monkeypatch):
    change = fw.apply(DOC, 30)
    fw.finish_interrupted()
    assert fw.pending()["id"] == change["id"]
    monkeypatch.setattr(fw.time, "time", lambda: time.time_ns() / 1e9 + 3600)
    fw.finish_interrupted()
    assert fw.pending() is None


def test_confirm_saves_the_document_for_the_cluster(client, auth_headers, monkeypatch):
    from app.core import privileged
    from app.repositories import registry

    asked = []

    def run(op, user=None, **args):
        asked.append(op)
        if op == "host.firewall_apply":
            return {"id": "0123456789abcdef", "deadline": 1}
        if op == "host.firewall_confirm":
            return {"confirmed": args["id"], "document": fw.normalize(DOC)}
        return {}

    monkeypatch.setattr(privileged, "run", run)
    admin = auth_headers("root")
    assert client.post("/cluster/firewall/apply", json={"document": DOC}, headers=admin).status_code == 200
    assert registry.settings().sync.app_setting(fw.SETTING) is None  # not before the confirmation
    assert client.post("/cluster/firewall/confirm", json={"id": "0123456789abcdef"}, headers=admin).status_code == 200
    assert json.loads(registry.settings().sync.app_setting(fw.SETTING)) == fw.normalize(DOC)
    assert asked == ["host.firewall_apply", "host.firewall_confirm"]
    assert client.get("/cluster/firewall", headers=auth_headers("eve", role="viewer")).status_code == 403


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


def test_the_vms_bridges_are_read_from_the_daemons_libvirt(daemon_libvirt):
    """The NAT, routed and isolated networks' bridges (their dnsmasq runs on the host), not a bridged one's."""
    daemon_libvirt(_Conn(NETS))
    assert fw._vm_bridges() == ["virbr0", "virbr1"]


def test_the_groups_are_synced_when_they_change_only(node):
    node["saved"]["value"] = json.dumps(DOC)
    fw.follow_once()
    fw.follow_once()
    assert node["synced"] == [["web"]]
    node["saved"]["value"] = json.dumps({**DOC, "groups": [WEB, {"name": "db", "rules": []}]})
    fw.follow_once()
    assert node["synced"] == [["web"], ["web", "db"]]


def test_a_group_is_a_vm_filter_of_incoming_rules_before_the_vms_own():
    import xml.etree.ElementTree as ET

    doc = fw.normalize(
        {
            "ipsets": [ADMINS],
            "groups": [
                {
                    "name": "admin",
                    "rules": [
                        {"action": "accept", "protocol": "tcp", "port": "22,8000-8010", "source": "+admins"},
                        {"action": "drop", "protocol": "all", "enabled": False},
                        {"action": "reject", "protocol": "icmp", "source": "10.0.0.0/8"},
                    ],
                }
            ],
        }
    )
    root = ET.fromstring(fw.group_filter_xml(doc, "admin"))
    assert root.get("name") == "hyperlite-group-admin"
    rules = [(r.get("action"), r.get("direction"), int(r.get("priority")), r[0].tag, dict(r[0].attrib)) for r in root]
    one = {"srcipaddr": "10.8.0.5", "srcipmask": "32"}
    lan = {"srcipaddr": "192.168.1.0", "srcipmask": "24"}
    assert rules == [
        ("accept", "in", 100, "tcp", {**one, "dstportstart": "22", "dstportend": "22"}),
        ("accept", "in", 101, "tcp", {**one, "dstportstart": "8000", "dstportend": "8010"}),
        ("accept", "in", 102, "tcp", {**lan, "dstportstart": "22", "dstportend": "22"}),
        ("accept", "in", 103, "tcp", {**lan, "dstportstart": "8000", "dstportend": "8010"}),
        ("reject", "in", 104, "icmp", {"srcipaddr": "10.0.0.0", "srcipmask": "8"}),
    ]


def test_a_vms_filter_references_its_groups_and_reads_them_back():
    from app.core import vm_firewall

    rule = {"action": "accept", "direction": "in", "protocol": "tcp", "port": 80}
    config = {"default_policy": "drop", "rules": [rule], "groups": ["web", "admin"]}
    xml = vm_firewall.filter_xml("web1", config)
    assert 'filterref filter="hyperlite-group-web"' in xml
    assert vm_firewall.parse(xml) == config


def test_a_vm_or_network_firewall_refuses_an_unknown_group(client, auth_headers, monkeypatch):
    monkeypatch.setattr(fw, "saved", lambda: fw.normalize({"groups": [WEB]}))
    body = {"default_policy": "accept", "rules": [], "groups": ["web", "nope"]}
    r = client.put("/networks/lab/firewall", json=body, headers=auth_headers("root"))
    assert r.status_code == 422 and "Unknown security group: nope" in r.json()["detail"]
    groups = client.get("/cluster/firewall/groups", headers=auth_headers("eve", role="viewer"))
    assert groups.status_code == 200 and [g["name"] for g in groups.json()] == ["web"]


def test_a_deleted_group_a_vm_still_references_is_kept(daemon_libvirt, monkeypatch):
    """libvirt lets a filter another references be undefined: a stopped VM would then not start again."""
    from app.core import network_firewall

    class Filter:
        def __init__(self, name, xml=""):
            self._name, self._xml, self.gone = name, xml, False

        def name(self):
            return self._name

        def XMLDesc(self, flags):
            return self._xml

        def undefine(self):
            self.gone = True

    used = Filter("hyperlite-group-old")
    unused = Filter("hyperlite-group-older")
    vm = Filter("hyperlite-vm-web1", "<filter><filterref filter='hyperlite-group-old'/></filter>")

    class Conn:
        def __init__(self):
            self.defined = []

        def nwfilterDefineXML(self, xml):
            self.defined.append(xml)

        def listAllNWFilters(self):
            return [used, unused, vm]

    conn = daemon_libvirt(Conn())
    monkeypatch.setattr(network_firewall, "reapply_local", lambda: None)
    fw.sync_groups(fw.normalize({"groups": [WEB]}))
    assert not used.gone and unused.gone and not vm.gone
    assert len(conn.defined) == 1 and "hyperlite-group-web" in conn.defined[0]
