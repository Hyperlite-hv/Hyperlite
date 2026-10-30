"""Load balancing's placement (app/core/drs.py, docs/design/drs.md 3.6, #334 step 5): a drained member's VMs spread
over the others, the largest first and their rules kept, and the member a new VM goes to on "auto"."""

import pytest

from app.core import cluster_view, drs, drs_rules, self_node, statd


def vm(mem=1024.0, cpu=10.0, vcpus=2):
    return {"cpu_pct": cpu, "vcpus": vcpus, "mem_used_mb": mem, "mem_total_mb": mem, "shared": True}


def node(cpu, mem_used, vms=None, mem_total=16384.0, cpus=8):
    vms = vms or {}
    host = {"cpu_pct": cpu, "cpus": cpus, "mem_used_mb": mem_used, "mem_total_mb": mem_total}
    return {"stale": False, "load": {"host": host, "vms": vms}, "vms": [{"name": n, "state": "active"} for n in vms]}


@pytest.fixture()
def table(database, monkeypatch):
    monkeypatch.setattr(drs, "_cpu", {})
    t = {}
    monkeypatch.setattr(statd, "table", lambda: t)
    return t


def test_a_drained_member_spreads_its_vms_largest_first_over_the_least_loaded(table):
    table.update(
        n1=node(50.0, 9000, {"big": vm(6000), "a": vm(1024), "b": vm(1024)}),
        n2=node(20.0, 4000),  # 24 %, the least loaded: it takes the largest
        n3=node(30.0, 6000),
    )
    placed = drs.spread("n1", ["big", "a", "b"])
    assert placed["big"] == "n2"  # n2 then at 61 %: a goes to n3, and b to n3 again (43 % < 61 %)
    assert placed == {"big": "n2", "a": "n3", "b": "n3"}


def test_a_drain_keeps_the_rules_and_leaves_a_vm_no_member_can_take(table):
    drs_rules.save_rule({"name": "dns", "kind": "apart", "strength": "must", "vms": ["dns1", "dns2"]}, "admin")
    table.update(
        n1=node(50.0, 9000, {"dns1": vm(), "huge": vm(15000)}),
        n2=node(10.0, 2000, {"dns2": vm()}),
        n3=node(60.0, 8000),
    )
    assert drs.spread("n1", ["dns1", "huge"]) == {"dns1": "n3", "huge": None}


def test_a_should_rule_gives_way_when_no_member_keeps_it(table):
    drs_rules.save_rule(
        {"name": "pin", "kind": "on_nodes", "strength": "should", "vms": ["web"], "nodes": ["n1"]}, "admin"
    )
    table.update(n1=node(50.0, 9000, {"web": vm()}), n2=node(10.0, 2000))
    assert drs.spread("n1", ["web"]) == {"web": "n2"}


def test_a_member_in_maintenance_takes_nothing(table):
    from app.core import maintenance

    table.update(n1=node(50.0, 9000, {"a": vm()}), n2=node(10.0, 2000), n3=node(40.0, 8000))
    maintenance.enter("n2", "admin")
    assert drs.spread("n1", ["a"]) == {"a": "n3"}
    assert drs.initial("new", 1024, 2) == ["n3", "n1"]


def test_a_new_vm_goes_where_it_leaves_the_lowest_score_and_keeps_its_rules(table):
    table.update(n1=node(10.0, 12000), n2=node(40.0, 4000), n3=node(20.0, 15800))
    assert drs.initial("new", 2048, 2) == ["n2", "n1"]  # n3 has no room for it
    drs_rules.save_rule(
        {"name": "lic", "kind": "on_nodes", "strength": "must", "vms": ["new"], "nodes": ["n1"]}, "admin"
    )
    assert drs.initial("new", 2048, 2) == ["n1"]


def test_the_placement_api_skips_a_member_without_the_network(client, auth_headers, table, monkeypatch):
    table.update(n1=node(80.0, 12000), n2=node(10.0, 2000), n3=node(30.0, 6000))
    monkeypatch.setattr(self_node, "name", lambda: "n1")
    monkeypatch.setattr(self_node, "is_self", lambda n: n == "n1")
    monkeypatch.setattr(cluster_view, "peer", lambda n: {"name": n})
    asked = []

    def forward(member, username, method, path):
        asked.append((member["name"], path))
        return (404, {}) if member["name"] == "n2" and path.startswith("/networks/") else (200, {})

    monkeypatch.setattr(cluster_view, "forward", forward)
    user = auth_headers("eve", "viewer")
    r = client.get("/drs/placement", params={"memory_mb": 1024, "vcpus": 2, "network": "lan 2"}, headers=user)
    assert r.status_code == 200, r.text
    assert r.json() == {"node": "n3", "local": False, "skipped": [{"node": "n2", "reason": "no network 'lan 2'"}]}
    assert ("n2", "/networks/lan%202") in asked
    r = client.get("/drs/placement", params={"memory_mb": 1024, "network": "lan 2"}, headers=user)
    assert r.json()["node"] == "n3"
    table.pop("n3")
    r = client.get("/drs/placement", params={"memory_mb": 1024, "network": "lan 2"}, headers=user)
    assert r.json() == {"node": "n1", "local": True, "skipped": [{"node": "n2", "reason": "no network 'lan 2'"}]}
    table.pop("n1")
    r = client.get("/drs/placement", params={"memory_mb": 1024, "network": "lan 2"}, headers=user)
    assert r.status_code == 409 and "n2: no network 'lan 2'" in r.json()["detail"]
    assert client.get("/drs/placement", params={"memory_mb": 0}, headers=user).status_code == 422
