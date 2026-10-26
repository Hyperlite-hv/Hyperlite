"""migration.start: what POST /vms/{name}/migrate does, for a caller that is not a route (DRS applying its moves on
the leader, #334): the same checks, the VM's lock, the task and the job, in the name it acts for."""

import threading

import libvirt
import pytest
from fastapi import HTTPException

from app.core import cluster_compat, iscsi, maintenance
from app.routers.vms import migration


class Domain:
    def __init__(self, active=True):
        self.active = active

    def isActive(self):
        return self.active


class Conn:
    def __init__(self, domains):
        self.domains = domains

    def lookupByName(self, name):
        if name not in self.domains:
            raise libvirt.libvirtError("no domain")
        return self.domains[name]

    def close(self):
        pass


@pytest.fixture()
def nodes(database, monkeypatch, daemon_libvirt):
    hosts = {None: Conn({"web": Domain(), "off": Domain(active=False)}), "n2": Conn({})}
    monkeypatch.setattr(migration, "open_conn", lambda node=None, readonly=False: hosts[node])
    daemon_libvirt(hosts[None], nodes={"n2": hosts["n2"]})  # the compatibility check is the daemon's
    monkeypatch.setattr(cluster_compat, "check_vm_migration", lambda src, dst, domain: [])
    monkeypatch.setattr(maintenance, "refuse_if_in_maintenance", lambda node, what: None)
    monkeypatch.setattr(iscsi, "refuse_if_iscsi", lambda domain, what: None)
    ran, done = [], threading.Event()

    def job(task_id, username, source_node, target_node, vm_name):
        ran.append((username, source_node, target_node, vm_name))
        done.set()

    monkeypatch.setattr(migration, "_migrate_vm_job", job)
    return ran, done


def test_a_migration_started_for_drs_runs_the_routes_job_in_its_name(nodes):
    ran, done = nodes
    answer = migration.start("web", None, "n2", "drs")
    assert answer["status"] == "running" and answer["task_id"]
    assert done.wait(5) and ran == [("drs", None, "n2", "web")]


@pytest.mark.parametrize(
    ("vm", "target", "status"),
    [("web", "local", 422), ("ghost", "n2", 404), ("off", "n2", 409)],
)
def test_it_refuses_what_the_route_refuses(nodes, vm, target, status):
    ran, _done = nodes
    with pytest.raises(HTTPException) as e:
        migration.start(vm, None, target, "drs")
    assert e.value.status_code == status and ran == []
