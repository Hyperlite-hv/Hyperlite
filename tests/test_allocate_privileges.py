"""Creating VMs without being an administrator, as vSphere and Proxmox VE allow: vm.allocate on the pool the VM goes
in, storage.allocate on the storage pool of its disks, network.assign on its network. What a Terraform pipeline's
token needs, and nothing more."""

import sqlite3

import pytest

from app.core import permissions as perm
from app.core import statd
from app.routers.vms import create, lifecycle
from tests.test_iso_install_is_manual import created  # noqa: F401 (a fixture)

BODY = {
    "name": "web",
    "vcpu": 1,
    "memory_mb": 512,
    "disks": [{"size_gb": 1}],
    "network": "lan",
    "storage_pool": "fast",
    "username": "u",
    "password": "pass",
}


@pytest.fixture()
def pipeline(client, auth_headers, monkeypatch):
    """A viewer account, an empty pool and a role made of the three privileges; the creation itself is not run."""
    made = []
    monkeypatch.setattr(
        create, "_create_vm", lambda payload, user, **kw: made.append(payload.name) or {"name": payload.name}
    )
    monkeypatch.setattr(create.maintenance, "refuse_if_in_maintenance", lambda *a: None)
    headers = auth_headers("ci", role="viewer")
    pool = perm.create_pool("ci-pool")
    role = f"custom:{perm.create_custom_role('terraform', ['vm.allocate', 'storage.allocate', 'network.assign'])}"
    return {"headers": headers, "pool": pool, "role": role, "made": made}


def grant(role, resource_type, resource_id):
    perm.create_acl("user", "ci", role, resource_type, str(resource_id))


def test_an_account_creates_a_vm_only_with_each_privilege_on_what_it_uses(client, pipeline):
    h, pool, role = pipeline["headers"], pipeline["pool"], pipeline["role"]
    body = {**BODY, "resource_pool": pool}

    r = client.post("/vms", json=BODY, headers=h)
    assert r.status_code == 403 and "resource_pool" in r.json()["detail"]
    r = client.post("/vms", json=body, headers=h)
    assert r.status_code == 403 and "vm.allocate" in r.json()["detail"]
    grant(role, "pool", pool)
    r = client.post("/vms", json=body, headers=h)
    assert r.status_code == 403 and "storage pool 'fast'" in r.json()["detail"]
    grant(role, "storage", "fast")
    r = client.post("/vms", json=body, headers=h)
    assert r.status_code == 403 and "network 'lan'" in r.json()["detail"]
    grant(role, "network", "lan")
    r = client.post("/vms", json=body, headers=h)
    assert r.status_code == 201, r.text
    assert pipeline["made"] == ["web"]
    # Another storage pool is still refused.
    r = client.post("/vms", json={**body, "storage_pool": "slow"}, headers=h)
    assert r.status_code == 403


def test_a_role_on_another_pool_gives_nothing(client, pipeline):
    other = perm.create_pool("other")
    grant(pipeline["role"], "pool", other)
    r = client.post("/vms", json={**BODY, "resource_pool": pipeline["pool"]}, headers=pipeline["headers"])
    assert r.status_code == 403 and pipeline["made"] == []


def test_an_administrator_needs_no_pool_but_a_pool_must_exist(client, auth_headers, pipeline):
    admin = auth_headers("root")
    assert client.post("/vms", json=BODY, headers=admin).status_code == 201
    assert client.post("/vms", json={**BODY, "resource_pool": 999}, headers=admin).status_code == 404


def test_the_vm_goes_in_its_pool(database, created):  # noqa: F811 (the fixture imported above)
    """Once made, the VM is a member of its pool: the pool's assignments give its creator rights on it."""
    pool = perm.create_pool("ci-pool")
    created("debian-13.iso", resource_pool=pool)
    assert [p["id"] for p in perm.list_pools() if "srv" in p["vms"]] == [pool]


def test_vm_allocate_on_its_pool_lets_an_account_delete_the_vm(client, pipeline, monkeypatch):
    deleted = []
    monkeypatch.setattr(lifecycle, "_delete_vm", lambda name, confirm, node, user: deleted.append(name) or {})
    h, pool, role = pipeline["headers"], pipeline["pool"], pipeline["role"]
    perm.add_pool_member(pool, "web")
    assert client.delete("/vms/web?confirm=true", headers=h).status_code == 403
    grant(role, "pool", pool)
    assert client.delete("/vms/web?confirm=true", headers=h).status_code == 200
    assert client.delete("/vms/other?confirm=true", headers=h).status_code == 403
    assert deleted == ["web"]


def test_storage_and_network_assignments_wait_for_every_member(client, auth_headers, pipeline, monkeypatch):
    admin = auth_headers("root")
    body = {
        "subject_type": "user",
        "subject_id": "ci",
        "role": pipeline["role"],
        "resource_type": "storage",
        "resource_id": "fast",
    }
    monkeypatch.setattr(statd, "members_without", lambda feature: ["node2"])
    r = client.post("/acl", json=body, headers=admin)
    assert r.status_code == 409 and "node2 still runs" in r.json()["detail"]
    monkeypatch.setattr(statd, "members_without", lambda feature: [])
    assert client.post("/acl", json=body, headers=admin).status_code == 201
    assert (
        client.post("/acl", json={**body, "resource_type": "network", "resource_id": "lan"}, headers=admin).status_code
        == 201
    )
    assert client.post("/acl", json={**body, "resource_type": "disk"}, headers=admin).status_code == 422


def test_a_member_whose_status_is_unknown_counts_as_not_knowing(monkeypatch):
    from app.core import cluster_view

    monkeypatch.setattr(cluster_view, "members", lambda: [{"name": "node2"}, {"name": "node3"}, {"name": "node4"}])
    monkeypatch.setattr(
        statd, "table", lambda: {"node2": {"features": ["acl_storage_network"]}, "node3": {"features": []}}
    )
    assert statd.members_without("acl_storage_network") == ["node3", "node4"]


def test_an_older_database_takes_storage_and_network_assignments(database):
    """A database of 1.1 (its CHECK knows vm, pool and container only) is migrated at start, its rows kept."""
    with database.get_conn() as db:
        db.execute("DROP TABLE acl")
        db.execute(
            "CREATE TABLE acl (id INTEGER PRIMARY KEY AUTOINCREMENT, subject_type TEXT NOT NULL CHECK(subject_type IN "
            "('user','group')), subject_id TEXT NOT NULL, role TEXT NOT NULL, resource_type TEXT NOT NULL CHECK("
            "resource_type IN ('vm','pool','container')), resource_id TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO acl (subject_type, subject_id, role, resource_type, resource_id) "
            "VALUES ('user', 'a', 'drive', 'vm', 'web')"
        )
        db.commit()
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO acl (subject_type, subject_id, role, resource_type, resource_id) "
                "VALUES ('user', 'a', 'drive', 'network', 'lan')"
            )
    database.init_db()
    perm.create_acl("user", "a", "drive", "network", "lan")
    assert [(a["resource_type"], a["resource_id"]) for a in perm.list_acl()] == [("vm", "web"), ("network", "lan")]
