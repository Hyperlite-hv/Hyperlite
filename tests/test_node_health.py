"""A node the cluster sees down is never waited for (app/core/node_health.py): the lists name it at once, a call to it
fails at once, and a node switched off seconds ago, not seen down yet, is waited for a few seconds at most.

Seen in production on 2026-10-21: with bico switched off, the VM creation wizard listed no ISO for about 16 s, the
ISO listing asking bico over SSH twice; a libvirt connection to it waited over two minutes."""

import os
import stat

import libvirt
import pytest
from fastapi import HTTPException

from app.core import cluster, iso_share, node_health, statd


def _set_status(database, name, status):
    with database.get_conn() as conn:
        conn.execute("UPDATE nodes SET status = ? WHERE name = ?", (status, name))
        conn.commit()


def _node(name, status="online"):
    return {"name": name, "hostname": f"{name}.lan", "ssh_user": "root", "ssh_port": 22, "status": status}


def test_a_node_the_poller_found_offline_is_down_unless_it_is_sending(monkeypatch):
    nodes = [_node("pve-a", "offline"), _node("pve-b", "offline"), _node("pve-c")]
    monkeypatch.setattr(statd, "table", lambda: {"pve-a": {"vms": [], "age_s": 2.0, "stale": False}})
    assert node_health.down(nodes) == {"pve-b"}
    assert node_health.down([_node("pve-c")]) == set()


def test_a_member_whose_status_alone_stopped_is_not_down(monkeypatch):
    """Its Hyperlite service may be stopped while its VMs run and libvirt answers: a migration off it must work."""
    monkeypatch.setattr(statd, "table", lambda: {"pve-a": {"vms": [], "age_s": 60.0, "stale": True}})
    assert node_health.down([_node("pve-a"), _node("pve-b")]) == set()


def test_without_statuses_the_poll_decides(monkeypatch):
    def broken():
        raise OSError("hyperlite-cfs is not answering")

    monkeypatch.setattr(statd, "table", broken)
    assert node_health.down([_node("pve-a", "offline"), _node("pve-b")]) == {"pve-a"}


