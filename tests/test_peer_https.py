"""Calls between members over HTTPS, the certificate pinned (app/core/peer_https.py, docs/scale-results.md fix 2):
a real local HTTPS server with a certificate made for the test."""

import hashlib
import http.server
import json
import ssl
import subprocess
import threading
from typing import ClassVar

import pytest

from app.core import peer_https, rolling_update


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive, as uvicorn
    seen: ClassVar[list] = []

    def _answer(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        Handler.seen.append((self.command, self.path, self.headers.get("Authorization"), body, self.client_address[1]))
        if "/fail-once" in self.path and sum(1 for s in Handler.seen if s[1] == self.path) == 1:
            self.close_connection = True  # the request is dropped: as a peer that closed a kept connection
            return
        out = json.dumps({"path": self.path, "method": self.command}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        if "/closing" in self.path:  # as uvicorn on an error or at its shutdown
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(out)

    do_GET = do_POST = _answer

    def log_message(self, *a):
        pass


@pytest.fixture()
def server(tmp_path, monkeypatch):
    key, crt = tmp_path / "k.pem", tmp_path / "c.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=peer", "-days", "1",
                    "-keyout", str(key), "-out", str(crt)], check=True, capture_output=True)  # fmt: skip
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(crt, key)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(crt.read_text())).hexdigest()
    monkeypatch.setattr("app.core.cluster_setup.PORT", httpd.server_address[1])
    published = {"pve-a": fingerprint}
    monkeypatch.setattr(peer_https, "fingerprint_of", lambda name: published.get(name))
    monkeypatch.setattr(peer_https, "_pools", {})
    Handler.seen = []
    yield {"node": {"name": "pve-a", "hostname": "127.0.0.1"}, "published": published, "port": httpd.server_address[1]}
    httpd.shutdown()


def test_a_call_goes_over_https_and_reads_reuse_the_connection(server):
    for _ in range(3):
        status, body = peer_https.call(server["node"], "tok", "GET", "/vms")
        assert status == 200 and body == {"path": "/vms", "method": "GET"}
    assert {s[2] for s in Handler.seen} == {"Bearer tok"}
    assert len({s[4] for s in Handler.seen}) == 1  # one connection for the three reads


def test_a_certificate_that_is_not_the_published_one_is_refused(server):
    server["published"]["pve-a"] = "0" * 64
    with pytest.raises(peer_https.Unavailable, match="does not match"):
        peer_https.call(server["node"], "tok", "GET", "/vms")
    assert Handler.seen == []  # nothing was sent


def test_no_fingerprint_published_means_ssh(server):
    with pytest.raises(peer_https.Unavailable):
        peer_https.call({"name": "pve-b", "hostname": "127.0.0.1"}, "tok", "GET", "/vms")


def test_a_read_is_sent_again_once_but_a_change_never(server, monkeypatch):
    assert peer_https.call(server["node"], "tok", "GET", "/fail-once/read")[0] == 200
    with pytest.raises(peer_https.Lost, match="may have been done"):
        peer_https.call(server["node"], "tok", "POST", "/fail-once/change", body={"x": 1})
    assert sum(1 for s in Handler.seen if s[1] == "/fail-once/change") == 1
    # Nor over SSH: the change may have been made, a second one would be another snapshot, another VM...
    ssh = []
    monkeypatch.setattr(rolling_update, "_ssh", lambda *a, **k: ssh.append(a))
    monkeypatch.setattr(rolling_update.peer_auth, "ticket_for", lambda node, user: "tok")
    with pytest.raises(rolling_update.PeerUnreachable, match="may have been done"):
        rolling_update.peer_api(server["node"], "ana", "POST", "/api/v1/fail-once/snapshot", body={})
    assert ssh == []


def test_a_file_is_sent_as_it_is_read_once_and_never_over_ssh(server):
    chunks = iter([b"--b\r\n", b"ISO" * 1000, b"\r\n--b--\r\n"])
    length = 5 + 3000 + 9
    status, body = peer_https.send(
        server["node"], "tok", "/api/v1/isos", "multipart/form-data; boundary=b", length, chunks, 30
    )
    assert status == 200 and body == {"path": "/api/v1/isos", "method": "POST"}
    method, _, auth, sent, _ = Handler.seen[-1]
    assert (method, auth, len(sent)) == ("POST", "Bearer tok", length) and b"ISO" * 1000 in sent
    with pytest.raises(peer_https.Lost):
        peer_https.send(
            server["node"], "tok", "/fail-once/upload", "multipart/form-data; boundary=b", 3, iter([b"abc"]), 30
        )
    server["published"].clear()
    with pytest.raises(peer_https.Unavailable):
        peer_https.send(server["node"], "tok", "/api/v1/isos", "multipart/form-data; boundary=b", 3, iter([b"abc"]), 30)


