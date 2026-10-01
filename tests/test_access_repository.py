"""The access repository (control plane v2, lot 8): custom roles, groups, pools and assignments (ACL), run against
every backend (SQLite today)."""

import asyncio

import pytest


@pytest.fixture(params=["sqlite"])
def repo(request, database):
    from app.repositories.sqlite.access import SqliteAccessRepository

    return SqliteAccessRepository()


def run(coro):
    return asyncio.run(coro)


def test_groups_and_their_members(repo):
    store = repo.sync
    ops = store.create_group("ops")
    store.add_group_member(ops, "bob")
    store.add_group_member(ops, "alice")
    store.add_group_member(ops, "alice")  # idempotent
    assert run(repo.groups()) == [{"id": ops, "name": "ops", "membres": ["alice", "bob"]}]
    assert store.groups_of("alice") == [ops]
    store.remove_group_member(ops, "alice")
    assert store.groups_of("alice") == []
    store.create_acl("group", str(ops), "lecteur", "vm", "web")
    store.delete_group(ops)
    assert run(repo.groups()) == [] and store.acl() == []  # its assignments go with it


def test_pools_follow_their_vms(repo):
    store = repo.sync
    prod = store.create_pool("prod", "production")
    store.add_pool_member(prod, "web")
    store.add_pool_member(prod, "db")
    assert run(repo.pools()) == [{"id": prod, "name": "prod", "description": "production", "vms": ["db", "web"]}]
    store.create_acl("user", "bob", "lecteur", "vm", "web")
    store.rename_pool_member("web", "web2")
    assert store.pools_of("web2") == [prod] and store.acl() == []  # the old name's direct rights do not follow
    store.remove_pool_member(prod, "db")
    store.remove_vm_from_all_pools("web2")
    assert store.pools()[0]["vms"] == []
    store.create_acl("user", "bob", "lecteur", "pool", str(prod))
    store.delete_pool(prod)
    assert store.pools() == [] and store.acl() == []


def test_custom_roles_and_assignments(repo):
    store = repo.sync
    role = store.create_custom_role("snap", "vm.view,vm.snapshot")
    assert store.custom_role(role)["privileges"] == "vm.view,vm.snapshot" and store.custom_role(999) is None
    a = store.create_acl("user", "bob", f"custom:{role}", "vm", "web")
    store.create_acl("user", "bob", "lecteur", "container", "ct1")
    assert [x["id"] for x in run(repo.acl())] == [a, a + 1]
    assert [x["resource_id"] for x in run(repo.acl("container"))] == ["ct1"]
    store.delete_custom_role(f"custom:{role}", role)
    assert run(repo.custom_roles()) == [] and [x["resource_type"] for x in store.acl()] == ["container"]
    store.create_acl("user", "bob", "lecteur", "vm", "web")
    store.delete_acl_for_vm("web")
    store.delete_acl(a + 1)
    assert store.acl() == []
