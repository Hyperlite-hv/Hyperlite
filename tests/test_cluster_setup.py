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
    monkeypatch.setattr(cluster_setup, "_run", run)
    monkeypatch.setattr(cluster_setup, "_local_addresses", lambda: {ADDRESS, "192.0.2.11"})
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
        "membres": [
            {"nom": "pve-b", "nodeid": 2, "adresse": "192.0.2.12"},
            {"nom": "hv-test", "nodeid": 1, "adresse": ADDRESS},
        ],
    }
    text = cluster_setup.render(cluster_setup._check_conf(conf))
    assert "cluster_name: prod" in text and "config_version: 3" in text
    assert "crypto_cipher: aes256" in text and "crypto_hash: sha256" in text and "transport: knet" in text
    assert text.index("name: hv-test") < text.index("name: pve-b")  # by node id
    for bad in (
        {**conf, "cluster": "bad name"},
        {**conf, "membres": [{"nom": "x;rm", "nodeid": 1, "adresse": ADDRESS}]},
        {**conf, "membres": [{"nom": "pve-c", "nodeid": 1, "adresse": "not-an-ip\n}"}]},
        {**conf, "membres": conf["membres"] + [{"nom": "pve-c", "nodeid": 2, "adresse": "192.0.2.13"}]},
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


def test_creating_a_cluster(host):
    state = _create(host)
    assert state["en_cluster"] and state["nom"] == "prod" and state["quorum"]
    assert state["membres"] == [{"nom": "hv-test", "nodeid": 1, "adresse": ADDRESS}]

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
    assert decoded["cluster"] == "prod" and decoded["adresse"] == ADDRESS and decoded["port"] == 8000
    assert decoded["empreinte"] == cluster_setup._certificate_fingerprint()

    with pytest.raises(PermissionError):
        cluster_setup.accept_member("x" * 43, "pve-b", "192.0.2.12", KEY_B)  # a wrong ticket also spends nothing
    answer = cluster_setup.accept_member(decoded["ticket"], "pve-b", "192.0.2.12", KEY_B)
    assert answer["configuration"]["version"] == 2
    assert answer["configuration"]["membres"][1] == {"nom": "pve-b", "nodeid": 2, "adresse": "192.0.2.12"}
    assert set(answer["cles"]) == {"HYPERLITE_SECRET_KEY", "HYPERLITE_ENCRYPTION_KEY"}
    assert answer["cles_ssh"] == {"hv-test": KEY_A, "pve-b": KEY_B}
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
            "membres": [
                {"nom": "pve-a", "nodeid": 1, "adresse": "192.0.2.20"},
                {"nom": me, "nodeid": 2, "adresse": address},
            ],
        },
        "cle_corosync": base64.b64encode(b"k" * 256).decode(),
        "cles": {
            "HYPERLITE_SECRET_KEY": "the-clusters-secret-key-0123456789",
            "HYPERLITE_ENCRYPTION_KEY": "the-clusters-fernet-key-0123456789ab=",
        },
        "cles_ssh": {"pve-a": KEY_B, me: KEY_A},
    }


def _blob(**over):
    info = {
        "cluster": "prod",
        "adresse": "192.0.2.20",
        "port": 8000,
        "empreinte": "ab" * 32,
        "ticket": "t" * 43,
        **over,
    }
    return base64.urlsafe_b64encode(json.dumps(info).encode()).decode()


