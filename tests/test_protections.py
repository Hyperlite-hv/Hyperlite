"""Protections, envelopes and guardrails (docs/design/access-2.md, sections 4 and 5)."""

import pytest

from app.core import guardrails, permissions, protections, self_node


@pytest.fixture()
def here(monkeypatch):
    monkeypatch.setattr(self_node, "name", lambda: "node-a")
    return "node-a"


def test_keep_refuses_deletions_only(database, here):
    protections.put("vm", "db1", "keep", "production database", "root")
    assert "kept from deletion" in protections.request_refusal("DELETE", "/api/v1/vms/db1")
    assert protections.request_refusal("DELETE", "/api/v1/vms/db1/snapshots/before") is not None
    assert protections.request_refusal("DELETE", "/api/v1/vms/db1/disks/vdb") is not None
    assert protections.request_refusal("POST", "/api/v1/vms/db1/start") is None
    assert protections.request_refusal("DELETE", "/api/v1/vms/other") is None


def test_freeze_refuses_every_change_but_the_consoles(database, here):
    protections.put("container", "ct1", "freeze", "", "root")
    assert "frozen" in protections.request_refusal("POST", "/api/v1/containers/ct1/start")
    assert protections.request_refusal("GET", "/api/v1/containers/ct1") is None
    protections.put("vm", "vm1", "freeze", "", "root")
    assert protections.request_refusal("PATCH", "/api/v1/vms/vm1") is not None
    assert protections.request_refusal("POST", "/api/v1/vms/vm1/console-ticket") is None


def test_protections_are_inherited_from_pools_and_the_node(database, here):
    pool = permissions.create_pool("prod")
    permissions.add_pool_member(pool, "web1")
    protections.put("pool", str(pool), "keep", "", "root")
    assert "from its pool" in protections.refusal("vm", "web1", deleting=True)
    assert protections.refusal("vm", "web2", deleting=True) is None
    protections.put("node", here, "freeze", "maintenance window", "root")
    assert "maintenance window" in protections.refusal("container", "ct9", deleting=False)
    assert protections.lift("node", here, "freeze") is True
    assert protections.lift("node", here, "freeze") is False


def test_protected_vm_is_refused_through_the_api(client, auth_headers, here):
    admin = auth_headers("root", "admin")
    assert client.put("/protections/vm/db1/keep", json={"reason": "prod"}, headers=admin).status_code == 200
    answer = client.delete("/vms/db1", headers=admin)
    assert answer.status_code == 423 and answer.json()["code"] == "protected"
    held = client.get("/protections/vm/db1", headers=admin).json()["items"]
    assert [(p["level"], p["reason"]) for p in held] == [("keep", "prod")]
    assert client.delete("/protections/vm/db1/keep", headers=admin).status_code == 200
    assert client.delete("/vms/db1", headers=admin).status_code != 423


def test_who_may_protect(client, auth_headers, here):
    pool = permissions.create_pool("team")
    permissions.create_acl("user", "lead", "steward", "pool", str(pool))
    lead = auth_headers("lead", "viewer")
    assert client.put(f"/protections/pool/{pool}/keep", json={}, headers=lead).status_code == 200
    assert client.put("/protections/node/node-a/freeze", json={}, headers=lead).status_code == 403
    assert client.put("/protections/vm/x/keep", json={}, headers=auth_headers("bob", "viewer")).status_code == 403


def test_pool_protection_holds_on_the_pool_itself(client, auth_headers, here):
    pool = permissions.create_pool("frozen")
    protections.put("pool", str(pool), "freeze", "", "root")
    admin = auth_headers("root", "admin")
    assert client.post(f"/pools/{pool}/members", json={"vm_name": "x"}, headers=admin).status_code == 423
    protections.lift("pool", str(pool), "freeze")
    protections.put("pool", str(pool), "keep", "", "root")
    assert client.delete(f"/pools/{pool}", headers=admin).status_code == 423


def test_guardrails(database):
    guardrails.set_rule("cluster", {"name_pattern": "[a-z]+-[0-9]+", "max_vcpu": 8, "networks": ["lan"]}, "root")
    check = guardrails.creation_refusal
    assert check("web-1", 4, 2048, 20, ["default"], ["lan"]) is None
    assert "pattern" in check("Web", 4, 2048, 20, ["default"], ["lan"])
    assert "8 at most" in check("web-1", 16, 2048, 20, ["default"], ["lan"])
    assert "network 'wan'" in check("web-1", 2, 2048, 20, ["default"], ["wan"])
    pool = permissions.create_pool("small")
    guardrails.set_rule(f"pool:{pool}", {"max_memory_mb": 1024}, "root")
    assert "1024 MB" in check("web-1", 2, 2048, 20, ["default"], ["lan"], pool)
    with pytest.raises(ValueError):
        guardrails.set_rule("cluster", {"name_pattern": "("}, "root")
    guardrails.set_rule("cluster", {}, "root")  # nothing left: the rule goes
    assert "cluster" not in guardrails.rules()


def test_envelopes(database, monkeypatch):
    pool = permissions.create_pool("team")
    monkeypatch.setattr(
        guardrails, "usage", lambda p: {"vms": 3, "vcpus": 6, "memory_mb": 6144, "disk_gb": 60, "measured": 3}
    )
    assert guardrails.envelope_refusal(pool, vms=1) is None  # no envelope
    guardrails.set_envelope(pool, {"max_vms": 4, "max_vcpus": 8}, "root")
    assert guardrails.envelope_refusal(pool, vms=1, vcpus=2) is None
    assert "5 VMs for 4" in guardrails.envelope_refusal(pool, vms=2)
    assert "10 vCPUs for 8" in guardrails.creation_refusal("a", 4, 512, 10, ["default"], ["lan"], pool)


def test_guardrail_routes(client, auth_headers, monkeypatch):
    monkeypatch.setattr(
        guardrails, "usage", lambda p: {"vms": 0, "vcpus": 0, "memory_mb": 0, "disk_gb": 0, "measured": 0}
    )
    admin = auth_headers("root", "admin")
    rule = {"max_vcpu": 2}
    assert client.put("/guardrails/rules/cluster", json=rule, headers=auth_headers("bob", "viewer")).status_code == 403
    assert client.put("/guardrails/rules/cluster", json=rule, headers=admin).status_code == 200
    assert client.put("/guardrails/rules/pool:999", json=rule, headers=admin).status_code == 404
    vm = {"name": "big", "vcpu": 4, "memory_mb": 512, "disks": [{"size_gb": 5}]}
    refused = client.post("/vms", json=vm, headers=admin)
    assert refused.status_code == 422 and "2 at most" in refused.json()["detail"]
    pool = permissions.create_pool("p")
    answer = client.put(f"/guardrails/envelopes/{pool}", json={"max_vms": 2}, headers=admin).json()
    assert answer["envelope"]["max_vms"] == 2
    listed = client.get("/guardrails", headers=admin).json()
    assert listed["rules"]["cluster"]["max_vcpu"] == 2 and listed["pools"][0]["envelope"]["max_vms"] == 2
