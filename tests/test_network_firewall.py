"""The network firewall (iptables, FORWARD chain, as root): the rules built, their order, and what is replayed
after a reboot. iptables itself is replaced by a recorder."""

import json

import pytest

from app.core import network_firewall as fw


class FakeIptables:
    def __init__(self):
        self.chains = {"FORWARD": []}
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        op, chain, rest = args[0], args[1] if len(args) > 1 else None, list(args[2:])

        class R:
            returncode = 0
            stderr = ""

        r = R()
        if op == "-nL":
            r.returncode = 0 if chain in self.chains else 1
        elif op == "-N":
            self.chains.setdefault(chain, [])
        elif op == "-F":
            self.chains[chain] = []
        elif op == "-X":
            self.chains.pop(chain, None)
        elif op == "-A":
            self.chains[chain].append(rest)
        elif op == "-I":
            self.chains[chain].insert(int(rest[0]) - 1, rest[1:])
        elif op == "-C":
            r.returncode = 0 if rest in self.chains.get(chain, []) else 1
        elif op == "-D":
            self.chains[chain].remove(rest)
        return r


class Net:
    def XMLDesc(self, *_):
        return "<network><name>lab</name><bridge name='virbr7'/></network>"


class Conn:
    def networkLookupByName(self, name):
        if name != "lab":
            raise fw.libvirt.libvirtError("no network")
        return Net()


@pytest.fixture()
def iptables(monkeypatch):
    fake = FakeIptables()
    monkeypatch.setattr(fw, "_run", fake)
    return fake


CONFIG = {
    "default_policy": "drop",
    "rules": [
        {"direction": "in", "protocol": "tcp", "port": 22, "action": "accept"},
        {"direction": "out", "protocol": "all", "action": "accept"},
    ],
}


def test_rules_are_stateful_ordered_and_end_with_the_default_policy():
    specs = fw._build_rule_specs("virbr7", CONFIG)
    assert specs[0][-1] == "ACCEPT" and "ESTABLISHED,RELATED" in specs[0]  # replies first
    assert ["-o", "virbr7", "-p", "tcp", "--dport", "22", "-j", "ACCEPT"] in specs  # "in" = towards the VMs
    assert ["-i", "virbr7", "-j", "ACCEPT"] in specs[:-2]  # "out" = from the VMs, all protocols
    assert specs[-2:] == [["-i", "virbr7", "-j", "DROP"], ["-o", "virbr7", "-j", "DROP"]]


def test_applying_puts_our_chain_first_in_forward_and_rebuilds_it(iptables, database):
    iptables.chains["FORWARD"] = [["-j", "LIBVIRT_FWX"]]
    result = fw.apply_network_firewall(Conn(), "lab", CONFIG)
    assert result == {"pont": "virbr7", "regles_appliquees": 2}
    assert iptables.chains["FORWARD"][0] == ["-j", fw.UMBRELLA_CHAIN]  # evaluated before libvirt's chains
    chain = fw._chain_name("lab")
    assert iptables.chains[chain] == fw._build_rule_specs("virbr7", CONFIG)
    # Applying again replaces the rules (flush), never appends a second copy.
    fw.apply_network_firewall(Conn(), "lab", CONFIG)
    assert iptables.chains[chain] == fw._build_rule_specs("virbr7", CONFIG)
    assert iptables.chains["FORWARD"].count(["-j", fw.UMBRELLA_CHAIN]) == 1
    assert iptables.chains[fw.UMBRELLA_CHAIN].count(["-i", "virbr7", "-j", chain]) == 1


def test_the_configuration_is_stored_and_replayed_after_a_reboot(iptables, database):
    fw.apply_network_firewall(Conn(), "lab", CONFIG)
    assert fw.get_network_firewall("lab") == {"default_policy": "drop", "rules": CONFIG["rules"]}
    iptables.chains = {"FORWARD": []}  # a reboot: iptables starts empty
    fw.reapply_all(Conn())
    assert iptables.chains[fw._chain_name("lab")] == fw._build_rule_specs("virbr7", CONFIG)


def test_a_rule_iptables_refuses_is_an_error(iptables, database, monkeypatch):
    real = iptables.__call__

    def refuse_port(*args):
        r = real(*args)
        if "--dport" in args:
            r.returncode, r.stderr = 2, "bad port"
        return r

    monkeypatch.setattr(fw, "_run", refuse_port)
    with pytest.raises(RuntimeError, match="refused a rule"):
        fw.apply_network_firewall(Conn(), "lab", CONFIG)


def test_removal_leaves_no_chain_nor_jump(iptables, database):
    fw.apply_network_firewall(Conn(), "lab", CONFIG)
    fw.remove_network_firewall(Conn(), "lab")
    assert fw._chain_name("lab") not in iptables.chains
    assert iptables.chains[fw.UMBRELLA_CHAIN] == []
    assert fw.get_network_firewall("lab")["rules"] == []


def test_chain_names_are_distinct_and_within_the_iptables_limit():
    a, b = fw._chain_name("a-very-long-network-name-1"), fw._chain_name("a-very-long-network-name-2")
    assert a != b and len(a) <= 28


def test_an_unknown_network_is_refused(iptables, database):
    with pytest.raises(ValueError):
        fw.apply_network_firewall(Conn(), "nope", CONFIG)
    assert json.loads(json.dumps(fw.get_network_firewall("nope")))["default_policy"] == "accept"
