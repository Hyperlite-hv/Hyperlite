"""Creating and joining a cluster (app/core/cluster_setup.py): the files and commands of Corosync, the shared
configuration in hyperlite-cfs, the one-time join ticket, the pinned certificate of the member, and the node that joins
giving up its own configuration. Commands, the daemon and the other node are fakes; Corosync itself is covered by
cfs/tests/cluster."""

import base64
import datetime as dt
import http.server
import json
import ssl
import threading
from typing import ClassVar

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.core import cluster, cluster_lead, cluster_setup, config_copy, tls_certs
from app.core.database import get_conn
from app.repositories.cfs import shadow
from tests.test_cfs_inbound import Cluster

ADDRESS = "192.0.2.10"
KEY_A = "ssh-ed25519 " + "A" * 68 + " hyperlite-cluster"
KEY_B = "ssh-ed25519 " + "B" * 68 + " hyperlite-cluster"
AUTH_KEYS = {
    "keys": [{"kid": "3fee8db48546834d", "public": base64.b64encode(b"p" * 32).decode(), "created": 1761200000}]
}


def _certificate(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "hyperlite")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    (tmp_path / "hyperlite.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "hyperlite.key").write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return tmp_path / "hyperlite.crt", tmp_path / "hyperlite.key"


@pytest.fixture()
def host(database, tmp_path, monkeypatch):
    """This node: Corosync installed, hyperlite-cfs installed and in local mode, every file under tmp_path."""
    daemon = Cluster()
    daemon.mode = "local"
    commands = []

    def run(args, timeout=60):
        commands.append(" ".join(args))
        if args[:3] == ["systemctl", "restart", shadow.SERVICE]:
            daemon.mode = "cluster" if "--cluster" in (tmp_path / "default-cfs").read_text() else "local"
        return True

    (tmp_path / "corosync").write_text("")
    (tmp_path / "hyperlite-cfs").write_text("")
    (tmp_path / "tls").mkdir()
    _certificate(tmp_path / "tls")
    monkeypatch.setattr(cluster_setup, "COROSYNC_BIN", str(tmp_path / "corosync"))
    monkeypatch.setattr(cluster_setup, "COROSYNC_CONF", tmp_path / "etc" / "corosync.conf")
    monkeypatch.setattr(cluster_setup, "COROSYNC_KEY", tmp_path / "etc" / "authkey")
    monkeypatch.setattr(cluster_setup, "CFS_DEFAULTS", tmp_path / "default-cfs")
    monkeypatch.setattr(cluster_setup, "CFS_DB", tmp_path / "cfs" / "config.db")
    monkeypatch.setattr(cluster_setup, "WAIT_S", 2)
    monkeypatch.setattr(cluster_setup, "JOIN_WAIT_S", 2)
    monkeypatch.setattr(cluster_setup, "_run", run)
    monkeypatch.setattr(cluster_setup, "_local_addresses", lambda: {ADDRESS, "192.0.2.11"})
    monkeypatch.setattr(cluster_setup, "_running_version", lambda: None)
    restarts = []
    monkeypatch.setattr(cluster_setup, "_restart_service", lambda: restarts.append(1))
    monkeypatch.setattr(shadow, "BINARY", str(tmp_path / "hyperlite-cfs"))
    monkeypatch.setattr(shadow, "_get_client", lambda: daemon)
    monkeypatch.setattr(tls_certs, "TLS_DIR", tmp_path / "tls")
    monkeypatch.setattr(cluster, "get_cluster_pubkey", lambda: KEY_A)
    monkeypatch.setattr(cluster, "_authorized_keys_path", lambda: tmp_path / "authorized_keys")
    monkeypatch.setattr(config_copy, "_env_path", lambda: tmp_path / ".env")
    monkeypatch.setattr(cluster_lead, "STATUS_TTL_S", 0)
    (tmp_path / ".env").write_text(
        "HYPERLITE_SECRET_KEY=a-secret-key-of-this-node-0123456789\nHYPERLITE_ENCRYPTION_KEY=its-fernet-key-0123456789abcdef=\nOTHER=1\n"
    )
    daemon.commands = commands
    daemon.restarts = restarts
    daemon.tmp = tmp_path
    return daemon


def test_the_configuration_corosync_gets(host):
    conf = {
        "cluster": "prod",
        "version": 3,
        "members": [
            {"name": "pve-b", "nodeid": 2, "address": "192.0.2.12"},
            {"name": "hv-test", "nodeid": 1, "address": ADDRESS},
        ],
    }
    text = cluster_setup.render(cluster_setup._check_conf(conf))
    assert "cluster_name: prod" in text and "config_version: 3" in text
    assert "crypto_cipher: aes256" in text and "crypto_hash: sha256" in text and "transport: knet" in text
    assert text.index("name: hv-test") < text.index("name: pve-b")  # by node id
    for bad in (
        {**conf, "cluster": "bad name"},
        {**conf, "members": [{"name": "x;rm", "nodeid": 1, "address": ADDRESS}]},
        {**conf, "members": [{"name": "pve-c", "nodeid": 1, "address": "not-an-ip\n}"}]},
        {**conf, "members": conf["members"] + [{"name": "pve-c", "nodeid": 2, "address": "192.0.2.13"}]},
        {**conf, "version": 0},
    ):
        with pytest.raises(cluster_setup.ClusterError):
            cluster_setup._check_conf(bad)


def test_creating_needs_corosync_and_an_address_of_this_node(host):
    with pytest.raises(cluster_setup.ClusterError, match="not an address of this node"):
        cluster_setup.create("prod", "192.0.2.99", "alice")
    with pytest.raises(cluster_setup.ClusterError, match="not an IP address"):
        cluster_setup.create("prod", "pve-a.lan", "alice")
    with pytest.raises(cluster_setup.ClusterError, match="Invalid cluster name"):
        cluster_setup.create("prod;reboot", ADDRESS, "alice")
    (host.tmp / "corosync").unlink()
    with pytest.raises(cluster_setup.ClusterError, match="apt install corosync"):
        cluster_setup.create("prod", ADDRESS, "alice")
    assert host.commands == []


def _create(host):
    with get_conn() as db:
        db.execute("INSERT INTO groups (name) VALUES ('ops')")
        db.commit()
    return cluster_setup.create("prod", ADDRESS, "alice")


def test_creating_a_cluster(host, monkeypatch):
    from app.core import peer_https

    monkeypatch.setattr(peer_https, "own_fingerprint", lambda: "ab" * 32)
    state = _create(host)
    assert state["in_cluster"] and state["name"] == "prod" and state["quorum"]
    assert state["members"] == [{"name": "hv-test", "nodeid": 1, "address": ADDRESS, "votes": 1}]

    key = cluster_setup.COROSYNC_KEY
    assert len(key.read_bytes()) == 256 and oct(key.stat().st_mode & 0o777) == "0o400"
    assert "ring0_addr: 192.0.2.10" in cluster_setup.COROSYNC_CONF.read_text()
    assert cluster_setup.CFS_DEFAULTS.read_text() == "HYPERLITE_CFS_MODE=--cluster\n"
    assert host.commands == [
        "systemctl enable corosync",
        "systemctl restart corosync",
        "systemctl enable hyperlite-cfs.service",
        "systemctl restart hyperlite-cfs.service",
    ]
    # This node's configuration is the cluster's, with what a new member needs.
    assert shadow.enabled() and any(p.startswith("/db/groups/") for p in host.files)
    assert base64.b64decode(host.files[cluster_setup.COROSYNC_KEY_ENTRY]) == key.read_bytes()
    assert host.files["/cluster/ssh/hv-test"] == KEY_A.encode()
    # Its certificate at once: the members call it over HTTPS from the start, and a file can be relayed to it.
    assert json.loads(host.files["/cluster/tls/hv-test"]) == {"sha256": "ab" * 32}
    with get_conn() as db:  # listed in the shared nodes table, which this node leaves itself out of
        assert db.execute("SELECT name, hostname FROM nodes").fetchall()[0][:] == ("hv-test", ADDRESS)
    with pytest.raises(cluster_setup.ClusterError, match="already in a cluster"):
        cluster_setup.create("other", ADDRESS, "alice")


def _information(host):
    info = cluster_setup.join_information()
    return info, json.loads(base64.urlsafe_b64decode(info["information"] + "=="))


def test_the_join_information_lets_one_node_in_once(host):
    _create(host)
    _info, decoded = _information(host)
    assert decoded["cluster"] == "prod" and decoded["address"] == ADDRESS and decoded["port"] == 8000
    assert decoded["fingerprint"] == cluster_setup._certificate_fingerprint()

    with pytest.raises(PermissionError):
        cluster_setup.accept_member("x" * 43, "pve-b", "192.0.2.12", KEY_B)  # a wrong ticket also spends nothing
    answer = cluster_setup.accept_member(decoded["ticket"], "pve-b", "192.0.2.12", KEY_B)
    assert answer["configuration"]["version"] == 2
    assert answer["configuration"]["members"][1] == {"name": "pve-b", "nodeid": 2, "address": "192.0.2.12"}
    assert set(answer["keys"]) == {"HYPERLITE_SECRET_KEY", "HYPERLITE_ENCRYPTION_KEY"}
    assert answer["ssh_keys"] == {"hv-test": KEY_A, "pve-b": KEY_B}
    # This node's Corosync knows the new member before it calls, and it may reach it over SSH.
    assert "name: pve-b" in cluster_setup.COROSYNC_CONF.read_text() and "corosync-cfgtool -R" in host.commands
    assert ("B" * 68) in (host.tmp / "authorized_keys").read_text()
    with get_conn() as db:
        assert {r[0] for r in db.execute("SELECT name FROM nodes")} == {"hv-test", "pve-b"}

    with pytest.raises(PermissionError):  # used
        cluster_setup.accept_member(decoded["ticket"], "pve-c", "192.0.2.13", KEY_B)


def test_a_ticket_expires_and_a_name_or_address_cannot_be_taken_twice(host, monkeypatch):
    _create(host)
    _info, decoded = _information(host)
    with pytest.raises(cluster_setup.ClusterError, match="already named"):
        cluster_setup.accept_member(decoded["ticket"], "hv-test", "192.0.2.12", KEY_B)
    _info, decoded = _information(host)
    with pytest.raises(cluster_setup.ClusterError, match="already uses"):
        cluster_setup.accept_member(decoded["ticket"], "pve-b", ADDRESS, KEY_B)
    _info, decoded = _information(host)
    with pytest.raises(cluster_setup.ClusterError, match="SSH key"):
        cluster_setup.accept_member(decoded["ticket"], "pve-b", "192.0.2.12", "ssh-rsa AAAA; rm -rf /")
    _info, decoded = _information(host)
    later = cluster_setup._now() + dt.timedelta(seconds=cluster_setup.TICKET_TTL_S + 1)
    monkeypatch.setattr(cluster_setup, "_now", lambda: later)
    with pytest.raises(PermissionError):
        cluster_setup.accept_member(decoded["ticket"], "pve-b", "192.0.2.12", KEY_B)


def _answer(me="hv-test", address="192.0.2.11"):
    return {
        "configuration": {
            "cluster": "prod",
            "version": 2,
            "members": [
                {"name": "pve-a", "nodeid": 1, "address": "192.0.2.20"},
                {"name": me, "nodeid": 2, "address": address},
            ],
        },
        "corosync_key": base64.b64encode(b"k" * 256).decode(),
        "keys": {
            "HYPERLITE_SECRET_KEY": "the-clusters-secret-key-0123456789",
            "HYPERLITE_ENCRYPTION_KEY": "the-clusters-fernet-key-0123456789ab=",
        },
        "ssh_keys": {"pve-a": KEY_B, me: KEY_A},
    }


def _blob(**over):
    info = {
        "cluster": "prod",
        "address": "192.0.2.20",
        "port": 8000,
        "fingerprint": "ab" * 32,
        "ticket": "t" * 43,
        **over,
    }
    return base64.urlsafe_b64encode(json.dumps(info).encode()).decode()


def test_joining_replaces_this_nodes_configuration_by_the_clusters(host, monkeypatch):
    from app.core import peer_auth

    sent = []
    monkeypatch.setattr(cluster_setup, "_call_member", lambda info, body: sent.append((info, body)) or _answer())
    monkeypatch.setattr(peer_auth, "_own_keys", lambda: AUTH_KEYS)
    cfs_db = cluster_setup.CFS_DB
    cfs_db.parent.mkdir(parents=True)
    cfs_db.write_bytes(b"its own tree")
    with get_conn() as db:
        db.execute("INSERT INTO groups (name) VALUES ('local-only')")
        db.commit()

    with pytest.raises(cluster_setup.ClusterError, match="Type the cluster's name"):
        cluster_setup.join(_blob(), "192.0.2.11", "wrong", "alice")
    with pytest.raises(cluster_setup.ClusterError, match="not valid"):
        cluster_setup.join(_blob(fingerprint="zz"), "192.0.2.11", "prod", "alice")
    assert sent == []

    done = cluster_setup.join(_blob(), "192.0.2.11", "prod", "alice")
    assert done == {"cluster": "prod", "restart": True} and host.restarts == [1]
    info, body = sent[0]
    assert info["address"] == "192.0.2.20" and body == {
        "ticket": "t" * 43,
        "name": "hv-test",
        "address": "192.0.2.11",
        "ssh_key": KEY_A,
        # Written by the member while the quorum holds: the others call this node as soon as it is up.
        "fingerprint": cluster_setup._certificate_fingerprint(),
        "auth_keys": AUTH_KEYS,
    }
    env = (host.tmp / ".env").read_text()
    assert "HYPERLITE_SECRET_KEY=the-clusters-secret-key-0123456789" in env and "OTHER=1" in env
    assert "a-secret-key-of-this-node" not in env
    assert cluster_setup.COROSYNC_KEY.read_bytes() == b"k" * 256
    assert "name: pve-a" in cluster_setup.COROSYNC_CONF.read_text()
    assert not cfs_db.exists() and len(list(cfs_db.parent.glob("config.db.before-join-*"))) == 1
    assert ("B" * 68) in (host.tmp / "authorized_keys").read_text()
    assert ("A" * 68) not in (host.tmp / "authorized_keys").read_text()  # not its own key
    assert "systemctl stop hyperlite-cfs.service" in host.commands

    # The next start: nothing this node had goes out, and the cluster's tree is applied from its start.
    with get_conn() as db:
        assert db.execute("SELECT COUNT(*) FROM cfs_outbox").fetchone()[0] > 0
    from app.core import database

    database.init_db()
    with get_conn() as db:
        assert db.execute("SELECT COUNT(*) FROM cfs_outbox").fetchone()[0] == 0
        settings = dict(db.execute("SELECT key, value FROM app_settings").fetchall())
    assert "cfs_join_pending" not in settings and "cfs_applied_version" not in settings


def test_an_answer_that_does_not_name_this_node_changes_nothing(host, monkeypatch):
    monkeypatch.setattr(cluster_setup, "_call_member", lambda info, body: _answer(me="someone-else"))
    with pytest.raises(cluster_setup.ClusterError, match="unexpected"):
        cluster_setup.join(_blob(), "192.0.2.11", "prod", "alice")
    assert "a-secret-key-of-this-node" in (host.tmp / ".env").read_text()
    assert not cluster_setup.COROSYNC_KEY.exists() and host.commands == []


def test_each_node_follows_the_shared_configuration(host):
    _create(host)
    conf = json.loads(host.files[cluster_setup.CONF_PATH])
    conf["members"].append({"name": "pve-c", "nodeid": 3, "address": "192.0.2.13"})
    conf["version"] = 5
    host.files[cluster_setup.CONF_PATH] = json.dumps(conf).encode()
    host.files["/cluster/ssh/pve-c"] = KEY_B.encode()
    (host.tmp / "authorized_keys").write_text("ssh-ed25519 KEEP admin\nssh-ed25519 OLD hyperlite-cluster-member\n")
    assert cluster_setup.sync()
    assert "config_version: 5" in cluster_setup.COROSYNC_CONF.read_text()
    keys = (host.tmp / "authorized_keys").read_text()
    assert "KEEP admin" in keys and "OLD" not in keys and ("B" * 68) in keys
    assert not cluster_setup.sync()  # nothing newer


def test_a_configuration_written_but_not_running_is_reloaded_again(host, monkeypatch):
    _create(host)
    cluster_setup.sync()
    written = cluster_setup._local_version()
    running = [written - 1]
    monkeypatch.setattr(cluster_setup, "_running_version", lambda: running[0])
    host.commands.clear()
    assert not cluster_setup.sync()
    assert host.commands == ["corosync-cfgtool -R"]  # its earlier reload did not take: Corosync lacks a member
    running[0] = written
    host.commands.clear()
    cluster_setup.sync()
    assert host.commands == []  # running what is written: nothing to do


def test_removing_a_member(host):
    _create(host)
    _info, decoded = _information(host)
    cluster_setup.accept_member(decoded["ticket"], "pve-b", "192.0.2.12", KEY_B)
    with pytest.raises(cluster_setup.ClusterError, match="cannot remove itself"):
        cluster_setup.remove_member("hv-test", "hv-test", "alice")
    with pytest.raises(cluster_setup.ClusterError, match="to confirm"):
        cluster_setup.remove_member("pve-b", "nope", "alice")
    state = cluster_setup.remove_member("pve-b", "pve-b", "alice")
    assert [m["name"] for m in state["members"]] == ["hv-test"]
    assert "pve-b" not in cluster_setup.COROSYNC_CONF.read_text() and "/cluster/ssh/pve-b" not in host.files
    with get_conn() as db:
        assert {r[0] for r in db.execute("SELECT name FROM nodes")} == {"hv-test"}


class _Member(http.server.BaseHTTPRequestHandler):
    """A member as the API serves it since 1.0: the old address without /api/v1 answers 410."""

    received: ClassVar[list] = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Member.received.append((self.path, json.loads(body)))
        gone = not self.path.startswith("/api/v1/")
        data = json.dumps({"detail": "This address is gone since Hyperlite 1.0"} if gone else {"ok": True}).encode()
        self.send_response(410 if gone else 200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def test_the_member_is_reached_only_through_the_certificate_of_the_join_information(tmp_path):
    cert, key = _certificate(tmp_path)
    server = http.server.HTTPServer(("127.0.0.1", 0), _Member)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        info = {"address": "127.0.0.1", "port": server.server_address[1], "fingerprint": "00" * 32}
        _Member.received.clear()
        with pytest.raises(cluster_setup.ClusterError, match="does not match"):
            cluster_setup._call_member(info, {"ticket": "secret"})
        assert _Member.received == []  # nothing was sent to a certificate that does not match
        info["fingerprint"] = cluster_setup.hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()
        assert cluster_setup._call_member(info, {"ticket": "secret"}) == {"ok": True}
        assert _Member.received == [("/api/v1/cluster/members", {"ticket": "secret"})]
    finally:
        server.shutdown()


def test_the_api(host, client, auth_headers):
    admin, viewer = auth_headers("alice"), auth_headers("olga", role="viewer")
    assert client.get("/cluster", headers=viewer).status_code == 403
    assert client.get("/cluster", headers=admin).json()["in_cluster"] is False
    r = client.post("/cluster/create", json={"name": "prod", "address": "192.0.2.99"}, headers=admin)
    assert r.status_code == 409 and "not an address of this node" in r.json()["detail"]
    r = client.post("/cluster/create", json={"name": "prod", "address": ADDRESS}, headers=admin)
    assert r.status_code == 200 and r.json()["name"] == "prod"
    assert client.post("/cluster/adhesion", headers=viewer).status_code == 403
    info = client.post("/cluster/adhesion", headers=admin).json()
    ticket = json.loads(base64.urlsafe_b64decode(info["information"] + "=="))["ticket"]
    # The joining node has no session here: the ticket is what lets it in.
    bad = {"ticket": "x" * 43, "name": "pve-b", "address": "192.0.2.12", "ssh_key": KEY_B}
    assert client.post("/cluster/members", json=bad).status_code == 403
    r = client.post("/cluster/members", json={**bad, "ticket": ticket})
    assert r.status_code == 200 and r.json()["configuration"]["version"] == 2


def test_a_join_whose_services_do_not_start_keeps_this_nodes_keys(host, monkeypatch):
    monkeypatch.setattr(cluster_setup, "_call_member", lambda info, body: _answer())
    monkeypatch.setattr(cluster_setup, "_run", lambda args, timeout=60: args[:2] != ["systemctl", "restart"])
    with pytest.raises(cluster_setup.ClusterError, match="failed"):
        cluster_setup.join(_blob(), "192.0.2.11", "prod", "alice")
    assert "a-secret-key-of-this-node" in (host.tmp / ".env").read_text()  # its database stays readable
    with get_conn() as db:
        assert db.execute("SELECT value FROM app_settings WHERE key = 'cfs_join_pending'").fetchone() is None
    assert host.restarts == []


def test_the_first_member_is_registered_before_corosync_drops_the_quorum(host, monkeypatch):
    """Going from 1 to 2 members raises the votes Corosync needs to 2: once it reloads, the member alone refuses every
    write until the new node is up. Every shared write must be done by then."""
    from app.core.cfs_client import ReadOnly

    _create(host)
    _info, decoded = _information(host)
    reloaded = []
    run = cluster_setup._run

    def reloading(args, timeout=60):
        if args == ["corosync-cfgtool", "-R"]:
            reloaded.append(1)
        return run(args, timeout)

    def refusing(real):
        def call(*args, **kwargs):
            if reloaded:
                raise ReadOnly(5, "this node is not part of a quorate cluster")
            return real(*args, **kwargs)

        return call

    for method in ("put", "delete", "next_id"):
        monkeypatch.setattr(host, method, refusing(getattr(host, method)))
    monkeypatch.setattr(cluster_setup, "_run", reloading)

    answer = cluster_setup.accept_member(decoded["ticket"], "pve-b", "192.0.2.12", KEY_B)
    assert reloaded and answer["configuration"]["members"][1]["name"] == "pve-b"
    assert answer["ssh_keys"] == {"hv-test": KEY_A, "pve-b": KEY_B} and answer["corosync_key"]
    with get_conn() as db:
        assert {r[0] for r in db.execute("SELECT name FROM nodes")} == {"hv-test", "pve-b"}


def test_a_shared_write_that_fails_leaves_no_trace_of_the_new_node(host, monkeypatch):
    from app.core.cfs_client import ReadOnly

    _create(host)
    _info, decoded = _information(host)
    put = host.put

    def refusing(path, data, expected=-1):
        if path == cluster_setup.CONF_PATH:
            raise ReadOnly(5, "this node is not part of a quorate cluster")
        return put(path, data, expected)

    monkeypatch.setattr(host, "put", refusing)
    with pytest.raises(ReadOnly):
        cluster_setup.accept_member(decoded["ticket"], "pve-b", "192.0.2.12", KEY_B)
    with get_conn() as db:
        assert {r[0] for r in db.execute("SELECT name FROM nodes")} == {"hv-test"}
    assert not any(p.endswith("/pve-b") for p in host.files) and "corosync-cfgtool -R" not in host.commands


def test_a_member_that_never_encrypted_anything_sends_a_new_encryption_key(host, monkeypatch):
    """HYPERLITE_ENCRYPTION_KEY only exists once a node encrypted a secret: a fresh member creates it, or the new node
    would get none."""
    from app.core import secrets_crypto

    env = host.tmp / ".env"
    env.write_text("HYPERLITE_SECRET_KEY=a-secret-key-of-this-node-0123456789\nOTHER=1\n")
    monkeypatch.setattr(secrets_crypto, "ENV_PATH", env)
    monkeypatch.delenv("HYPERLITE_ENCRYPTION_KEY")
    _create(host)
    _info, decoded = _information(host)
    answer = cluster_setup.accept_member(decoded["ticket"], "pve-b", "192.0.2.12", KEY_B)
    key = answer["keys"]["HYPERLITE_ENCRYPTION_KEY"]
    assert key and f"HYPERLITE_ENCRYPTION_KEY={key}" in env.read_text()


def test_an_answer_without_the_clusters_keys_changes_nothing(host, monkeypatch):
    answer = _answer()
    del answer["keys"]["HYPERLITE_ENCRYPTION_KEY"]
    monkeypatch.setattr(cluster_setup, "_call_member", lambda info, body: answer)
    with pytest.raises(cluster_setup.ClusterError, match=r"did not send the cluster's keys.*remove hv-test there"):
        cluster_setup.join(_blob(), "192.0.2.11", "prod", "alice")
    assert "a-secret-key-of-this-node" in (host.tmp / ".env").read_text()
    assert not cluster_setup.COROSYNC_KEY.exists() and not cluster_setup.CFS_DEFAULTS.exists()
    assert host.commands == []


def test_a_join_that_fails_puts_this_node_back_as_it_was(host, monkeypatch):
    """hyperlite-cfs never reaches the cluster: Corosync, hyperlite-cfs, their files, this node's cfs database and its
    authorized_keys are back as they were, and nothing tells Hyperlite it joined."""
    monkeypatch.setattr(cluster_setup, "_call_member", lambda info, body: _answer())
    monkeypatch.setattr(cluster_setup, "WAIT_S", 0)
    monkeypatch.setattr(cluster_setup, "JOIN_WAIT_S", 0)
    conf = cluster_setup.COROSYNC_CONF
    conf.parent.mkdir(parents=True)
    conf.write_text("# Debian's own\n")
    cfs_db = cluster_setup.CFS_DB
    cfs_db.parent.mkdir(parents=True)
    cfs_db.write_bytes(b"its own tree")
    keys = host.tmp / "authorized_keys"
    keys.write_text("ssh-ed25519 OWN admin\n")
    run = cluster_setup._run

    def services(args, timeout=60):
        if args[:2] == ["systemctl", "is-active"]:
            return args[-1] == "corosync"  # Debian starts Corosync when it is installed; hyperlite-cfs is off
        if args[:2] == ["systemctl", "is-enabled"]:
            return args[-1] == "corosync"
        if args == ["systemctl", "restart", shadow.SERVICE]:
            (cfs_db.parent / "config.db").write_bytes(b"the cluster's tree")
            return True  # the daemon stays in local mode: _wait_for_cluster gives up
        return run(args, timeout)

    monkeypatch.setattr(cluster_setup, "_run", services)
    with pytest.raises(cluster_setup.ClusterError, match=r"back as it was.*remove hv-test there"):
        cluster_setup.join(_blob(), "192.0.2.11", "prod", "alice")

    assert conf.read_text() == "# Debian's own\n" and not cluster_setup.COROSYNC_KEY.exists()
    assert not cluster_setup.CFS_DEFAULTS.exists()
    assert cfs_db.read_bytes() == b"its own tree" and not list(cfs_db.parent.glob("*.before-join-*"))
    assert keys.read_text() == "ssh-ed25519 OWN admin\n"
    assert "a-secret-key-of-this-node" in (host.tmp / ".env").read_text()
    tail = host.commands[host.commands.index("systemctl stop corosync") :]
    assert "systemctl disable hyperlite-cfs.service" in tail and "systemctl restart corosync" in tail
    assert "systemctl restart hyperlite-cfs.service" not in tail
    with get_conn() as db:
        settings = dict(db.execute("SELECT key, value FROM app_settings").fetchall())
    assert "cfs_join_pending" not in settings and settings.get("cfs_shadow") != "1"
    assert host.restarts == []


# ---- The primary node: it counts twice in the quorum ----


def _add_member(host, name="pve-b", nodeid=2, address="192.0.2.11"):
    conf = json.loads(host.files[cluster_setup.CONF_PATH])
    conf["members"].append({"name": name, "nodeid": nodeid, "address": address})
    host.files[cluster_setup.CONF_PATH] = json.dumps(conf).encode()


def test_a_primary_node_counts_twice_and_alone_keeps_the_quorum_with_one_other(host):
    _create(host)
    _add_member(host)  # a member from a configuration without votes: one each
    assert "quorum_votes: 1" in cluster_setup.render(cluster_setup.shared_conf())
    state = cluster_setup.set_primary("hv-test", "admin")
    assert {m["name"]: m["votes"] for m in state["members"]} == {"hv-test": 2, "pve-b": 1}
    conf = cluster_setup.COROSYNC_CONF.read_text()
    assert "name: hv-test\n    nodeid: 1\n    quorum_votes: 2" in conf  # 2 of 3: quorate alone
    assert "name: pve-b\n    nodeid: 2\n    quorum_votes: 1" in conf
    version = cluster_setup.shared_conf()["version"]
    state = cluster_setup.set_primary("pve-b", "admin")  # one primary at most: the other goes back to one vote
    assert {m["name"]: m["votes"] for m in state["members"]} == {"hv-test": 1, "pve-b": 2}
    assert cluster_setup.shared_conf()["version"] == version + 1  # every member writes corosync.conf again (sync)
    state = cluster_setup.set_primary(None, "admin")
    assert {m["votes"] for m in state["members"]} == {1}


def test_a_primary_must_be_a_member_and_a_configuration_with_two_is_refused(host):
    _create(host)
    with pytest.raises(cluster_setup.ClusterError, match="not a member"):
        cluster_setup.set_primary("pve-z", "admin")
    _add_member(host)
    conf = json.loads(host.files[cluster_setup.CONF_PATH])
    for m in conf["members"]:
        m["votes"] = 2
    with pytest.raises(cluster_setup.ClusterError):
        cluster_setup._check_conf(conf)  # never written from such a configuration
    conf["members"][0]["votes"] = 7
    with pytest.raises(cluster_setup.ClusterError):
        cluster_setup._check_conf(conf)


def test_setting_the_primary_is_for_administrators(client, auth_headers, host):
    _create(host)
    viewer = auth_headers("olga", "viewer")
    assert client.post("/cluster/principal", json={"name": "hv-test"}, headers=viewer).status_code == 403
    admin = auth_headers("root")
    r = client.post("/cluster/principal", json={"name": "hv-test"}, headers=admin)
    assert r.status_code == 200 and r.json()["members"][0]["votes"] == 2


def test_the_new_members_row_is_taken_by_the_cluster_before_the_quorum_is_lost(host, monkeypatch):
    """Adding the first member raises the votes needed to 2: once this node's Corosync reloads, the cluster takes no
    change until the new node is up. The nodes row was confirmed after that (with the answer), refused, and the join
    failed with the cluster left waiting for a node that gave up."""
    from app.repositories.cfs import confirm, shadow

    _create(host)
    _info, decoded = _information(host)
    order = []
    monkeypatch.setattr(confirm, "settle", lambda: order.append(("settle", len(host.commands))))
    cluster_setup.accept_member(decoded["ticket"], "pve-b", "192.0.2.12", KEY_B)
    [(_, commands_before)] = order
    assert "corosync-cfgtool -R" not in host.commands[:commands_before]  # confirmed while the quorum held

    def refused():
        raise shadow.NotConfirmed("The cluster refused the change: hyperlite-cfs has no quorum: it is read-only")

    monkeypatch.setattr(confirm, "settle", refused)
    _info, decoded = _information(host)
    with pytest.raises(cluster_setup.ClusterError, match="could not record pve-c"):
        cluster_setup.accept_member(decoded["ticket"], "pve-c", "192.0.2.13", KEY_B)
    assert all(m["name"] != "pve-c" for m in cluster_setup.shared_conf()["members"])  # not added, nothing to wait for
    with get_conn() as db:
        assert "pve-c" not in {r[0] for r in db.execute("SELECT name FROM nodes")}


def test_a_joining_node_calls_the_route_the_member_serves(client):
    """Since 1.0 the API is under /api/v1 and the old addresses answer 410: a join posted to /cluster/members was
    refused by every member, and no node could join a cluster."""
    body = {"ticket": "x", "name": "pve-b", "address": "192.0.2.2", "ssh_key": ""}
    # Absolute addresses: the test client otherwise puts every path under /api/v1 itself.
    r = client.post("http://testserver" + cluster_setup.MEMBERS_PATH, json=body)
    assert r.status_code == 403, r.text  # the route answers: the ticket is refused
    assert client.post("http://testserver/cluster/members", json=body).status_code == 410


def test_a_member_older_than_1_0_is_asked_at_its_old_address(monkeypatch):
    sent = []

    def post(info, path, body):
        sent.append(path)
        return (404, {"detail": "Not Found"}) if path.startswith("/api/v1") else (200, {"ok": True})

    monkeypatch.setattr(cluster_setup, "_post_member", post)
    assert cluster_setup._call_member({}, {}) == {"ok": True}
    assert sent == ["/api/v1/cluster/members", "/cluster/members"]


def test_the_new_members_fingerprint_and_keys_are_written_before_the_quorum_is_lost(host, monkeypatch):
    """Until the cluster has them, the members can neither check the new node's certificate (no file relayed to it)
    nor sign a call it accepts: taken for a node before 1.2, it was sent the shared key and refused it (401)."""
    from app.repositories.cfs import confirm, shadow

    _create(host)
    _info, decoded = _information(host)
    reloaded = []
    monkeypatch.setattr(
        cluster_setup,
        "sync",
        lambda: reloaded.append((host.files.get("/cluster/tls/pve-b"), host.files.get("/cluster/authkey/pve-b"))),
    )
    cluster_setup.accept_member(decoded["ticket"], "pve-b", "192.0.2.12", KEY_B, "CD" * 32, AUTH_KEYS)
    [(fingerprint, keys)] = reloaded  # both there when this node's Corosync reloads with the new member
    assert json.loads(fingerprint) == {"sha256": "cd" * 32} and json.loads(keys) == AUTH_KEYS

    for bad in (
        ("ab" * 31, None),
        (None, {"keys": [{"kid": "x", "public": "not base64", "created": 1}]}),
        (None, {"keys": []}),
    ):
        _info, decoded = _information(host)
        with pytest.raises(cluster_setup.ClusterError, match="Invalid"):
            cluster_setup.accept_member(decoded["ticket"], "pve-c", "192.0.2.13", KEY_B, *bad)

    def refused():
        raise shadow.NotConfirmed("The cluster refused the change: hyperlite-cfs has no quorum: it is read-only")

    monkeypatch.setattr(confirm, "settle", refused)
    _info, decoded = _information(host)
    with pytest.raises(cluster_setup.ClusterError, match="could not record pve-c"):
        cluster_setup.accept_member(decoded["ticket"], "pve-c", "192.0.2.13", KEY_B, "ef" * 32, AUTH_KEYS)
    assert "/cluster/tls/pve-c" not in host.files and "/cluster/authkey/pve-c" not in host.files


def test_a_node_before_1_3_joins_without_its_fingerprint_and_keys(host):
    _create(host)
    _info, decoded = _information(host)
    cluster_setup.accept_member(decoded["ticket"], "pve-b", "192.0.2.12", KEY_B)  # it publishes them itself, later
    assert "/cluster/tls/pve-b" not in host.files and "/cluster/authkey/pve-b" not in host.files


def test_a_joining_node_waits_longer_for_its_links_than_a_new_cluster(host, monkeypatch):
    """The links to the members come up once they reloaded with the new node: more than a minute on a loaded host
    (the lab on GitHub's runners gave up at 60 s, the links up a second later)."""
    waits = []
    monkeypatch.setattr(cluster_setup, "_call_member", lambda info, body: _answer())
    monkeypatch.setattr(cluster_setup, "_wait_for_cluster", lambda wait_s=None: waits.append(wait_s))
    monkeypatch.setattr(cluster_setup, "JOIN_WAIT_S", 300)
    cluster_setup.join(_blob(), "192.0.2.11", "prod", "alice")
    assert waits == [300] and cluster_setup.JOIN_WAIT_S > cluster_setup.WAIT_S


def test_each_member_says_which_configuration_its_file_holds(host):
    _create(host)
    cluster_setup.sync()
    assert host.files[f"{cluster_setup.WRITTEN_DIR}/hv-test"] == str(cluster_setup._local_version()).encode()


def test_a_new_member_is_reloaded_in_once_every_member_wrote_it(host, monkeypatch):
    """The lab's node-3 failure: the reload reaches every member at once, each from its own file, and a member that
    still held the older one left the new node out while this one took it into the ring."""
    _create(host)
    _, decoded = _information(host)
    cluster_setup.accept_member(decoded["ticket"], "pve-b", "192.0.2.12", KEY_B)
    _, decoded = _information(host)
    written = f"{cluster_setup.WRITTEN_DIR}/pve-b"
    seen = []

    def member_follows(seconds):  # pve-b's lead loop writes the new configuration while this node waits
        seen.append(list(host.commands))
        host.files[written] = b"3"

    monkeypatch.setattr(cluster_setup.time, "sleep", member_follows)
    host.commands.clear()
    cluster_setup.accept_member(decoded["ticket"], "pve-c", "192.0.2.13", KEY_B.replace("B", "C"))
    assert seen == [[]]  # waited once, before any reload
    assert host.commands == ["corosync-cfgtool -R"]


def test_a_member_that_does_not_write_it_is_waited_for_a_while_only(host, monkeypatch, caplog):
    _create(host)
    _, decoded = _information(host)
    cluster_setup.accept_member(decoded["ticket"], "pve-b", "192.0.2.12", KEY_B)
    _, decoded = _information(host)
    monkeypatch.setattr(cluster_setup, "WRITTEN_WAIT_S", 0)
    host.commands.clear()
    cluster_setup.accept_member(decoded["ticket"], "pve-c", "192.0.2.13", KEY_B.replace("B", "C"))
    assert host.commands == ["corosync-cfgtool -R"] and "pve-b did not write version 3" in caplog.text


def test_corosync_gets_the_token_timeout_the_shared_configuration_carries(host):
    conf = {"cluster": "prod", "version": 3, "members": [{"name": "hv-test", "nodeid": 1, "address": ADDRESS}]}
    assert "token:" not in cluster_setup.render(cluster_setup._check_conf(conf))  # Corosync's default
    text = cluster_setup.render(cluster_setup._check_conf({**conf, "token": 10000}))
    assert "  config_version: 3\n  token: 10000\n  transport: knet\n" in text
    for bad in (999, 60001, "ten"):
        with pytest.raises(cluster_setup.ClusterError):
            cluster_setup._check_conf({**conf, "token": bad})


def _leading(monkeypatch, leader=True):
    monkeypatch.setattr(cluster_lead, "is_leader", lambda: leader)


def test_each_member_says_what_it_writes_to_corosyncs_configuration(host):
    _create(host)
    cluster_setup.sync()
    assert json.loads(host.files[f"{cluster_setup.RENDERS_DIR}/hv-test"]) == ["token"]


def test_the_token_timeout_waits_for_every_member_to_write_it(host, monkeypatch):
    """Corosync wants the same timeout on every member: a member of an older version would write the configuration
    without it, so the cluster keeps the default until each one says it writes it."""
    _create(host)
    _add_member(host)
    _leading(monkeypatch)
    cluster_setup.sync()
    assert "token" not in cluster_setup.shared_conf()  # pve-b said nothing yet
    assert "token:" not in cluster_setup.COROSYNC_CONF.read_text()

    host.files[f"{cluster_setup.RENDERS_DIR}/pve-b"] = b'["token"]'  # pve-b updated
    version = cluster_setup.shared_conf()["version"]
    cluster_setup.sync()
    conf = cluster_setup.shared_conf()
    assert conf["token"] == cluster_setup.TOKEN_MS and conf["version"] == version + 1  # every member writes it again
    assert f"token: {cluster_setup.TOKEN_MS}" in cluster_setup.COROSYNC_CONF.read_text()
    host.commands.clear()
    cluster_setup.sync()
    assert cluster_setup.shared_conf()["version"] == version + 1 and host.commands == []  # settled: nothing more


def test_a_member_that_does_not_write_the_token_takes_the_cluster_back_to_the_default(host, monkeypatch):
    _create(host)
    _leading(monkeypatch)
    cluster_setup.sync()
    assert cluster_setup.shared_conf()["token"] == cluster_setup.TOKEN_MS
    _add_member(host, "pve-old", 3, "192.0.2.13")  # a node of an older version joined
    cluster_setup.sync()
    assert "token" not in cluster_setup.shared_conf()
    assert "token:" not in cluster_setup.COROSYNC_CONF.read_text()


def test_only_the_leader_settles_the_token_timeout(host, monkeypatch):
    _create(host)
    _leading(monkeypatch, leader=False)
    cluster_setup.sync()
    assert "token" not in cluster_setup.shared_conf()


def test_a_member_change_on_this_node_goes_first(host, monkeypatch):
    """set_primary() runs sync() while it holds the members' lock: the token waits for the next pass instead of
    waiting for that lock forever."""
    _create(host)
    _add_member(host)
    host.files[f"{cluster_setup.RENDERS_DIR}/pve-b"] = b'["token"]'
    _leading(monkeypatch)
    version = cluster_setup.shared_conf()["version"]
    cluster_setup.set_primary("hv-test", "admin")
    assert "token" not in cluster_setup.shared_conf() and cluster_setup.shared_conf()["version"] == version + 1
    cluster_setup.sync()
    assert cluster_setup.shared_conf()["token"] == cluster_setup.TOKEN_MS
