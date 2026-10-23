"""A node of a cluster that lost the quorum changes nothing (app/main.py read_only_without_quorum, cluster_lead
.read_only_reason), as Proxmox makes /etc/pve read-only: its configuration would diverge from the majority's. Reading and
signing in and out still work, to see why."""

import logging

import pytest

from app.core import cluster_lead
from app.core.cfs_client import NotFound, Status
from app.repositories.cfs import shadow


class Daemon:
    def __init__(self):
        self.quorate = True
        self.down = False
        self.counter = 1000

    def status(self):
        if self.down:
            raise ConnectionRefusedError("hyperlite-cfs is not running")
        return Status(version=1, checksum="00", quorate=self.quorate, mode="cluster", entries=0, bytes=0)

    def next_id(self):  # a new row's id in a cluster (app/repositories/cfs/ids.py)
        self.counter += 1
        return self.counter

    def get(self, path):
        raise NotFound(1, "no such entry")


@pytest.fixture()
def node(client, auth_headers, monkeypatch):
    headers = auth_headers("ana")  # signed in before the node joins: the account exists
    daemon = Daemon()
    monkeypatch.setenv("HYPERLITE_CFS_SHADOW", "1")
    monkeypatch.setattr(shadow, "_get_client", lambda: daemon)
    monkeypatch.setattr(shadow, "drain", lambda limit=500: 0)  # the copy is not what is tested here
    monkeypatch.setattr(shadow, "confirm", lambda wanted, timeout=None: None)  # nor its confirmation
    monkeypatch.setattr(cluster_lead, "QUORUM_TTL_S", 0)
    monkeypatch.setattr(cluster_lead, "STATUS_TTL_S", 0)
    cluster_lead.forget()
    yield client, headers, daemon
    cluster_lead.forget()


def test_with_the_quorum_changes_are_made(node):
    client, headers, _ = node
    assert client.post("/groups", json={"name": "ops"}, headers=headers).status_code == 201


def test_without_the_quorum_nothing_changes_and_reading_still_works(node):
    client, headers, daemon = node
    daemon.quorate = False
    r = client.post("/groups", json={"name": "ops"}, headers=headers)
    assert r.status_code == 503 and "lost the cluster's quorum" in r.json()["detail"]
    assert client.post("/vms/web/start", headers=headers).status_code == 503  # VMs too, as on Proxmox
    assert client.delete("/groups/1", headers=headers).status_code == 503
    assert client.get("/groups", headers=headers).status_code == 200
    assert client.get("/groups", headers=headers).json()["items"] == []  # nothing was created


def test_signing_in_and_out_still_work_without_the_quorum(node):
    client, _, daemon = node
    daemon.quorate = False
    r = client.post("/auth/login", data={"username": "ana", "password": "correct horse battery"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    assert client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_a_cluster_service_that_does_not_answer_is_read_only_too(node):
    client, headers, daemon = node
    assert client.get("/groups", headers=headers).status_code == 200
    assert cluster_lead.mode() == "cluster"  # this node knows it is in a cluster
    daemon.down = True  # then its hyperlite-cfs stops answering
    r = client.post("/groups", json={"name": "ops"}, headers=headers)
    assert r.status_code == 503 and "does not answer" in r.json()["detail"]


def test_the_quorum_back_changes_are_made_again(node):
    client, headers, daemon = node
    daemon.quorate = False
    assert client.post("/groups", json={"name": "ops"}, headers=headers).status_code == 503
    daemon.quorate = True
    assert client.post("/groups", json={"name": "ops"}, headers=headers).status_code == 201


def test_the_quorum_back_the_next_change_is_made_at_once(node, monkeypatch):
    """A refusal is not kept: the Cluster page shows the quorum as soon as hyperlite-cfs has it again, and the change
    made then must not be refused on an answer from the seconds before."""
    client, headers, daemon = node
    monkeypatch.setattr(cluster_lead, "QUORUM_TTL_S", 60)
    daemon.quorate = False
    assert client.post("/groups", json={"name": "ops"}, headers=headers).status_code == 503
    daemon.quorate = True
    assert client.post("/groups", json={"name": "ops"}, headers=headers).status_code == 201


def test_a_node_that_never_heard_its_daemon_is_not_blocked(node):
    """Not known to be in a cluster (a single node whose hyperlite-cfs never answered): nothing is refused."""
    client, headers, daemon = node
    daemon.down = True
    assert client.post("/groups", json={"name": "ops"}, headers=headers).status_code != 503


def test_outside_a_cluster_nothing_is_refused(client, auth_headers):
    headers = auth_headers("ana")
    assert client.post("/groups", json={"name": "ops"}, headers=headers).status_code == 201


def test_background_work_waits_while_the_quorum_is_lost(node, caplog):
    caplog.set_level(logging.INFO, logger="app.core.cluster_lead")
    _, _, daemon = node
    daemon.quorate = False
    assert cluster_lead.paused("Scheduled backups") and cluster_lead.paused("Scheduled backups")
    assert sum("Scheduled backups paused" in r.message for r in caplog.records) == 1  # said once per pause
    daemon.quorate = True
    assert not cluster_lead.paused("Scheduled backups")
    assert any("Scheduled backups resumed" in r.message for r in caplog.records)


def test_vms_start_at_boot_once_the_quorum_is_there(monkeypatch):
    from app.core import vm_boot

    answers = iter([True, True, False])  # no quorum yet at start, then the cluster forms
    started = []
    monkeypatch.setattr(cluster_lead, "paused", lambda what: next(answers))
    monkeypatch.setattr(vm_boot, "QUORUM_WAIT_S", 0)
    monkeypatch.setattr(vm_boot, "_local_boot_id", lambda: "boot-1")
    monkeypatch.setattr(vm_boot, "boot_if_new", lambda node, boot_id: started.append((node, boot_id)))
    vm_boot._boot_when_writable()
    assert started == [("local", "boot-1")]
