"""Compliance review, required and inherited tags, dynamic pools (docs/design/governance.md, sections 1 to 3)."""

import pytest

from app.core import compliance, guardrails, object_meta, permissions, self_node


@pytest.fixture()
def here(monkeypatch):
    monkeypatch.setattr(self_node, "name", lambda: "node-a")
    monkeypatch.setattr(permissions, "_known_vms", lambda: {"web-1", "web-2", "db-1"})
    return "node-a"


def test_dynamic_pool_by_tag_and_name(database, here):
    by_tag = permissions.create_pool("prod")
    by_name = permissions.create_pool("web")
    permissions.set_pool_rule(by_tag, "prod", None)
    permissions.set_pool_rule(by_name, None, "web-.*")
    object_meta.put("vm", "db-1", "", ["prod"])
    assert set(permissions.get_vm_pools("db-1")) == {by_tag}
    assert set(permissions.get_vm_pools("web-1")) == {by_name}
    listed = {p["name"]: p for p in permissions.list_pools()}
    assert listed["web"]["dynamic_vms"] == ["web-1", "web-2"] and listed["web"]["rule"]["name_pattern"] == "web-.*"
    # Rights given on the pool hold on the VMs its rule takes in.
    permissions.create_acl("user", "bob", "operator", "pool", str(by_tag))
    assert permissions.has_privilege({"username": "bob", "role": "viewer"}, "db-1", "vm.power")
    assert not permissions.has_privilege({"username": "bob", "role": "viewer"}, "web-1", "vm.power")
    permissions.set_pool_rule(by_tag, None, None)
    assert permissions.get_vm_pools("db-1") == []
    with pytest.raises(ValueError):
        permissions.set_pool_rule(by_name, None, "(")


def test_inherited_tags(database, here):
    pool = permissions.create_pool("prod")
    permissions.add_pool_member(pool, "db-1")
    object_meta.put("pool", str(pool), "", ["prod", "team-a"])
    object_meta.put("vm", "db-1", "", ["db"])
    assert object_meta.effective_tags("vm", "db-1") == ["db", "prod", "team-a"]
    # A tag restriction reads the inherited tags.
    permissions.create_dated_acl("user", "bob", "operator", "cluster", "*", None, "team-a", "", "root", "manual")
    assert permissions.has_privilege({"username": "bob", "role": "viewer"}, "db-1", "vm.power")


def test_required_tags(database):
    guardrails.set_rule("cluster", {"required_tags": ["Owner"]}, "root")
    assert guardrails.rules()["cluster"]["required_tags"] == ["owner"]
    assert "requires the tag owner" in guardrails.creation_refusal("a", 1, 512, 5, ["default"], ["lan"])
    assert guardrails.creation_refusal("a", 1, 512, 5, ["default"], ["lan"], tags=["owner"]) is None
    pool = permissions.create_pool("p")
    object_meta.put("pool", str(pool), "", ["owner"])
    assert guardrails.creation_refusal("a", 1, 512, 5, ["default"], ["lan"], pool) is None  # from its pool
    guardrails.set_rule("cluster", {}, "root")
    assert "cluster" not in guardrails.rules()


class _Domain:
    def __init__(self, name):
        self._name = name


def test_compliance_review_and_fix(database, here, monkeypatch):
    vms = {
        "web-1": {"name": "web-1", "vcpu": 2, "memory_mb": 2048, "disks": [{"size_gb": 10, "storage_pool": "default"}],
                  "interfaces": [{"network": "lan"}]},
        "big": {"name": "big", "vcpu": 16, "memory_mb": 2048, "disks": [{"size_gb": 10, "storage_pool": "slow"}],
                "interfaces": [{"network": "lan"}]},
    }  # fmt: skip

    class Conn:
        def listAllDomains(self):
            return [_Domain(n) for n in vms]

        def close(self):
            pass

    from app.core import libvirt_utils, vm_definition

    monkeypatch.setattr(libvirt_utils, "open_conn", lambda *a, **k: Conn())
    monkeypatch.setattr(vm_definition, "read", lambda conn, d: vms[d._name])
    guardrails.set_rule("cluster", {"max_vcpu": 8, "storage_pools": ["default"], "required_tags": ["owner"]}, "root")
    report = compliance.review()
    issues = {v["name"]: {i["rule"] for i in v["issues"]} for v in report["vms"]}
    assert issues["big"] == {"max_vcpu", "storage_pools", "required_tags"}
    assert issues["web-1"] == {"required_tags"}
    assert report["summary"] == {"vms": 2, "compliant": 0, "percent": 0, "fixable": 2}
    assert compliance.fix("web-1") == ["owner"]
    assert object_meta.get("vm", "web-1")["tags"] == ["owner"]
    assert compliance.review()["summary"]["compliant"] == 1


def test_routes(client, auth_headers, here, monkeypatch):
    admin = auth_headers("root", "admin")
    pool = permissions.create_pool("web")
    rule = client.put(f"/pools/{pool}/rule", json={"name_pattern": "web-.*"}, headers=admin).json()
    assert rule["dynamic_vms"] == ["web-1", "web-2"]
    assert (
        client.put(f"/pools/{pool}/rule", json={"tag": "x"}, headers=auth_headers("bob", "viewer")).status_code == 403
    )
    assert client.put("/pools/999/rule", json={"tag": "x"}, headers=admin).status_code == 404
    assert client.get("/compliance", headers=auth_headers("eve", "viewer")).status_code == 403
