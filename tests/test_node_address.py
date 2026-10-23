"""A node's address and SSH user are checked before any connection to it, on every path that registers one
(GHSA-x3f5-r8vr-pvgw): nothing ssh could read as an option, nor libvirt as a part of its URI."""

import pytest

from app.core import cluster


@pytest.mark.parametrize(
    ("hostname", "ssh_user"),
    [
        ("node2.example.org", "-oProxyCommand=x"),
        ("-oProxyCommand=x", "root"),
        ("h/system?command=x", "root"),
        ("node2\n", "root"),
        ("node2", "root\n"),
        ("", "root"),
    ],
)
def test_what_ssh_or_libvirt_would_misread_is_refused_everywhere(client, auth_headers, monkeypatch, hostname, ssh_user):
    monkeypatch.setattr(cluster.libvirt, "openReadOnly", lambda uri: pytest.fail("a connection was opened"))
    monkeypatch.setattr(cluster.subprocess, "run", lambda *a, **k: pytest.fail("a command was run"))
    with pytest.raises(ValueError):
        cluster.check_address(hostname, ssh_user)
    assert cluster.test_node_connection(hostname, ssh_user, 22)[0] is False
    with pytest.raises(RuntimeError):
        cluster.register_node("n2", hostname, ssh_user, 22, "admin")
    with pytest.raises(ValueError):
        cluster.ensure_known_host({"hostname": hostname, "ssh_user": ssh_user, "ssh_port": 22})
    admin = auth_headers("root", "admin")
    r = client.post("/nodes", json={"name": "n2", "hostname": hostname, "ssh_user": ssh_user}, headers=admin)
    assert r.status_code == 422


def test_a_plain_address_and_user_are_accepted():
    for host in ("node2", "192.168.1.20", "fd00::20", "node-2.lab.example.org"):
        cluster.check_address(host, "root")
    cluster.check_address("node2", "hyper_admin")
