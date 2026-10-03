"""An access right goes to an existing account or group, and goes with the account: one given to an unknown name, or
kept after its account was deleted, went to whoever was later created under that name."""

from app.core import permissions


def _acl(client, admin, **over):
    body = {"subject_type": "user", "subject_id": "bob", "role": "lecteur", "resource_type": "vm", "resource_id": "web"}
    return client.post("/acl", json={**body, **over}, headers=admin)


def test_a_right_is_for_an_existing_user_group_or_pool(client, auth_headers):
    admin = auth_headers("root")
    assert _acl(client, admin, subject_id="nobody").status_code == 404
    assert _acl(client, admin, subject_type="group", subject_id="999").status_code == 404
    auth_headers("bob", role="observateur")
    assert _acl(client, admin, resource_type="pool", resource_id="999").status_code == 404
    group_id = permissions.create_group("ops")
    assert _acl(client, admin, subject_type="group", subject_id=str(group_id)).status_code == 201
    assert _acl(client, admin).status_code == 201


def test_a_deleted_accounts_rights_are_not_inherited_by_a_new_one(client, auth_headers):
    admin = auth_headers("root")
    auth_headers("bob", role="observateur")
    assert _acl(client, admin, role="operateur").status_code == 201
    assert permissions.has_privilege({"username": "bob", "role": "observateur"}, "web", "vm.power")
    assert client.delete("/auth/users/bob", headers=admin).status_code == 200
    assert [a for a in permissions._store().acl() if a["subject_id"] == "bob"] == []
    auth_headers("bob", role="observateur", password="another horse battery")
    assert not permissions.has_privilege({"username": "bob", "role": "observateur"}, "web", "vm.power")
