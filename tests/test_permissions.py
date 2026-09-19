"""Per-resource privilege evaluation (roles, ACLs, groups, pools)."""

import pytest

from app.core import permissions


def user(username, role="utilisateur"):
    return {"username": username, "role": role}


def test_admin_has_every_privilege(database):
    for privilege in permissions.ALL_PRIVILEGES:
        assert permissions.has_privilege(user("root", "admin"), "any-vm", privilege)


def test_observer_is_read_only(database):
    observer = user("watcher", "observateur")
    assert permissions.has_privilege(observer, "vm1", "vm.view")
    assert permissions.has_privilege(observer, "vm1", "vm.console")
    assert not permissions.has_privilege(observer, "vm1", "vm.power")
    assert not permissions.has_privilege(observer, "vm1", "vm.hardware")


def test_user_without_acl_has_no_access(database):
    assert not permissions.has_privilege(user("bob"), "vm1", "vm.view")


def test_direct_user_acl_grants_only_the_role_privileges(database):
    permissions.create_acl("user", "bob", "operateur", "vm", "vm1")
    bob = user("bob")
    assert permissions.has_privilege(bob, "vm1", "vm.power")
    assert not permissions.has_privilege(bob, "vm1", "vm.hardware")  # not part of the operator role
    assert not permissions.has_privilege(bob, "other-vm", "vm.power")  # scoped to vm1 only


def test_group_acl_applies_to_members_only(database):
    group_id = permissions.create_group("ops")
    permissions.add_group_member(group_id, "carol")
    permissions.create_acl("group", str(group_id), "gestionnaire", "vm", "vm1")
    assert permissions.has_privilege(user("carol"), "vm1", "vm.snapshot")
    assert not permissions.has_privilege(user("dave"), "vm1", "vm.snapshot")


def test_pool_acl_covers_every_vm_in_the_pool(database):
    pool_id = permissions.create_pool("web")
    permissions.add_pool_member(pool_id, "vm-a")
    permissions.create_acl("user", "erin", "lecteur", "pool", str(pool_id))
    assert permissions.has_privilege(user("erin"), "vm-a", "vm.view")
    assert not permissions.has_privilege(user("erin"), "vm-b", "vm.view")


def test_deleting_an_acl_revokes_the_access(database):
    acl_id = permissions.create_acl("user", "bob", "lecteur", "vm", "vm1")
    assert permissions.has_privilege(user("bob"), "vm1", "vm.view")
    permissions.delete_acl(acl_id)
    assert not permissions.has_privilege(user("bob"), "vm1", "vm.view")


def test_unknown_role_is_rejected(database):
    with pytest.raises(ValueError):
        permissions.create_acl("user", "bob", "superuser", "vm", "vm1")


def test_custom_role_uses_its_own_privilege_set(database):
    role_key = f"custom:{permissions.create_custom_role('snapshotter', {'vm.view', 'vm.snapshot'})}"
    permissions.create_acl("user", "bob", role_key, "vm", "vm1")
    bob = user("bob")
    assert permissions.has_privilege(bob, "vm1", "vm.snapshot")
    assert not permissions.has_privilege(bob, "vm1", "vm.power")


def test_container_privileges_follow_the_same_model(database):
    assert permissions.has_container_privilege(user("root", "admin"), "ct1", "container.power")
    assert permissions.has_container_privilege(user("watcher", "observateur"), "ct1", "container.view")
    assert not permissions.has_container_privilege(user("watcher", "observateur"), "ct1", "container.power")
    permissions.create_acl("user", "bob", "operateur", "container", "ct1")
    assert permissions.has_container_privilege(user("bob"), "ct1", "container.power")
    assert not permissions.has_container_privilege(user("bob"), "ct2", "container.power")


def test_custom_role_rejects_unknown_or_empty_privilege_sets(database):
    with pytest.raises(ValueError):
        permissions.create_custom_role("bad", {"vm.teleport"})
    with pytest.raises(ValueError):
        permissions.create_custom_role("empty", set())


def test_deleting_a_custom_role_removes_the_acls_that_used_it(database):
    role_id = permissions.create_custom_role("temp", {"vm.view"})
    permissions.create_acl("user", "bob", f"custom:{role_id}", "vm", "vm1")
    assert permissions.has_privilege(user("bob"), "vm1", "vm.view")
    permissions.delete_custom_role(role_id)
    assert not permissions.has_privilege(user("bob"), "vm1", "vm.view")
