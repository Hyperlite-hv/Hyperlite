"""Pool export and VM groups (docs/design/governance.md, sections 12 and 13)."""

import pytest

from app.core import permissions, recycle_bin, vm_definition, vm_groups


def _vm(name, net="lan", store="fast"):
    return {"name": name, "vcpu": 2, "memory_mb": 2048, "firmware": "bios", "resource_pool": None, "tags": [],
            "disks": [{"size_gb": 10, "storage_pool": store, "target": "vda"}],
            "interfaces": [{"network": net, "mac": None, "vlan_tag": None}]}  # fmt: skip


def test_pool_as_terraform():
    pool = {"id": 3, "name": "web", "description": "shop"}
    acl = [{"id": 9, "subject_type": "group", "subject_id": "4", "role": "operator"}]
    hcl = vm_definition.pool_as_terraform(pool, [_vm("web-1"), _vm("web-2", net="dmz")], acl, {4: "devs"})
    assert 'resource "hyperlite_resource_pool" "web"' in hcl
    assert 'data "hyperlite_network" "dmz"' in hcl and 'data "hyperlite_network" "lan"' in hcl
    assert 'data "hyperlite_storage_pool" "fast"' in hcl
    assert hcl.count('resource "hyperlite_vm"') == 2 and "resource_pool = hyperlite_resource_pool.web.name" in hcl
    assert 'group = "devs"' in hcl and 'role = "operator"' in hcl


@pytest.fixture()
def fleet(monkeypatch):
    domains = {}
    deployed, deleted = [], []
    monkeypatch.setattr(vm_groups, "_domains", lambda: dict(domains))
    import app.routers.templates as templates
    from app.routers.vms import lifecycle

    def deploy(template, payload, user, wait=True):
        deployed.append(payload.new_name)
        domains[payload.new_name] = False

    def delete(name, confirm, node, purge, user):
        deleted.append(name)
        domains.pop(name)

    monkeypatch.setattr(templates, "deploy_template", deploy)
    monkeypatch.setattr(lifecycle, "delete_vm", delete)
    return domains, deployed, deleted


def test_scale_up_and_down(database, fleet, monkeypatch):
    domains, deployed, deleted = fleet
    pool = permissions.create_pool("ci")
    g = vm_groups.create(
        {"name": "runners", "template": "deb", "prefix": "run", "pool_id": pool, "tags": ["ci"]}, "root"
    )
    user = {"username": "root", "role": "admin"}
    monkeypatch.setattr(recycle_bin, "holds", lambda name: name == "run-2")  # binned: its name stays taken
    assert vm_groups.scale(g["id"], 3, user)["made"] == ["run-1", "run-3", "run-4"]
    assert "run-3" in permissions.list_pools()[0]["vms"]
    domains["run-4"] = True  # running
    assert vm_groups.scale(g["id"], 2, user)["removed"] == ["run-3"]  # a stopped one first, the highest number
    with pytest.raises(vm_groups.GroupError, match="still run"):
        vm_groups.scale(g["id"], 0, user)
    assert [m["name"] for m in vm_groups.groups()[0]["members"]] == ["run-1", "run-4"]
    assert deployed == ["run-1", "run-3", "run-4"] and deleted == ["run-3"]
    with pytest.raises(vm_groups.GroupError):
        vm_groups.create({"name": "x", "template": "deb", "prefix": "Bad Prefix"}, "root")


def test_a_vm_the_group_did_not_make_is_never_one_of_its_members(database, fleet):
    """Membership was a name pattern: a VM named <prefix>-<n> by someone else was counted, and deleted when the count
    went down. Only the VMs the group made are its own; a prefix an existing VM already has is refused."""
    domains, _deployed, deleted = fleet
    domains["db-1"] = True  # someone's production VM
    with pytest.raises(vm_groups.GroupError, match="db-1 already has this prefix"):
        vm_groups.create({"name": "dbs", "template": "deb", "prefix": "db"}, "root")
    g = vm_groups.create({"name": "webs", "template": "deb", "prefix": "web"}, "root")
    user = {"username": "root", "role": "admin"}
    assert vm_groups.scale(g["id"], 1, user)["made"] == ["web-1"]
    domains["web-2"] = False  # made by hand afterwards, under the group's pattern
    assert [m["name"] for m in vm_groups.groups()[0]["members"]] == ["web-1"]
    assert vm_groups.scale(g["id"], 0, user)["removed"] == ["web-1"] and deleted == ["web-1"]
    assert "web-2" in domains


def test_a_protected_member_is_never_removed(database, fleet):
    from app.core import protections

    _domains, _deployed, deleted = fleet
    g = vm_groups.create({"name": "webs", "template": "deb", "prefix": "web"}, "root")
    user = {"username": "root", "role": "admin"}
    vm_groups.scale(g["id"], 2, user)
    protections.put("vm", "web-2", "keep", "", "root")
    with pytest.raises(vm_groups.GroupError, match="kept from deletion"):
        vm_groups.scale(g["id"], 1, user)
    assert deleted == []