def test_joining_replaces_this_nodes_configuration_by_the_clusters(host, monkeypatch):
    sent = []
    monkeypatch.setattr(cluster_setup, "_call_member", lambda info, body: sent.append((info, body)) or _answer())
    cfs_db = cluster_setup.CFS_DB
    cfs_db.parent.mkdir(parents=True)
    cfs_db.write_bytes(b"its own tree")
    with get_conn() as db:
        db.execute("INSERT INTO groups (name) VALUES ('local-only')")
        db.commit()

    with pytest.raises(cluster_setup.ClusterError, match="Type the cluster's name"):
        cluster_setup.join(_blob(), "192.0.2.11", "wrong", "alice")
    with pytest.raises(cluster_setup.ClusterError, match="not valid"):
        cluster_setup.join(_blob(empreinte="zz"), "192.0.2.11", "prod", "alice")
    assert sent == []

    done = cluster_setup.join(_blob(), "192.0.2.11", "prod", "alice")
    assert done == {"cluster": "prod", "redemarrage": True} and host.restarts == [1]
    info, body = sent[0]
    assert info["adresse"] == "192.0.2.20" and body == {
        "ticket": "t" * 43,
        "nom": "hv-test",
        "adresse": "192.0.2.11",
        "cle_ssh": KEY_A,
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
        settings = dict(db.execute("SELECT cle, valeur FROM app_settings").fetchall())
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
    conf["membres"].append({"nom": "pve-c", "nodeid": 3, "adresse": "192.0.2.13"})
    conf["version"] = 5
    host.files[cluster_setup.CONF_PATH] = json.dumps(conf).encode()
    host.files["/cluster/ssh/pve-c"] = KEY_B.encode()
    (host.tmp / "authorized_keys").write_text("ssh-ed25519 KEEP admin\nssh-ed25519 OLD hyperlite-cluster-member\n")
    assert cluster_setup.sync()
    assert "config_version: 5" in cluster_setup.COROSYNC_CONF.read_text()
    keys = (host.tmp / "authorized_keys").read_text()
    assert "KEEP admin" in keys and "OLD" not in keys and ("B" * 68) in keys
    assert not cluster_setup.sync()  # nothing newer


def test_removing_a_member(host):
    _create(host)
    _info, decoded = _information(host)
    cluster_setup.accept_member(decoded["ticket"], "pve-b", "192.0.2.12", KEY_B)
    with pytest.raises(cluster_setup.ClusterError, match="cannot remove itself"):
        cluster_setup.remove_member("hv-test", "hv-test", "alice")
    with pytest.raises(cluster_setup.ClusterError, match="to confirm"):
        cluster_setup.remove_member("pve-b", "nope", "alice")
    state = cluster_setup.remove_member("pve-b", "pve-b", "alice")
    assert [m["nom"] for m in state["membres"]] == ["hv-test"]
    assert "pve-b" not in cluster_setup.COROSYNC_CONF.read_text() and "/cluster/ssh/pve-b" not in host.files
    with get_conn() as db:
        assert {r[0] for r in db.execute("SELECT name FROM nodes")} == {"hv-test"}


class _Member(http.server.BaseHTTPRequestHandler):
    received: ClassVar[list] = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Member.received.append(json.loads(body))
        data = json.dumps({"ok": True}).encode()
        self.send_response(200)
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
        info = {"adresse": "127.0.0.1", "port": server.server_address[1], "empreinte": "00" * 32}
        _Member.received.clear()
        with pytest.raises(cluster_setup.ClusterError, match="does not match"):
            cluster_setup._call_member(info, {"ticket": "secret"})
        assert _Member.received == []  # nothing was sent to a certificate that does not match
        info["empreinte"] = cluster_setup.hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()
        assert cluster_setup._call_member(info, {"ticket": "secret"}) == {"ok": True}
        assert _Member.received == [{"ticket": "secret"}]
    finally:
        server.shutdown()


def test_the_api(host, client, auth_headers):
    admin, viewer = auth_headers("alice"), auth_headers("olga", role="observateur")
    assert client.get("/cluster", headers=viewer).status_code == 403
    assert client.get("/cluster", headers=admin).json()["en_cluster"] is False
    r = client.post("/cluster/creer", json={"nom": "prod", "adresse": "192.0.2.99"}, headers=admin)
    assert r.status_code == 409 and "not an address of this node" in r.json()["detail"]
    r = client.post("/cluster/creer", json={"nom": "prod", "adresse": ADDRESS}, headers=admin)
    assert r.status_code == 200 and r.json()["nom"] == "prod"
    assert client.post("/cluster/adhesion", headers=viewer).status_code == 403
    info = client.post("/cluster/adhesion", headers=admin).json()
    ticket = json.loads(base64.urlsafe_b64decode(info["information"] + "=="))["ticket"]
    # The joining node has no session here: the ticket is what lets it in.
    bad = {"ticket": "x" * 43, "nom": "pve-b", "adresse": "192.0.2.12", "cle_ssh": KEY_B}
    assert client.post("/cluster/membres", json=bad).status_code == 403
    r = client.post("/cluster/membres", json={**bad, "ticket": ticket})
    assert r.status_code == 200 and r.json()["configuration"]["version"] == 2


def test_a_join_whose_services_do_not_start_keeps_this_nodes_keys(host, monkeypatch):
    monkeypatch.setattr(cluster_setup, "_call_member", lambda info, body: _answer())
    monkeypatch.setattr(cluster_setup, "_run", lambda args, timeout=60: args[:2] != ["systemctl", "restart"])
    with pytest.raises(cluster_setup.ClusterError, match="failed"):
        cluster_setup.join(_blob(), "192.0.2.11", "prod", "alice")
    assert "a-secret-key-of-this-node" in (host.tmp / ".env").read_text()  # its database stays readable
    with get_conn() as db:
        assert db.execute("SELECT valeur FROM app_settings WHERE cle = 'cfs_join_pending'").fetchone() is None
    assert host.restarts == []
