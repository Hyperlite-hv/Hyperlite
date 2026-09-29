"""Pushing metrics to InfluxDB (HTTP line protocol) and Graphite (TCP plaintext), against real local listeners."""

import http.server
import socket
import threading
from typing import ClassVar

import pytest

from app.core import metric_export as me

ROWS = [
    ("host", "host", 12.5, 2048, 8192, None, None, 100, 200),
    ("vm", "web 1,a=b", 3.0, None, None, None, None, None, None),
]


def test_formats_escape_names_and_skip_missing_values():
    assert me.line_protocol(ROWS, 1700000000.9) == (
        "hyperlite,scope=host,target=host cpu_pct=12.5,mem_used_mb=2048.0,mem_total_mb=8192.0,net_rx_bps=100.0,net_tx_bps=200.0 1700000000\n"
        "hyperlite,scope=vm,target=web\\ 1\\,a\\=b cpu_pct=3.0 1700000000\n"
    )
    assert me.graphite_lines(ROWS[1:], 1700000000, "lab.hv") == "lab.hv.vm.web_1_a_b.cpu_pct 3.0 1700000000\n"
    assert me.line_protocol([("vm", "x", *([None] * 7))], 1) == ""


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"nom": "a", "type": "statsd"}, "type must be"),
        ({"nom": "", "type": "graphite", "hote": "g"}, "Invalid name"),
        ({"nom": "a", "type": "influxdb", "url": "file:///etc/passwd", "org": "o", "bucket": "b"}, "Unsupported URL"),
        ({"nom": "a", "type": "influxdb", "url": "http://i:8086?x=1", "org": "o", "bucket": "b"}, "without a query"),
        ({"nom": "a", "type": "influxdb", "url": "http://i:8086", "org": "o&x", "bucket": "b"}, "Invalid organization"),
        ({"nom": "a", "type": "graphite", "hote": "bad host"}, "Invalid host"),
        ({"nom": "a", "type": "graphite", "hote": "g", "port": 70000}, "port"),
        ({"nom": "a", "type": "graphite", "hote": "g", "prefixe": "a..b"}, "Invalid prefix"),
    ],
)
def test_bad_definitions_are_refused(payload, message):
    with pytest.raises(me.ExportError, match=message):
        me.validate(payload)


class _Influx(http.server.BaseHTTPRequestHandler):
    received: ClassVar[list] = []
    status = 204

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        _Influx.received.append((self.path, self.headers["Authorization"], body))
        self.send_response(_Influx.status)
        self.end_headers()
        if _Influx.status >= 400:
            self.wfile.write(b'{"message":"unauthorized access"}')

    def log_message(self, *args):
        pass


@pytest.fixture()
def influx():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Influx)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Influx.received, _Influx.status = [], 204
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_api_manages_servers_and_pushes_to_influxdb(client, auth_headers, influx):
    admin = auth_headers("root", "admin")
    body = {"nom": "influx", "type": "influxdb", "url": influx + "/", "org": "lab", "bucket": "hv", "jeton": "s3cret"}
    r = client.post("/metric-servers", json=body, headers=admin)
    assert r.status_code == 201, r.text
    server = r.json()
    assert server["url"] == influx and server["jeton_defini"] is True and "jeton" not in server
    assert client.post("/metric-servers", json=body, headers=admin).status_code == 422  # same name
    assert client.get("/metric-servers", headers=auth_headers("watcher", "observateur")).status_code == 403

    me.push(ROWS, 1700000000)
    path, auth, sent = _Influx.received[-1]
    assert path == "/api/v2/write?org=lab&bucket=hv&precision=s" and auth == "Token s3cret"
    assert sent.startswith("hyperlite,scope=host,target=host cpu_pct=12.5")
    assert client.get("/metric-servers", headers=admin).json()[0]["dernier_envoi"]

    _Influx.status = 401
    r = client.post(f"/metric-servers/{server['id']}/test", headers=admin)
    assert r.status_code == 502 and "InfluxDB answered 401" in r.json()["detail"]
    assert "unauthorized" in client.get("/metric-servers", headers=admin).json()[0]["derniere_erreur"]

    r = client.put(f"/metric-servers/{server['id']}", json=body | {"jeton": "", "bucket": "hv2"}, headers=admin)
    assert r.status_code == 200 and r.json()["bucket"] == "hv2"
    _Influx.status = 204
    me.push(ROWS, 1700000000)
    assert _Influx.received[-1][1] == "Token s3cret"  # an empty token on update keeps the stored one
    assert client.delete(f"/metric-servers/{server['id']}", headers=admin).status_code == 200
    assert client.get("/metric-servers", headers=admin).json() == []


def test_graphite_receives_plaintext_and_an_unreachable_server_is_recorded(database):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    got = []

    def accept():
        conn, _ = listener.accept()
        with conn:
            got.append(conn.recv(65536).decode())

    t = threading.Thread(target=accept, daemon=True)
    t.start()
    me.save_server({"nom": "graphite", "type": "graphite", "hote": "127.0.0.1", "port": port, "prefixe": "lab"})
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    dead_port = closed.getsockname()[1]
    closed.close()
    me.save_server({"nom": "dead", "type": "graphite", "hote": "127.0.0.1", "port": dead_port})
    me.push(ROWS[:1], 1700000000)
    t.join(3)
    listener.close()
    assert got[0].splitlines()[0] == "lab.host.host.cpu_pct 12.5 1700000000"
    servers = {s["nom"]: s for s in me.list_servers()}
    assert servers["graphite"]["derniere_erreur"] is None and servers["graphite"]["dernier_envoi"]
    assert servers["dead"]["derniere_erreur"] and servers["dead"]["dernier_envoi"] is None
