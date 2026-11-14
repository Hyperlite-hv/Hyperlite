"""Perimeters, area privileges and delegation (docs/design/access-2.md, sections 1 to 3)."""

import pytest

from app.core import permissions, self_node


def user(username, role="viewer"):
    return {"username": username, "role": role}


@pytest.fixture()
def here(monkeypatch):
    monkeypatch.setattr(self_node, "name", lambda: "node-a")
    return "node-a"


def test_cluster_assignment_holds_on_every_object(database, here):
    permissions.create_acl("user", "bob", "builder", "cluster", "*")
    bob = user("bob")
    assert permissions.has_privilege(bob, "any-vm", "vm.power")
    assert permissions.has_container_privilege(bob, "any-ct", "container.power")
    assert permissions.has_scoped_privilege(bob, "storage", "fast", "storage.allocate")
    assert permissions.has_scoped_privilege(bob, "pool", 7, "vm.allocate")
    assert not permissions.has_privilege(bob, "any-vm", "vm.exec")  # a root shell in every guest stays explicit


def test_node_assignment_holds_only_where_that_node_answers(database, here):
    permissions.create_acl("user", "bob", "manager", "node", "node-a")
    permissions.create_acl("user", "eve", "manager", "node", "node-b")
    assert permissions.has_privilege(user("bob"), "vm1", "vm.resize")
    assert not permissions.has_privilege(user("eve"), "vm1", "vm.resize")


def test_pool_assignment_does_not_reach_other_vms(database, here):
    pool = permissions.create_pool("web")
    permissions.add_pool_member(pool, "vm-in")
    permissions.create_acl("user", "bob", "operator", "pool", str(pool))
    assert permissions.has_privilege(user("bob"), "vm-in", "vm.power")
    assert not permissions.has_privilege(user("bob"), "vm-out", "vm.power")


def test_area_privileges_and_their_reach(database, here):
    permissions.create_acl("user", "nina", "builder", "node", "node-a")
    nina = user("nina")
    assert permissions.has_area_privilege(nina, "node.manage")
    assert permissions.has_area_privilege(nina, "update.apply")
    # The cluster's own configuration is only granted from the cluster perimeter.
    assert not permissions.has_area_privilege(nina, "alert.manage")
    assert not permissions.has_area_privilege(nina, "cluster.manage")
    permissions.create_acl("user", "wes", "watcher", "cluster", "*")
    wes = user("wes")
    assert permissions.has_area_privilege(wes, "alert.manage")
    assert permissions.has_area_privilege(wes, "backup.view")
    assert not permissions.has_area_privilege(wes, "backup.manage")
    assert not permissions.has_area_privilege(wes, "access.view")
    assert permissions.has_area_privilege(user("root", "admin"), "access.manage")


def test_every_role_names_known_privileges():
    for key, role in permissions.ROLES.items():
        assert role["privileges"] <= set(permissions.ALL_PRIVILEGES), key
    assert "access.manage" not in permissions.ROLES["builder"]["privileges"]
    assert {p for p in permissions.ROLES["observer"]["privileges"] if not p.endswith(".view")} == set()


def test_object_acl_shows_inherited_perimeters(client, auth_headers, here):
    pool = permissions.create_pool("web")
    permissions.add_pool_member(pool, "vm-a")
    permissions.create_acl("user", "own", "drive", "vm", "vm-a")
    permissions.create_acl("user", "inpool", "drive", "pool", str(pool))
    permissions.create_acl("user", "onnode", "drive", "node", "node-a")
    permissions.create_acl("user", "everywhere", "drive", "cluster", "*")
    rows = client.get("/acl/object/vm/vm-a", headers=auth_headers("root", "admin")).json()["items"]
    assert [(r["subject_id"], r["inherited_type"]) for r in rows] == [
        ("own", None),
        ("inpool", "pool"),
        ("onnode", "node"),
        ("everywhere", "cluster"),
    ]


