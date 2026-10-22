"""Reading one object answers what the list gives for it (app/core/listing.item), and a 404 when there is none: what a
client that manages one object at a time (the Terraform provider) reads after each change. A role's privileges and a
pool's description change in place, keeping what refers to them."""

import pytest

from app.core import backup_groups
from app.core import permissions as perm
from app.routers import storage


@pytest.fixture()
def admin(client, auth_headers):
    return auth_headers("root")


def test_a_pool_is_read_changed_and_keeps_its_rights(client, admin):
    pool = client.post("/pools", json={"name": "web", "description": "front"}, headers=admin).json()
    one = client.get(f"/pools/{pool['id']}", headers=admin)
    assert one.status_code == 200 and one.json() == client.get("/pools", headers=admin).json()["items"][0]

    r = client.patch(f"/pools/{pool['id']}", json={"description": "front ends"}, headers=admin)
    assert r.status_code == 200 and r.json() == {"id": pool["id"], "name": "web", "description": "front ends"}
    r = client.patch(f"/pools/{pool['id']}", json={"name": "www"}, headers=admin)
    assert r.json()["name"] == "www" and r.json()["description"] == "front ends"
    assert client.patch(f"/pools/{pool['id']}", json={}, headers=admin).status_code == 422
    assert client.patch("/pools/999", json={"description": "x"}, headers=admin).status_code == 404
    assert client.get("/pools/999", headers=admin).status_code == 404


def test_a_role_changes_its_privileges_and_keeps_its_assignments(client, admin, make_user):
    role = client.post("/acl/custom-roles", json={"name": "ops", "privileges": ["vm.view"]}, headers=admin).json()
    make_user("alice", "viewer", "correct horse battery")
    acl = client.post(
        "/acl",
        json={
            "subject_type": "user",
            "subject_id": "alice",
            "role": role["key"],
            "resource_type": "vm",
            "resource_id": "web",
        },
        headers=admin,
    ).json()

    r = client.patch(f"/acl/custom-roles/{role['id']}", json={"privileges": ["vm.view", "vm.power"]}, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json() == {"id": role["id"], "name": "ops", "privileges": ["vm.power", "vm.view"]}
    assert perm.get_role_privileges(role["key"]) == {"vm.view", "vm.power"}
    assert client.get(f"/acl/{acl['id']}", headers=admin).json()["role"] == role["key"]
    assert client.get(f"/acl/custom-roles/{role['id']}", headers=admin).json()["label"] == "ops"

    bad = client.patch(f"/acl/custom-roles/{role['id']}", json={"privileges": ["vm.everything"]}, headers=admin)
    assert bad.status_code == 422 and perm.get_role_privileges(role["key"]) == {"vm.view", "vm.power"}
    assert client.patch("/acl/custom-roles/999", json={"privileges": ["vm.view"]}, headers=admin).status_code == 404
    assert client.get("/acl/999", headers=admin).status_code == 404
    # The fixed paths registered before /acl/{id} keep their routes.
    assert client.get("/acl/roles", headers=admin).status_code == 200


def test_groups_users_and_backup_jobs_are_read_one_by_one(client, admin, make_user, monkeypatch):
    group = client.post("/groups", json={"name": "dev"}, headers=admin).json()
    assert client.get(f"/groups/{group['id']}", headers=admin).json()["name"] == "dev"
    assert client.get("/groups/999", headers=admin).status_code == 404

    make_user("bob", "viewer", "correct horse battery")
    users = client.get("/auth/users", headers=admin).json()["items"]
    one = client.get("/auth/users/bob", headers=admin)
    assert one.status_code == 200 and one.json() == next(u for u in users if u["username"] == "bob")
    assert client.get("/auth/users/nobody", headers=admin).status_code == 404

    monkeypatch.setattr(backup_groups, "_local_vm_names", lambda: [])
    job = backup_groups.save_job(
        {
            "name": "nightly",
            "selection": "all",
            "value": None,
            "excluded": [],
            "frequency": "daily",
            "time": "02:00",
            "target_dir": None,
            "retention_count": 7,
            "keep_days": None,
            "keep_weeks": None,
            "keep_months": None,
            "active": True,
        }
    )
    one = client.get(f"/backup-groups/{job['id']}", headers=admin)
    assert one.status_code == 200 and one.json()["name"] == "nightly" and "vms" in one.json()
    assert client.get("/backup-groups/999", headers=admin).status_code == 404


def test_storage_pools_and_volumes_are_read_one_by_one(client, admin, monkeypatch):
    pools = [{"name": "default", "type": "dir", "node": "local", "path": "/var/lib/libvirt/images"}]
    monkeypatch.setattr(storage, "_pools", lambda node: pools)
    monkeypatch.setattr(storage, "_volumes", lambda pool: [{"name": "data.qcow2", "capacity_gb": 1.0, "used": False}])
    assert client.get("/storage/default", headers=admin).json() == pools[0]
    assert client.get("/storage/missing", headers=admin).status_code == 404
    assert client.get("/storage/default/volumes/data.qcow2", headers=admin).json()["capacity_gb"] == 1.0
    assert client.get("/storage/default/volumes/other", headers=admin).status_code == 404
    # GET /storage/history is the metrics' route, not a pool named "history".
    from app.services import metrics_service

    async def history(range_, node):
        return [{"node": "local", "pool": "default", "points": []}]

    monkeypatch.setattr(metrics_service, "storage_history", history)
    assert client.get("/storage/history", headers=admin).json()["items"][0]["points"] == []


def test_an_ha_protection_is_read_alone(client, admin, monkeypatch):
    from app.core import ha

    monkeypatch.setattr(ha, "list_protected", lambda: [{"vm_name": "web", "node": "local"}])
    r = client.get("/ha/web", headers=admin)
    assert r.status_code == 200 and r.json()["vm_name"] == "web" and "node_status" in r.json()
    assert client.get("/ha/other", headers=admin).status_code == 404
    assert client.get("/ha/status", headers=admin).status_code == 200
