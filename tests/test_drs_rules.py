"""Load balancing's settings and rules (app/core/drs_rules.py, docs/design/drs.md 3.4-3.5, #334 step 2): checked
when saved, what each kind allows, "must" refused and "should" said, and HA bound by the "must" rules."""

import pytest

from app.core import drs_rules, ha, ha_recovery, statd


def test_settings_default_to_automatic_level_3_and_are_checked(database):
    assert drs_rules.settings() == {"mode": "automatic", "threshold": 3}
    assert drs_rules.save_settings("manual", 5) == {"mode": "manual", "threshold": 5}
    for mode, level in (("sometimes", 3), ("manual", 0), ("manual", 6), ("manual", "3")):
        with pytest.raises(ValueError):
            drs_rules.save_settings(mode, level)


@pytest.mark.parametrize(
    ("rule", "error"),
    [
        ({"name": "x", "kind": "together", "vms": ["a"]}, "at least two VMs"),
        ({"name": "x", "kind": "on_nodes", "vms": ["a"]}, "one VM and one node"),
        ({"name": "x", "kind": "sideways", "vms": ["a", "b"]}, "kind"),
        ({"name": "x", "kind": "apart", "strength": "maybe", "vms": ["a", "b"]}, "must or should"),
        ({"name": "bad name", "kind": "apart", "vms": ["a", "b"]}, "name"),
        ({"name": "x", "kind": "apart", "vms": ["a", "b;rm"]}, "Not a VM name"),
        ({"name": "x", "kind": "on_nodes", "vms": ["a"], "nodes": ["n 1"]}, "Not a node name"),
    ],
)
def test_a_rule_is_checked_when_saved(database, rule, error):
    with pytest.raises(ValueError, match=error):
        drs_rules.save_rule(rule, "admin")


def test_each_kind_says_what_it_breaks(database):
    drs_rules.save_rule({"name": "web-db", "kind": "together", "strength": "must", "vms": ["web", "db"]}, "admin")
    drs_rules.save_rule({"name": "dns", "kind": "apart", "strength": "must", "vms": ["dns1", "dns2"]}, "admin")
    drs_rules.save_rule(
        {"name": "lic", "kind": "on_nodes", "strength": "must", "vms": ["sql"], "nodes": ["n1"]}, "admin"
    )
    drs_rules.save_rule({"name": "slow", "kind": "not_on_nodes", "vms": ["sql"], "nodes": ["n3"]}, "admin")  # should
    where = {"db": "n2", "dns1": "n1"}
    assert drs_rules.allowed("web", "n2", where) and not drs_rules.allowed("web", "n1", where)
    assert not drs_rules.allowed("dns2", "n1", where) and drs_rules.allowed("dns2", "n2", where)
    assert drs_rules.allowed("sql", "n1", where) and not drs_rules.allowed("sql", "n2", where)
    assert drs_rules.allowed("other", "n3", where)  # in no rule
    with pytest.raises(RuntimeError, match="Rule 'web-db' \\(must\\)"):
        drs_rules.refuse_if_broken("web", "n1", where)
    # A "should" rule is said, never refused.
    drs_rules.save_rule(
        {"name": "lic", "kind": "on_nodes", "strength": "must", "vms": ["sql"], "nodes": ["n1", "n3"]}, "admin"
    )
    assert drs_rules.refuse_if_broken("sql", "n3", where) == [("slow", "its VMs never run on n3")]


def test_a_disabled_rule_binds_nothing(database):
    drs_rules.save_rule(
        {"name": "dns", "kind": "apart", "strength": "must", "vms": ["dns1", "dns2"], "enabled": False}, "admin"
    )
    assert drs_rules.allowed("dns2", "n1", {"dns1": "n1"})


def test_the_placement_is_read_from_the_statuses(monkeypatch):
    monkeypatch.setattr(
        statd,
        "table",
        lambda: {
            "n1": {"vms": [{"name": "web", "state": "active"}, {"name": "off", "state": "stopped"}]},
            "n2": {"vms": [{"name": "db", "state": "running"}]},
        },
    )
    assert drs_rules.placement() == {"web": "n1", "db": "n2"}


def _protect(database, vm, node, n):
    with database.get_conn() as db:
        db.execute(
            "INSERT INTO ha_protected_vms (vm_name, node, domain_xml, enabled_by, enabled_at) VALUES (?, ?, ?, 'admin', 'now')",
            (
                vm,
                node,
                f"<domain><name>{vm}</name><uuid>6f1c2a3e-0000-4000-8000-00000000000{n}</uuid><memory unit='MiB'>512</memory></domain>",
            ),
        )
        db.commit()


def test_ha_restarts_a_vm_only_where_it_keeps_its_must_rules(database, monkeypatch):
    # n2 is lost with dns2 and web; dns1 runs on n3, db on n1: apart keeps dns2 off n3, together sends web to n1.
    drs_rules.save_rule({"name": "dns", "kind": "apart", "strength": "must", "vms": ["dns1", "dns2"]}, "admin")
    drs_rules.save_rule({"name": "app", "kind": "together", "strength": "must", "vms": ["web", "db"]}, "admin")
    monkeypatch.setattr(drs_rules, "placement", lambda: {"dns1": "n3", "db": "n1", "dns2": "n2", "web": "n2"})
    _protect(database, "dns2", "n2", 1)
    _protect(database, "web", "n2", 2)
    online = {"n1": 1000, "n3": 9000}  # n3 has the most room: HA's first choice without rules
    policy = {"priority": "medium", "preferred_nodes": [], "auto_restarts": []}
    monkeypatch.setattr(ha_recovery, "_in_maintenance", lambda node: False)
    where = {v: n for v, n in drs_rules.placement().items() if n != "n2"}
    assert ha_recovery.candidates(ha.get_protected("dns2"), policy, online, {"n2"}, where) == ["n1"]
    assert ha_recovery.candidates(ha.get_protected("web"), policy, online, {"n2"}, where) == ["n1"]
    drs_rules.save_rule(
        {"name": "pin", "kind": "on_nodes", "strength": "must", "vms": ["web"], "nodes": ["n2"]}, "admin"
    )
    assert ha_recovery.candidates(ha.get_protected("web"), policy, online, {"n2"}, where) == []  # manual, said why
