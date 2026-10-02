"""Who runs what once several nodes run Hyperlite on one configuration (app/core/cluster_lead.py): each node runs the
jobs of the VMs it hosts on its own schedule, one node at a time runs the cluster-wide work."""

from datetime import UTC, datetime, timedelta

import libvirt
import pytest

from app.core import backups, cluster_lead, replication, vm_cleanup
from app.core.cfs_client import Locked, NotFound, Status
from app.core.database import get_conn
from app.repositories.cfs import ids, shadow
from tests.test_replication import job_payload, vms  # noqa: F401  (fixture)


class Daemon:
    path = "fake"

    def __init__(self, mode="cluster"):
        self.mode = mode
        self.down = False
        self.holder = None

    def status(self):
        if self.down:
            raise ConnectionRefusedError("down")
        return Status(version=1, checksum="00", quorate=True, mode=self.mode, entries=0, bytes=0)

    def next_id(self):
        return 100

    def get(self, path):
        raise NotFound(1, "no such entry")

    def lock(self, name, owner, ttl=60):
        if self.down:
            raise ConnectionRefusedError("down")
        if self.holder not in (None, owner):
            raise Locked(5, self.holder)
        self.holder = owner


@pytest.fixture()
def daemon(database, monkeypatch):
    fake = Daemon()
    monkeypatch.setenv("HYPERLITE_CFS_SHADOW", "1")
    monkeypatch.setattr(shadow, "_get_client", lambda: fake)
    monkeypatch.setattr(cluster_lead, "STATUS_TTL_S", 0)  # every call asks the daemon
    cluster_lead.forget()
    ids.forget()
    yield fake
    cluster_lead.forget()


def test_a_single_node_runs_everything(database, monkeypatch):
    monkeypatch.delenv("HYPERLITE_CFS_SHADOW", raising=False)
    cluster_lead.forget()
    assert not cluster_lead.in_cluster() and cluster_lead.is_leader()
    assert cluster_lead.renew() is False  # no lock to take


def test_a_daemon_in_local_mode_is_a_single_node(daemon):
    daemon.mode = "local"
    assert not cluster_lead.in_cluster() and cluster_lead.is_leader()


def test_one_node_holds_the_lead_and_keeps_it_across_its_restart(daemon):
    assert cluster_lead.in_cluster() and not cluster_lead.is_leader()  # not before the lock is taken
    assert cluster_lead.renew() and cluster_lead.is_leader()
    assert daemon.holder == "hv-test"
    cluster_lead.forget()  # this node restarts: the lock is still its own
    assert cluster_lead.renew() and cluster_lead.is_leader()

    daemon.holder = "pve-b"  # this node lost it (left the membership), another took it
    assert not cluster_lead.renew() and not cluster_lead.is_leader()


def test_a_daemon_that_stops_answering_ends_the_lead_but_not_the_cluster(daemon):
    assert cluster_lead.renew()
    daemon.down = True
    assert not cluster_lead.renew() and not cluster_lead.is_leader()
    assert cluster_lead.in_cluster()  # the last mode it reported holds


def test_before_the_daemon_ever_answered_a_node_is_careful(daemon):
    daemon.down = True
    assert cluster_lead.in_cluster() and not cluster_lead.is_leader()


def test_a_per_vm_backup_job_runs_on_the_node_hosting_the_vm(daemon, monkeypatch):
    ran = []
    monkeypatch.setattr(backups, "run_backup", lambda vm, *a, **k: ran.append(vm))
    monkeypatch.setattr(backups, "_hosted_vms", lambda: {"web"})
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    with get_conn() as db:
        for vm in ("web", "elsewhere"):
            db.execute(
                "INSERT INTO backup_jobs (vm_name, frequence, heure, cible_dir, prochaine_execution) "
                "VALUES (?, 'quotidien', '02:00', '/b', ?)",
                (vm, past),
            )
        db.commit()
    backups.run_due_schedules(datetime.now(UTC))
    assert ran == ["web"]
    with get_conn() as db:
        nexts = dict(db.execute("SELECT vm_name, prochaine_execution FROM backup_jobs").fetchall())
    assert nexts["elsewhere"] == past and nexts["web"] > past  # the other node's job is left for it


def test_in_a_cluster_each_node_replicates_on_its_own_schedule(daemon, vms, monkeypatch):  # noqa: F811
    started = []
    monkeypatch.setattr(replication, "start_job", lambda job, user: started.append(job["nom"]))
    job = replication.save_job(job_payload())
    shared = replication._store().get_job(job["id"])["prochaine_execution"]
    now = datetime.now(UTC) + timedelta(seconds=1)
    replication.run_due(now)
    assert started == ["to site B"]
    with get_conn() as db:  # the shared row still says when the other nodes run it
        assert db.execute("SELECT prochaine_execution FROM replication_jobs").fetchone()[0] == shared
    own = (now + timedelta(minutes=15)).isoformat()
    assert replication.list_jobs()[0]["prochaine_execution"] == own  # what this node shows is its own
    replication.run_due(now)
    assert started == ["to site B"]

    # An edit (here or on another node) starts the schedule again from the edit.
    replication.save_job(job_payload(intervalle_minutes=30), job_id=job["id"])
    replication.run_due(now + timedelta(seconds=1))
    assert started == ["to site B", "to site B"]

    replication.delete_job(job["id"])
    with get_conn() as db:
        assert db.execute("SELECT COUNT(*) FROM schedule_state").fetchone()[0] == 0


def test_a_single_node_keeps_moving_the_shared_next_run(database, vms, monkeypatch):  # noqa: F811
    monkeypatch.setattr(replication, "start_job", lambda job, user: None)
    job = replication.save_job(job_payload())
    now = datetime.now(UTC) + timedelta(seconds=1)
    replication.run_due(now)
    with get_conn() as db:
        assert (
            db.execute("SELECT prochaine_execution FROM replication_jobs").fetchone()[0]
            == (now + timedelta(minutes=15)).isoformat()
        )
        assert db.execute("SELECT COUNT(*) FROM schedule_state").fetchone()[0] == 0
    assert job["id"]


class _NoVm:
    def lookupByName(self, name):
        raise libvirt.libvirtError("no domain")


def _cleanup_rows():
    with get_conn() as db:
        return [r[0] for r in db.execute("SELECT vm_name FROM vm_auto_cleanup").fetchall()]


def test_automatic_deletion_leaves_a_vm_of_another_node_alone(daemon):
    with get_conn() as db:
        db.execute(
            "INSERT INTO vm_auto_cleanup (vm_name, inactive_days, last_active_at, created_at) VALUES ('web', 7, 't', 't')"
        )
        db.commit()
    vm_cleanup._check_vm(_NoVm(), {"vm_name": "web"})
    assert _cleanup_rows() == ["web"]

    daemon.mode = "local"  # alone, a VM it does not have was deleted: the row goes
    vm_cleanup._check_vm(_NoVm(), {"vm_name": "web"})
    assert _cleanup_rows() == []
