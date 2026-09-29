"""Node maintenance mode: the drain plan says which VMs move and why the others stay, draining migrates them one
after another through ordinary migration tasks, and a node in maintenance receives no new VM and is never a
migration or HA recovery target. A protected VM's HA record follows it when it is migrated."""

from datetime import UTC, datetime

import libvirt
import pytest

from app.core import cluster_compat, ha, maintenance
from app.core.preflight import BLOCKING, OK
from app.routers import nodes

ISCSI_DISK = (
    "<disk type='block' device='disk'><source dev='/dev/disk/by-path/ip-192.0.2.5:3260-iscsi-iqn.2026-01.example:s-lun-1'/>"
    "<target dev='vda'/></disk>"
)
FILE_DISK = "<disk type='file' device='disk'><source file='/var/lib/libvirt/images/x.qcow2'/><target dev='vda'/></disk>"


class FakeDomain:
    def __init__(self, name, active=True, iscsi=False, blocked=False):
        self._name, self.active, self.blocked = name, active, blocked
        self._xml = f"<domain><name>{name}</name><devices>{ISCSI_DISK if iscsi else FILE_DISK}</devices></domain>"

    def name(self):
        return self._name

    def isActive(self):
        return self.active

    def XMLDesc(self, *_):
        return self._xml


class FakeConn:
    def __init__(self, *domains):
        self.domains = {d.name(): d for d in domains}

    def listAllDomains(self, flags):
        return list(self.domains.values())

    def lookupByName(self, name):
        if name not in self.domains:
            raise libvirt.libvirtError("Domain not found")
        return self.domains[name]

    def close(self):
        pass


@pytest.fixture()
def compat(monkeypatch):
    """The compatibility diagnostic blocks exactly the domains flagged `blocked`."""

    def check(src, dst, domain):
        if domain.blocked:
            return [{"id": "cpu", "statut": BLOCKING, "message": "CPU model not available on the target"}]
        return [{"id": "cpu", "statut": OK, "message": "ok"}]

    monkeypatch.setattr(cluster_compat, "check_vm_migration", check)


def source_conn():
    return FakeConn(
        FakeDomain("web"),
        FakeDomain("db"),
        FakeDomain("stopped", active=False),
        FakeDomain("lun", iscsi=True),
        FakeDomain("oldcpu", blocked=True),
        FakeDomain("twin"),
    )


def test_the_plan_moves_running_vms_and_explains_every_vm_that_stays(database, compat):
    result = maintenance.plan(source_conn(), FakeConn(FakeDomain("twin", active=False)), "n2")
    assert result["migrables"] == ["db", "web"]
    reasons = {b["nom"]: b["raison"] for b in result["non_migrables"]}
    assert reasons["stopped"].startswith("Stopped")
    assert "iSCSI" in reasons["lun"]
    assert "CPU model" in reasons["oldcpu"]
    assert "already exists on 'n2'" in reasons["twin"]


def test_without_a_target_every_vm_stays(database, compat):
    result = maintenance.plan(source_conn(), None, None)
    assert result["migrables"] == []
    assert all(b["raison"] for b in result["non_migrables"]) and len(result["non_migrables"]) == 6


def _add_node(database, name):
    with database.get_conn() as db:
        db.execute(
            "INSERT INTO nodes (name, hostname, ssh_user, ssh_port, statut, added_at) "
            "VALUES (?, ?, 'root', 22, 'en_ligne', ?)",
            (name, f"{name}.example", datetime.now(UTC).isoformat()),
        )
        db.commit()


@pytest.fixture()
def cluster(client, auth_headers, database, monkeypatch, compat):
    _add_node(database, "n2")
    conns = {None: source_conn(), "n2": FakeConn(FakeDomain("twin", active=False))}
    monkeypatch.setattr(nodes, "open_conn", lambda key=None: conns[key])
    # The drain runs in the test's own thread, so its outcome can be checked right away.
    monkeypatch.setattr(nodes, "_run_in_background", lambda target, *args: target(*args))
    migrated = []
    fail = set()

    def fake_migrate(task_id, username, source_node, target_node, vm_name):
        migrated.append((source_node, target_node, vm_name))
        nodes.finish_task(task_id, "echec" if vm_name in fail else "termine")

    monkeypatch.setattr(nodes, "_migrate_vm_job", fake_migrate)
    return {"client": client, "headers": auth_headers("admin1"), "migrated": migrated, "fail": fail, "db": database}


def _tasks(database, type_):
    with database.get_conn() as db:
        return [dict(r) for r in db.execute("SELECT * FROM tasks WHERE type = ? ORDER BY cree_le", (type_,))]