def test_peer_api_uses_https_and_falls_back_to_ssh(server, monkeypatch):
    ssh = []

    def fake_ssh(node, args, stdin=None, timeout=60, connect_timeout=10):
        ssh.append(node["name"])
        return subprocess.CompletedProcess(args, 0, json.dumps({"status": 200, "body": "[]"}), "")

    monkeypatch.setattr(rolling_update, "_ssh", fake_ssh)
    # hyperlite-daemon reads the member's address from the node registry, for the fallback.
    monkeypatch.setattr(rolling_update.cluster, "get_node", lambda name: dict(server["node"]))
    status, body = rolling_update.peer_api(server["node"], "ana", "GET", "/vms")
    assert status == 200 and body["path"] == "/api/v1/vms" and ssh == []  # the members' API prefix
    server["published"].clear()  # no fingerprint: SSH, as before
    assert rolling_update.peer_api(server["node"], "ana", "GET", "/vms") == (200, []) and ssh == ["pve-a"]


def test_the_fingerprint_is_published_only_when_it_changed(monkeypatch):
    from app.core.cfs_client import NotFound

    puts = []

    class Client:
        stored = None

        def get(self, path):
            if self.stored is None:
                raise NotFound(1, "no such entry")
            return type("E", (), {"data": self.stored})()

        def put(self, path, data):
            puts.append(path)
            self.stored = data

    client = Client()
    monkeypatch.setattr(peer_https, "_client", lambda: client)
    monkeypatch.setattr(peer_https, "own_fingerprint", lambda: "ab" * 32)
    peer_https.publish()
    peer_https.publish()
    assert puts == ["/cluster/tls/hv-test"]
    monkeypatch.setattr(peer_https, "own_fingerprint", lambda: "cd" * 32)  # a renewed certificate
    peer_https.publish()
    assert len(puts) == 2


def test_the_publishing_loop_survives_a_round_that_fails(monkeypatch):
    import sqlite3
    import time

    rounds = []

    def publish():
        rounds.append(1)
        if len(rounds) == 1:
            raise sqlite3.OperationalError("no such table: app_settings")

    # A failed round is tried again soon, not at the next round.
    monkeypatch.setattr(peer_https, "PUBLISH_S", 600)
    monkeypatch.setattr(peer_https, "RETRY_S", 0.01)
    monkeypatch.setattr(peer_https, "publish", publish)
    stop = peer_https.start()
    deadline = time.monotonic() + 5
    while len(rounds) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    assert len(rounds) >= 2


def test_a_fingerprint_the_cluster_refused_is_published_again_soon(monkeypatch):
    """A node that joined publishes as it restarts, while the cluster reloads without the quorum: hyperlite-cfs refuses
    the write. It is tried again within seconds, not at the next round, or no file could be relayed to that node."""
    import time

    from app.core.cfs_client import CfsError, NotFound

    rounds = []

    class Client:
        def get(self, path):
            raise NotFound(1, "no such entry")

        def put(self, path, data):
            rounds.append(path)
            if len(rounds) == 1:
                raise CfsError(503, "not quorate")

    monkeypatch.setattr(peer_https, "_client", lambda: Client())
    monkeypatch.setattr(peer_https, "own_fingerprint", lambda: "ab" * 32)
    monkeypatch.setattr(peer_https, "PUBLISH_S", 600)
    monkeypatch.setattr(peer_https, "RETRY_S", 0.01)
    stop = peer_https.start()
    deadline = time.monotonic() + 5
    while len(rounds) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    assert len(rounds) >= 2


def test_an_export_is_read_while_it_arrives_on_a_connection_of_its_own(server):
    answer = peer_https.stream(server["node"], "tok", "/api/v1/tasks/export.csv")
    assert answer.status == 200
    assert json.loads(answer.lines.read()) == {"path": "/api/v1/tasks/export.csv", "method": "GET"}
    answer.close()
    assert Handler.seen[-1][2] == "Bearer tok"
    assert peer_https._pools == {}  # never a kept connection: the caller closes it once read
    server["published"]["pve-a"] = "0" * 64
    with pytest.raises(peer_https.Unavailable, match="does not match"):
        peer_https.stream(server["node"], "tok", "/api/v1/tasks/export.csv")
    with pytest.raises(peer_https.Unavailable):
        peer_https.stream({"name": "pve-b", "hostname": "127.0.0.1"}, "tok", "/api/v1/tasks/export.csv")


def test_a_connection_the_peer_closed_is_never_reopened_unchecked(server):
    # http.client reopens a connection closed by Connection: close on its own, on the next request, and that connect
    # skipped the pinning: the call sent its peer ticket to whatever answered there (GHSA-mv7v-9gqp-797f).
    assert peer_https.call(server["node"], "tok", "GET", "/closing")[0] == 200
    assert all(pool.empty() for pool in peer_https._pools.values())  # not kept
    conn = peer_https._connect("127.0.0.1", server["port"], server["published"]["pve-a"], 5)
    conn.close()  # as the peer closing it
    conn.fingerprint = "0" * 64  # what answers there now is not the member
    with pytest.raises(peer_https.Unavailable, match="does not match"):
        conn.request("GET", "/vms", headers={"Authorization": "Bearer tok"})
    assert [s[1] for s in Handler.seen] == ["/closing"]  # nothing sent to it
