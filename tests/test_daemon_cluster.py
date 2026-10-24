"""The cluster's keys held by hyperlite-daemon (app/daemon/ops/cluster.py): the API names a registered node, whose
address the daemon reads itself, or an address checked before anything is run with root's key."""

from datetime import UTC, datetime

import pytest

from app.core import cluster, privileged
from app.daemon.registry import DaemonError


@pytest.fixture()
def registered(database, monkeypatch):
    from app.domain.node import NodeSpec

    cluster._nodes().create(
        NodeSpec(name="n2", hostname="10.0.0.2", ssh_user="root", ssh_port=22), datetime.now(UTC).isoformat(), "online"
    )
    done = []
    monkeypatch.setattr(cluster, "ensure_known_host", lambda node: done.append(("known", node["hostname"])))
    monkeypatch.setattr(cluster, "ensure_reverse_trust", lambda node: done.append(("reverse", node["name"])))
    monkeypatch.setattr(cluster, "forget_known_host", lambda node: done.append(("forget", node["hostname"])))
    monkeypatch.setattr(cluster, "revoke_reverse_trust", lambda name: done.append(("revoke", name)))
    return done


def test_the_trust_is_given_to_the_address_the_registry_holds(registered):
    assert privileged.run("cluster.trust", node="n2") == {"reverse_error": None}
    privileged.run("cluster.forget", node="n2")
    assert registered == [("known", "10.0.0.2"), ("reverse", "n2"), ("forget", "10.0.0.2"), ("revoke", "n2")]


def test_a_node_the_registry_does_not_hold_is_refused(registered):
    for op in ("cluster.trust", "cluster.forget"):
        with pytest.raises(DaemonError) as e:
            privileged.run(op, node="ghost")
        assert e.value.kind == "not_found"
    assert registered == []


def test_a_failed_reverse_trust_is_said_and_the_registration_stands(registered, monkeypatch):
    def unreachable(node):
        raise RuntimeError("Unable to create /root/.hyperlite-reverse on n2")

    monkeypatch.setattr(cluster, "ensure_reverse_trust", unreachable)
    assert "Unable to create" in privileged.run("cluster.trust", node="n2")["reverse_error"]


@pytest.mark.parametrize(
    ("hostname", "ssh_user"),
    [("-oProxyCommand=sh", "root"), ("10.0.0.2", "-oProxyCommand=sh"), ("host name", "root"), ("10.0.0.2", "Root")],
)
def test_an_address_ssh_could_read_as_an_option_is_never_tried(monkeypatch, hostname, ssh_user):
    monkeypatch.setattr(cluster, "test_node_connection", lambda *a: pytest.fail("never connected"))
    with pytest.raises(DaemonError) as e:
        privileged.run("cluster.test_node", hostname=hostname, ssh_user=ssh_user, ssh_port=22)
    assert e.value.kind == "refused"


@pytest.mark.parametrize(("old", "new"), [("n2", "../../etc/x"), ("../n2", "n3"), ("n2", "a/b")])
def test_a_rename_never_names_a_file_outside_the_reverse_keys(monkeypatch, old, new):
    monkeypatch.setattr(cluster, "rename_reverse_trust", lambda *a: pytest.fail("never renamed"))
    with pytest.raises(DaemonError) as e:
        privileged.run("cluster.rename_trust", node=old, new_name=new)
    assert e.value.kind == "refused"


def test_the_public_key_is_given_the_private_one_never(monkeypatch):
    monkeypatch.setattr(cluster, "get_cluster_pubkey", lambda: "ssh-ed25519 AAAA hyperlite-cluster")
    assert privileged.run("cluster.pubkey") == {"public_key": "ssh-ed25519 AAAA hyperlite-cluster"}
