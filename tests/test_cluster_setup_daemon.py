"""Forming the cluster through hyperlite-daemon (app/daemon/ops/cluster.py, cluster.setup_*; docs/design/privilege-
separation.md, section 14, 4e): Corosync's files and services, hyperlite-cfs's mode and the shared configuration are
root's, so the routes ask the daemon, and keep their answers: a cluster's refusal 409, a forged ticket 403,
hyperlite-cfs not answering 503. The steps themselves are tests/test_cluster_setup.py's."""

import pytest

from app.core import cluster_setup
from app.core.cfs_client import CfsError


@pytest.fixture()
def admin(auth_headers):
    return auth_headers("ana")


def test_the_routes_ask_the_daemon_with_the_callers_name(client, admin, monkeypatch):
    calls = []
    monkeypatch.setattr(
        cluster_setup,
        "create",
        lambda name, address, username: calls.append(("create", name, username)) or {"in_cluster": True},
    )
    monkeypatch.setattr(
        cluster_setup, "set_primary", lambda name, username: calls.append(("primary", name, username)) or {"ok": 1}
    )
    r = client.post("/cluster/create", json={"name": "lab", "address": "192.0.2.1"}, headers=admin)
    assert r.status_code == 200 and r.json() == {"in_cluster": True}
    assert client.post("/cluster/principal", json={"name": "n1"}, headers=admin).status_code == 200
    assert calls == [("create", "lab", "ana"), ("primary", "n1", "ana")]


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (cluster_setup.ClusterError("This node is already in a cluster"), 409),
        (CfsError(1, "down"), 503),
        (OSError("disk"), 503),
    ],
)
def test_the_daemons_refusals_keep_the_routes_answers(client, admin, monkeypatch, error, status):
    def fail(*args):
        raise error

    monkeypatch.setattr(cluster_setup, "join", fail)
    body = {"information": "x", "address": "192.0.2.3", "confirmation": "lab"}
    r = client.post("/cluster/join", json=body, headers=admin)
    assert r.status_code == status
    if status == 409:
        assert r.json()["detail"] == "This node is already in a cluster"


def test_a_joining_node_is_let_in_by_its_ticket_without_an_account(client, monkeypatch):
    seen = []

    def accept(ticket, name, address, ssh_key, fingerprint=None, auth_keys=None):
        seen.append((ticket, name))
        if ticket != "good":
            raise PermissionError("Invalid or expired ticket")
        return {"members": [name]}

    monkeypatch.setattr(cluster_setup, "accept_member", accept)
    body = {"ticket": "good", "name": "n3", "address": "192.0.2.3", "ssh_key": "ssh-ed25519 AAAA"}
    assert client.post("/cluster/members", json=body).json() == {"members": ["n3"]}
    r = client.post("/cluster/members", json={**body, "ticket": "forged"})
    assert r.status_code == 403 and "ticket" in r.json()["detail"]
    assert seen == [("good", "n3"), ("forged", "n3")]


def test_a_viewer_cannot_form_a_cluster(client, auth_headers):
    viewer = auth_headers("vic", "viewer")
    assert (
        client.post("/cluster/create", json={"name": "lab", "address": "192.0.2.1"}, headers=viewer).status_code == 403
    )