def test_the_iso_list_names_a_node_seen_down_without_asking_it(cluster, client, auth_headers, database):
    (cluster["local"] / "debian.iso").write_bytes(b"d")
    (cluster["add_node"]("antho", "10.0.0.2") / "ubuntu.iso").write_bytes(b"u")
    (cluster["add_node"]("bico", "10.0.0.3", reachable=False) / "alma.iso").write_bytes(b"a")
    _set_status(database, "bico", "offline")

    r = client.get("/isos/cluster", headers=auth_headers("viewer", role="viewer"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted((i["node"], i["name"]) for i in body["isos"]) == [("antho", "ubuntu.iso"), ("local", "debian.iso")]
    assert body["unreachable"] == [{"node": "bico", "error": iso_share.OFFLINE}]
    assert cluster["roots"]["10.0.0.3"]["commands"] == []  # not even tried
    assert len(cluster["roots"]["10.0.0.2"]["commands"]) == 1  # one connection lists a library


def test_a_node_marked_offline_that_sends_its_status_has_its_isos_listed(cluster, client, auth_headers, database, monkeypatch):  # fmt: skip
    (cluster["add_node"]("bico", "10.0.0.3") / "alma.iso").write_bytes(b"a")
    _set_status(database, "bico", "offline")
    monkeypatch.setattr(statd, "table", lambda: {"bico": {"vms": [], "age_s": 1.0, "stale": False}})
    body = client.get("/isos/cluster", headers=auth_headers("viewer", role="viewer")).json()
    assert [(i["node"], i["name"]) for i in body["isos"]] == [("bico", "alma.iso")] and body["unreachable"] == []


def test_a_library_on_either_layout_is_listed(cluster):
    (cluster["add_node"]("old", "10.0.0.2", layout="old") / "a.iso").write_bytes(b"a")
    (cluster["add_node"]("new", "10.0.0.3", layout="new") / "b.iso").write_bytes(b"b")
    names = sorted(i["name"] for i in iso_share.cluster_isos()["isos"])
    assert names == ["a.iso", "b.iso"]
    command = cluster["roots"]["10.0.0.2"]["commands"][0]
    assert command[:2] == ["sh", "-c"] and command[4:] == list(iso_share.LIBRARY_DIRS)


def test_a_libvirt_connection_to_a_node_seen_down_is_refused_at_once(database, monkeypatch):
    from app.core import libvirt_utils

    monkeypatch.setattr(cluster, "get_node", lambda name: _node(name, "offline"))
    monkeypatch.setattr(statd, "table", lambda: {})

    def no_connection(uri):
        raise AssertionError("a node seen down must not be connected to")

    monkeypatch.setattr(libvirt, "open", no_connection)
    with pytest.raises(HTTPException) as e:
        libvirt_utils.open_conn("bico")
    assert e.value.status_code == 503 and "bico" in e.value.detail


def test_a_storage_list_of_a_node_seen_down_answers_at_once(client, auth_headers, monkeypatch):
    monkeypatch.setattr(cluster, "get_node", lambda name: _node(name, "offline"))
    monkeypatch.setattr(statd, "table", lambda: {})
    monkeypatch.setattr(libvirt, "open", lambda uri: (_ for _ in ()).throw(AssertionError("connected")))
    r = client.get("/storage?node=bico", headers=auth_headers("viewer", role="viewer"))
    assert r.status_code == 503 and "offline" in r.json()["detail"]


def test_a_node_not_seen_down_that_does_not_answer_is_said_unreachable(client, auth_headers, monkeypatch):
    """Lab: a node switched off seconds ago made the storage list fail with an internal error."""
    monkeypatch.setattr(cluster, "get_node", lambda name: _node(name))
    monkeypatch.setattr(cluster, "ensure_known_host", lambda node: None)

    def timed_out(uri):
        raise libvirt.libvirtError("Cannot recv data: ssh: connect to host bico.lan port 22: Connection timed out")

    monkeypatch.setattr(libvirt, "open", timed_out)
    r = client.get("/storage?node=bico", headers=auth_headers("viewer", role="viewer"))
    assert r.status_code == 502 and r.json()["detail"].startswith("Node unreachable:")


def test_the_connections_this_node_opens_are_bounded_and_a_migration_uri_is_not(monkeypatch):
    """A migration's destination URI is opened by the source node's libvirtd, which may run a version without the
    script: it keeps plain ssh."""
    monkeypatch.setattr(cluster, "ensure_known_host", lambda node: None)
    node = _node("bico")
    plain = cluster.build_libvirt_uri(node)
    assert "command=" not in plain
    assert cluster.libvirt_uri_from_here(node) == f"{plain}&command={cluster.NODE_SSH}"


def test_the_ssh_libvirt_uses_sets_a_connection_timeout():
    script = cluster.NODE_SSH
    assert script.read_text().splitlines()[-1] == f'exec ssh -o ConnectTimeout={node_health.CONNECT_TIMEOUT_S} "$@"'
    assert os.stat(script).st_mode & stat.S_IXUSR


def test_the_vm_list_names_a_member_seen_down_without_asking_it(client, auth_headers, monkeypatch):
    from types import SimpleNamespace

    from app.core import cluster_view
    from app.routers.vms import inventory

    members = [_node("pve-a"), _node("bico", "offline")]
    monkeypatch.setattr(cluster_view, "members", lambda: members)
    monkeypatch.setattr(statd, "table", lambda: {})
    asked = []

    def forward(node, username, method, path):
        asked.append(node["name"])
        return 200, [{"name": "from-" + node["name"]}]

    monkeypatch.setattr(cluster_view, "forward", forward)
    monkeypatch.setattr(inventory, "open_conn", lambda node=None: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(inventory, "_domain_summaries", lambda c, node: [])
    body = client.get("/vms?cluster=true", headers=auth_headers("ana")).json()
    assert asked == ["pve-a"] and body["unreachable"] == ["bico"]


def test_a_node_seen_down_is_not_probed(monkeypatch):
    import subprocess

    from app.core import host_stats

    monkeypatch.setattr(statd, "table", lambda: {})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("probed")))
    assert host_stats.run_probe_script("true", _node("bico", "offline")) is None
