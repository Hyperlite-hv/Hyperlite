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
        if self.path.startswith("/fail-once") and sum(1 for s in Handler.seen if s[1] == self.path) == 1:
            self.close_connection = True  # the request is dropped: as a peer that closed a kept connection
            return
        out = json.dumps({"path": self.path, "method": self.command}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
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
    context.load_cert_chain(crt, key)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(crt.read_text())).hexdigest()
    monkeypatch.setattr("app.core.cluster_setup.PORT", httpd.server_address[1])
    published = {"pve-a": fingerprint}
    monkeypatch.setattr(peer_https, "fingerprint_of", lambda name: published.get(name))
    monkeypatch.setattr(peer_https, "_pools", {})
    Handler.seen = []
    yield {"node": {"name": "pve-a", "hostname": "127.0.0.1"}, "published": published}
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


def test_a_read_is_sent_again_once_but_a_change_never(server):
    assert peer_https.call(server["node"], "tok", "GET", "/fail-once/read")[0] == 200
    with pytest.raises(peer_https.Unavailable):
        peer_https.call(server["node"], "tok", "POST", "/fail-once/change", body={"x": 1})
    assert sum(1 for s in Handler.seen if s[1] == "/fail-once/change") == 1


def test_peer_api_uses_https_and_falls_back_to_ssh(server, monkeypatch):
    ssh = []

    def fake_ssh(node, args, stdin=None, timeout=60, connect_timeout=10):
        ssh.append(node["name"])
        return subprocess.CompletedProcess(args, 0, json.dumps({"status": 200, "body": "[]"}), "")

    monkeypatch.setattr(rolling_update, "_ssh", fake_ssh)
    status, body = rolling_update.peer_api(server["node"], "ana", "GET", "/vms")
    assert status == 200 and body["path"] == "/vms" and ssh == []
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