def test_delegate_assigns_inside_its_perimeter_only(client, auth_headers, make_user, here):
    pool = permissions.create_pool("web")
    other = permissions.create_pool("db")
    permissions.add_pool_member(pool, "vm-a")
    make_user("dev", "viewer", "correct horse battery")
    make_user("dev2", "viewer", "correct horse battery")
    permissions.create_acl("user", "del", "access_delegate", "pool", str(pool))
    headers = auth_headers("del", "viewer")

    def assign(**body):
        payload = {"subject_type": "user", "subject_id": "dev", "role": "operator"} | body
        return client.post("/acl", json=payload, headers=headers)

    assert assign(resource_type="pool", resource_id=str(pool)).status_code == 201
    assert assign(resource_type="vm", resource_id="vm-a").status_code == 201  # a VM of its pool
    assert assign(resource_type="vm", resource_id="vm-z").status_code == 403
    assert assign(resource_type="pool", resource_id=str(other)).status_code == 403
    assert assign(resource_type="cluster", resource_id="*").status_code == 403
    assert assign(subject_id="del", resource_type="pool", resource_id=str(pool)).status_code == 403  # itself
    # Another delegate beside it: refused. On a VM below its pool: allowed.
    refused = assign(subject_id="dev2", role="access_delegate", resource_type="pool", resource_id=str(pool))
    assert refused.status_code == 403
    assert assign(subject_id="dev2", role="access_delegate", resource_type="vm", resource_id="vm-a").status_code == 201
    # It sees the pool's Rights tab, not another pool's.
    assert client.get(f"/acl/object/pool/{pool}", headers=headers).status_code == 200
    assert client.get(f"/acl/object/pool/{other}", headers=headers).status_code == 403
    # It removes what it may give, nothing else.
    outside = permissions.create_acl("user", "dev", "drive", "pool", str(other))
    assert client.delete(f"/acl/{outside}", headers=headers).status_code == 403
    inside = next(a["id"] for a in permissions.list_acl() if a["resource_id"] == "vm-a" and a["role"] == "operator")
    assert client.delete(f"/acl/{inside}", headers=headers).status_code == 200


def test_viewer_without_delegation_cannot_assign(client, auth_headers, make_user, here):
    make_user("dev", "viewer", "correct horse battery")
    headers = auth_headers("plain", "viewer")
    body = {"subject_type": "user", "subject_id": "dev", "role": "drive", "resource_type": "vm", "resource_id": "x"}
    assert client.post("/acl", json=body, headers=headers).status_code == 403
    assert client.get("/access/subjects", headers=headers).status_code == 403


def test_area_routes_follow_the_privilege(client, auth_headers, here):
    headers = auth_headers("wes", "viewer")
    assert client.get("/backup-remotes", headers=headers).status_code == 403
    assert client.post("/alert-rules", json={}, headers=headers).status_code == 403
    permissions.create_acl("user", "wes", "watcher", "cluster", "*")
    assert client.get("/backup-remotes", headers=headers).status_code == 200  # backup.view, from the watcher
    assert client.post("/alert-rules", json={}, headers=headers).status_code == 422  # alert.manage: past the check
    assert client.get("/update/history", headers=headers).status_code == 200  # update.view, from the watcher
    assert client.post("/update/apply", json={}, headers=headers).status_code == 403  # update.apply is not a watcher's


def test_me_and_explain(client, auth_headers, make_user, here):
    pool = permissions.create_pool("web")
    permissions.add_pool_member(pool, "vm-a")
    permissions.create_acl("user", "bob", "operator", "pool", str(pool))
    permissions.create_acl("user", "bob", "maintainer", "node", "node-a")
    bob = auth_headers("bob", "viewer")
    me = client.get("/access/me", headers=bob).json()
    assert me["admin"] is False
    assert "update.apply" in me["areas"] and "alert.view" not in me["areas"]
    assert {(p["resource_type"], p["resource_id"]) for p in me["perimeters"]} == {("pool", str(pool)), ("node", here)}

    answer = client.get(
        "/access/explain",
        params={"resource_type": "vm", "resource_id": "vm-a", "username": "bob"},
        headers=auth_headers("root", "admin"),
    ).json()
    held = {p["privilege"]: p["via"] for p in answer["privileges"]}
    assert held["vm.power"][0]["resource_type"] == "pool"
    assert held["vm.view"][0] == {"global": "viewer"}
    assert "vm.snapshot" not in held
    assert [p["resource_type"] for p in answer["perimeters"]] == ["vm", "pool", "node", "cluster"]
    # Anyone may ask about itself, not about another account without access.view.
    mine = {"resource_type": "vm", "resource_id": "vm-a", "username": "bob"}
    assert client.get("/access/explain", params=mine, headers=bob).status_code == 200
    make_user("carol", "viewer", "correct horse battery")
    theirs = mine | {"username": "carol"}
    assert client.get("/access/explain", params=theirs, headers=bob).status_code == 403
