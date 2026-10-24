"""Load balancing's recommended moves (app/core/drs.py, docs/design/drs.md 3.1-3.3, #334 step 3), on clusters made
of statuses: what counts as unbalanced, which VM moves where, and what never moves."""

import pytest

from app.core import cluster_lead, drs, drs_rules, maintenance, statd


def vm(cpu=50.0, vcpus=2, mem=1024.0, shared=True):
    return {"cpu_pct": cpu, "vcpus": vcpus, "mem_used_mb": mem, "mem_total_mb": mem, "shared": shared}


def node(cpu, mem_used, vms, mem_total=16384.0, cpus=8, stale=False):
    host = {"cpu_pct": cpu, "cpus": cpus, "mem_used_mb": mem_used, "mem_total_mb": mem_total}
    return {"stale": stale, "load": {"host": host, "vms": vms}, "vms": [{"name": n, "state": "active"} for n in vms]}


@pytest.fixture(autouse=True)
def fresh(database, monkeypatch):
    monkeypatch.setattr(drs, "_cpu", {})
    monkeypatch.setattr(drs, "_moved", {})
    monkeypatch.setattr(
        drs, "_state", {"unbalanced": 0, "at": None, "spread": None, "moves": [], "nodes": {}, "reason": None}
    )


def test_the_view_scores_each_member_and_leaves_out_what_cannot_receive():
    table = {
        "n1": node(80.0, 4096, {}),
        "n2": node(10.0, 12288, {}),  # 75 % of its memory: memory is its score
        "n3": node(10.0, 1000, {}, stale=True),
        "n4": {"stale": False, "load": None, "vms": []},  # a node before this version
        "n5": node(5.0, 1000, {}),
    }
    v = drs.view(table, unavailable={"n5"})
    assert set(v) == {"n1", "n2"} and v["n1"]["score"] == 80.0 and v["n2"]["score"] == 75.0


def test_a_balanced_cluster_moves_nothing():
    nodes = drs.view({"n1": node(40.0, 6000, {"a": vm()}), "n2": node(35.0, 6000, {"b": vm()})})
    assert drs.recommend(nodes, 3) == []


def test_the_most_loaded_member_gives_a_vm_to_the_least_loaded():
    nodes = drs.view(
        {
            "n1": node(85.0, 8000, {"big": vm(cpu=90.0, vcpus=4, mem=4096), "small": vm(cpu=40.0, vcpus=2, mem=512)}),
            "n2": node(15.0, 4000, {}),
        }
    )
    moves = drs.recommend(nodes, 3)
    assert moves and moves[0]["vm"] == "big" and moves[0]["source"] == "n1" and moves[0]["target"] == "n2"
    assert moves[0]["gain"] >= drs.SPREAD[3] / 4 and "spread" in moves[0]["reason"]


@pytest.mark.parametrize("why", ["local disks", "excluded", "moved an hour ago", "no room"])
def test_what_never_moves(why, monkeypatch):
    the_vm = vm(cpu=90.0, vcpus=4, mem=4096, shared=why != "local disks")
    behaviours = {"big": "excluded"} if why == "excluded" else {}
    target = node(15.0, 15000 if why == "no room" else 4000, {})
    if why == "moved an hour ago":
        drs._moved["big"] = 1000.0
    nodes = drs.view({"n1": node(85.0, 8000, {"big": the_vm}), "n2": target})
    assert drs.recommend(nodes, 3, behaviours, now=1000.0 + 60) == []


def test_a_must_rule_keeps_a_vm_where_it_is_and_a_broken_rule_is_mended_first(database):
    drs_rules.save_rule({"name": "dns", "kind": "apart", "strength": "must", "vms": ["dns1", "dns2"]}, "admin")
    nodes = drs.view({"n1": node(85.0, 8000, {"dns1": vm(cpu=90.0, vcpus=4)}), "n2": node(15.0, 4000, {"dns2": vm()})})
    assert drs.recommend(nodes, 3) == []  # dns1 may not join dns2
    drs_rules.save_rule(
        {"name": "pin", "kind": "on_nodes", "strength": "should", "vms": ["web"], "nodes": ["n2"]}, "admin"
    )
    nodes = drs.view({"n1": node(40.0, 6000, {"web": vm()}), "n2": node(35.0, 6000, {})})
    moves = drs.recommend(nodes, 3)
    assert moves == [
        {"vm": "web", "source": "n1", "target": "n2", "gain": None, "reason": "rule 'pin': its VMs run on n2 only"}
    ]


def test_level_1_moves_for_rules_only_and_a_hot_member_moves_at_any_spread():
    nodes = drs.view({"n1": node(85.0, 8000, {"big": vm(cpu=90.0, vcpus=4)}), "n2": node(15.0, 4000, {})})
    assert drs.recommend(nodes, 1) == []
    hot = drs.view(
        {"n1": node(30.0, 15500, {"fat": vm(cpu=5.0, mem=3000)}), "n2": node(30.0, 10000, {})}  # 95 % vs 61 % memory
    )
    moves = drs.recommend(hot, 2)  # spread 34 < 40, but n1 is above 90 %
    assert moves and moves[0]["vm"] == "fat" and "hot" in moves[0]["reason"]


def test_moves_wait_for_an_imbalance_that_lasts(monkeypatch):
    monkeypatch.setattr(cluster_lead, "in_cluster", lambda: True)
    monkeypatch.setattr(maintenance, "list_all", lambda: [])
    table = {"n1": node(85.0, 8000, {"big": vm(cpu=90.0, vcpus=4, mem=4096)}), "n2": node(15.0, 4000, {})}
    monkeypatch.setattr(statd, "table", lambda: table)
    for _ in range(drs.SUSTAINED - 1):
        assert drs.evaluate()["moves"] == []  # a burst is no imbalance
    assert drs.evaluate()["moves"][0]["vm"] == "big"
    drs_rules.save_settings("off", 3)
    assert drs.evaluate()["moves"] == [] and drs.state()["reason"] == "off"


def test_the_cpu_is_the_mean_of_the_last_five_minutes():
    for i, cpu in enumerate((90.0, 10.0, 20.0)):
        drs.observe({"n1": node(cpu, 1000, {})}, now=1000.0 + i * 30)
    drs.observe({"n1": node(99.0, 1000, {})}, now=1000.0 + drs.CPU_WINDOW_S + 31)  # the first two are out of it
    assert drs._cpu_mean("n1", 0.0) == pytest.approx((20.0 + 99.0) / 2)


def test_whether_a_vm_is_on_shared_storage_is_asked_once_in_five_minutes(monkeypatch):
    from app.core import libvirt_utils, metrics

    asked = []
    monkeypatch.setattr(metrics, "_shared", {})
    monkeypatch.setattr(libvirt_utils, "uses_shared_storage", lambda s, d, dom: asked.append(dom) or True)

    class Domain:
        def UUIDString(self):
            return "u1"

    clock = [100.0]
    monkeypatch.setattr(metrics.time, "monotonic", lambda: clock[0])
    assert metrics._on_shared_storage(None, Domain()) and metrics._on_shared_storage(None, Domain())
    clock[0] += metrics.SHARED_EVERY_S + 1
    metrics._on_shared_storage(None, Domain())
    assert len(asked) == 2
