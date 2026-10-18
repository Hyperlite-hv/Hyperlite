"""The node poll (app/core/cluster.py::_poll_once) reports the protected VMs of a node that went down, from the node
leading the cluster. A node that saw it go down before it led (the leader was the node that went down) reports it once
it leads, if it is still down."""

import pytest

from app.core import cluster, cluster_lead


class Nodes:
    """The nodes table as the poll uses it: update_status returns the previous status."""

    def __init__(self):
        self.status = {}

    def update_status(self, node_id, status, at):
        prev, self.status[node_id] = self.status.get(node_id), status
        return prev


@pytest.fixture()
def poll(database, monkeypatch):
    from app.core import ha

    state = {"up": True, "leader": True, "alerts": []}
    monkeypatch.setattr(
        cluster, "list_nodes", lambda: [{"id": 1, "name": "pve-a", "hostname": "a", "ssh_user": "root", "ssh_port": 22}]
    )
    monkeypatch.setattr(cluster, "test_node_connection", lambda *a: (state["up"], ""))
    nodes = Nodes()
    monkeypatch.setattr(cluster, "_nodes", lambda: nodes)
    monkeypatch.setattr(cluster, "_check_node_boot", lambda node: None)
    monkeypatch.setattr(cluster, "_unreported", set())
    monkeypatch.setattr(cluster_lead, "in_cluster", lambda: True)
    monkeypatch.setattr(cluster_lead, "is_leader", lambda: state["leader"])
    monkeypatch.setattr(ha, "alert_for_down_node", lambda name: state["alerts"].append(name))
    monkeypatch.setattr(ha, "sync_protected_vms", lambda: None)
    return state


def run(state, **changes):
    state.update(changes)
    cluster._poll_once()


def test_the_leader_reports_a_node_that_went_down_once(poll):
    run(poll)  # first sight: online
    run(poll, up=False)
    run(poll)
    assert poll["alerts"] == ["pve-a"]


def test_a_node_that_saw_it_before_leading_reports_it_once_it_leads(poll):
    run(poll, leader=False)
    run(poll, up=False)  # the leader was the node that went down: nobody reports it now
    assert poll["alerts"] == []
    run(poll, leader=True)  # this node took the lead: the node is still down
    run(poll)
    assert poll["alerts"] == ["pve-a"]


def test_a_node_back_before_this_one_leads_is_not_reported(poll):
    run(poll, leader=False)
    run(poll, up=False)
    run(poll, up=True)
    run(poll, leader=True)
    run(poll)
    assert poll["alerts"] == []


def test_a_node_down_at_the_first_poll_is_not_reported(poll):
    run(poll, up=False)  # no previous status: nothing went down while this node watched
    run(poll)
    assert poll["alerts"] == []


def test_in_a_cluster_each_node_refreshes_only_the_protected_vms_it_hosts(database, monkeypatch):
    from app.core import ha

    rows = [{"vm_name": "mine", "node": "local"}, {"vm_name": "theirs", "node": "pve-b"}]
    opened = []
    monkeypatch.setattr(ha, "list_protected", lambda: rows)
    monkeypatch.setattr(ha, "open_conn", lambda key=None: opened.append(key) or (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(cluster_lead, "in_cluster", lambda: True)
    ha.sync_protected_vms()
    assert opened == [None]  # its own libvirt, for its own VM only
    opened.clear()
    monkeypatch.setattr(cluster_lead, "in_cluster", lambda: False)  # a controller of SSH-driven nodes: all of them
    ha.sync_protected_vms()
    assert opened == [None, "pve-b"]