def test_draining_marks_the_node_then_migrates_each_vm_as_its_own_task(cluster):
    c, h = cluster["client"], cluster["headers"]
    plan = c.get("/nodes/local/drain-plan", params={"target_node": "n2"}, headers=h)
    assert plan.status_code == 200 and plan.json()["migrables"] == ["db", "web"]
    assert maintenance.get("local") is None  # the plan changes nothing

    r = c.post("/nodes/local/maintenance", json={"target_node": "n2"}, headers=h)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["en_maintenance"] is True and body["migrables"] == ["db", "web"] and len(body["non_migrables"]) == 4
    assert cluster["migrated"] == [(None, "n2", "db"), (None, "n2", "web")]
    assert [t["cible"] for t in _tasks(cluster["db"], "migrate_vm")] == ["db", "web"]
    drain = _tasks(cluster["db"], "drain_node")
    assert len(drain) == 1 and drain[0]["id"] == body["task_id"] and drain[0]["statut"] == "termine"
    assert [m["node"] for m in c.get("/nodes/maintenance", headers=h).json()] == ["local"]


def test_a_failed_migration_fails_the_drain_task_with_its_name(cluster):
    cluster["fail"].add("web")
    r = cluster["client"].post("/nodes/local/maintenance", json={"target_node": "n2"}, headers=cluster["headers"])
    drain = _tasks(cluster["db"], "drain_node")[0]
    assert r.status_code == 202 and drain["statut"] == "echec" and "failed: web" in drain["erreur"]


def test_maintenance_without_a_target_only_marks_the_node(cluster):
    r = cluster["client"].post("/nodes/local/maintenance", json={}, headers=cluster["headers"])
    assert r.status_code == 202 and r.json()["task_id"] is None and cluster["migrated"] == []
    assert maintenance.get("local")["started_by"] == "admin1"


def test_bad_targets_are_refused_before_anything_changes(cluster):
    c, h = cluster["client"], cluster["headers"]
    assert c.post("/nodes/local/maintenance", json={"target_node": "local"}, headers=h).status_code == 422
    assert c.post("/nodes/local/maintenance", json={"target_node": "ghost"}, headers=h).status_code == 404
    assert c.post("/nodes/ghost/maintenance", json={}, headers=h).status_code == 404
    maintenance.enter("n2", "someone")
    r = c.post("/nodes/local/maintenance", json={"target_node": "n2"}, headers=h)
    assert r.status_code == 409 and "maintenance" in r.json()["detail"]
    assert maintenance.get("local") is None and cluster["migrated"] == []


def test_a_node_in_maintenance_receives_no_vm_and_is_no_target(cluster):
    c, h = cluster["client"], cluster["headers"]
    maintenance.enter("local", "admin1")
    maintenance.enter("n2", "admin1")
    create = c.post("/vms", json={"name": "new", "vcpu": 1, "memory_mb": 512, "disks": [{"size_gb": 5}]}, headers=h)
    assert create.status_code == 409 and "VM creation refused" in create.json()["detail"]
    migrate = c.post("/vms/web/migrate", json={"target_node": "n2"}, headers=h)
    assert migrate.status_code == 409
    recover = c.post("/ha/web/recover", json={"target_node": "n2"}, headers=h)
    assert recover.status_code == 409 and "HA recovery refused" in recover.json()["detail"]


def test_ending_the_maintenance(cluster):
    c, h = cluster["client"], cluster["headers"]
    maintenance.enter("n2", "admin1")
    assert c.delete("/nodes/n2/maintenance", headers=h).json() == {"node": "n2", "en_maintenance": False}
    assert c.delete("/nodes/n2/maintenance", headers=h).status_code == 404
    assert c.get("/nodes/maintenance", headers=h).json() == []


def test_an_observer_can_read_but_not_change_maintenance(cluster, auth_headers):
    h = auth_headers("watcher", role="observateur")
    c = cluster["client"]
    assert c.get("/nodes/maintenance", headers=h).status_code == 200
    assert c.post("/nodes/local/maintenance", json={}, headers=h).status_code == 403
    assert c.get("/nodes/local/drain-plan", headers=h).status_code == 403
    assert maintenance.get("local") is None


def test_the_ha_record_follows_a_migrated_vm(database):
    with database.get_conn() as db:
        db.execute(
            "INSERT INTO ha_protected_vms (vm_name, node, enabled_by, enabled_at) VALUES ('web', 'local', 'a', 'now')"
        )
        db.commit()
    assert ha.follow_migration("web", "n2") is True
    assert ha.get_protected("web")["node"] == "n2"
    assert ha.follow_migration("not-protected", "n2") is False


def test_a_second_drain_of_the_same_node_is_refused_and_a_finished_one_frees_it(cluster):
    c, h = cluster["client"], cluster["headers"]
    nodes._draining.add("local")
    try:
        r = c.post("/nodes/local/maintenance", json={"target_node": "n2"}, headers=h)
        assert r.status_code == 409 and "already being drained" in r.json()["detail"]
    finally:
        nodes._draining.discard("local")
    assert c.post("/nodes/local/maintenance", json={"target_node": "n2"}, headers=h).status_code == 202
    assert "local" not in nodes._draining
