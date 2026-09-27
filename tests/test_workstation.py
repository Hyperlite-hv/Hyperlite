"""Workstation client support: device sign-in, expiring tokens, tunnel tickets and
the WebSocket relay (against a real local TCP echo server; no libvirt)."""

import socket
import threading
from datetime import UTC, datetime, timedelta

import pytest

from app.core import api_tokens, workstation


@pytest.fixture(autouse=True)
def _clean_state():
    workstation._requests.clear()
    workstation._tunnel_tickets.clear()
    workstation._open_tunnels.clear()
    yield
    workstation._requests.clear()
    workstation._tunnel_tickets.clear()
    workstation._open_tunnels.clear()


def test_tunnel_ports_setting(monkeypatch):
    monkeypatch.delenv("HYPERLITE_TUNNEL_PORTS", raising=False)
    assert workstation.tunnel_ports() == [22, 3389]
    monkeypatch.setenv("HYPERLITE_TUNNEL_PORTS", "22, 5900,abc,70000,22")
    assert workstation.tunnel_ports() == [22, 5900]
    monkeypatch.setenv("HYPERLITE_TUNNEL_PORTS", "")
    assert workstation.tunnel_ports() == []
    monkeypatch.setenv("HYPERLITE_CLI_TOKEN_DAYS", "9999")
    assert workstation.cli_token_days() == 365


def test_device_sign_in_needs_a_web_session_and_hands_out_one_expiring_token(client, auth_headers):
    headers = auth_headers("alice")
    start = client.post("/auth/cli/start", json={"hostname": "PC-ALICE", "client_version": "1.0.0"}).json()
    assert start["verification_uri_complete"].endswith(f"/cli-login?code={start['user_code']}")
    pending = client.post("/auth/cli/token", json={"device_code": start["device_code"]})
    assert pending.status_code == 400 and pending.json()["error"] == "authorization_pending"

    info = client.get(f"/auth/cli/requests/{start['user_code']}", headers=headers).json()
    assert info["hostname"] == "PC-ALICE" and info["token_days"] == 30

    # an API token cannot approve a workstation
    _, api_token = api_tokens.create_token("alice", "script")
    refused = client.post(
        f"/auth/cli/requests/{start['user_code']}/approve", headers={"Authorization": f"Bearer {api_token}"}
    )
    assert refused.status_code == 403

    # the code is accepted without its dash and in lower case
    code = start["user_code"].replace("-", "").lower()
    assert client.post(f"/auth/cli/requests/{code}/approve", headers=headers).json()["ok"] is True
    granted = client.post("/auth/cli/token", json={"device_code": start["device_code"]}).json()
    assert granted["access_token"].startswith("hlt_") and granted["username"] == "alice"
    expires = datetime.fromisoformat(granted["expires_at"])
    assert timedelta(days=29) < expires - datetime.now(UTC) <= timedelta(days=30)
    # single use
    again = client.post("/auth/cli/token", json={"device_code": start["device_code"]})
    assert again.json()["error"] == "expired_token"
    # the token works and is listed as a workstation token
    me = client.get("/workstation/config", headers={"Authorization": f"Bearer {granted['access_token']}"})
    assert me.status_code == 200 and me.json()["tunnel_ports"] == [22, 3389]
    tokens = client.get("/auth/tokens", headers=headers).json()
    assert [t["kind"] for t in tokens if t["name"] == "Workstation: PC-ALICE"] == ["cli"]


def test_denied_and_invalid_requests(client, auth_headers):
    headers = auth_headers("alice")
    assert client.post("/auth/cli/start", json={"hostname": "bad host;rm"}).status_code == 422
    start = client.post("/auth/cli/start", json={"hostname": "pc"}).json()
    assert client.post(f"/auth/cli/requests/{start['user_code']}/deny", headers=headers).json()["ok"] is True
    denied = client.post("/auth/cli/token", json={"device_code": start["device_code"]})
    assert denied.json()["error"] == "access_denied"
    assert client.post(f"/auth/cli/requests/{start['user_code']}/approve", headers=headers).status_code == 404
    assert client.post("/auth/cli/token", json={"device_code": "nope"}).json()["error"] == "expired_token"


def test_pending_requests_are_limited_per_address(client):
    for _ in range(workstation._MAX_PENDING_PER_IP):
        assert client.post("/auth/cli/start", json={"hostname": "pc"}).status_code == 200
    assert client.post("/auth/cli/start", json={"hostname": "pc"}).status_code == 429


def test_expired_token_is_refused(database, make_user):
    make_user("alice")
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    _, token = api_tokens.create_token("alice", "old", expires_at=past, kind="cli")
    assert api_tokens.verify_token(token) is None
    _, valid = api_tokens.create_token(
        "alice", "new", expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(), kind="cli"
    )
    assert api_tokens.verify_token(valid)["username"] == "alice"


class _Domain:
    def __init__(self, active=True):
        self._active = active

    def isActive(self):
        return self._active

    def interfaceAddresses(self, source):
        return {"vnet0": {"addrs": [{"type": 0, "addr": "192.168.122.50"}]}}


class _Conn:
    def __init__(self, domain):
        self.domain = domain

    def lookupByName(self, name):
        return self.domain

    def close(self):
        pass


def test_tunnel_ticket_checks_port_privilege_and_state(client, auth_headers, monkeypatch):
    from app.routers.vms import tunnel

    admin = auth_headers("alice")
    observer = auth_headers("bob", role="observateur")
    monkeypatch.setattr(tunnel, "open_conn", lambda: _Conn(_Domain()))
    assert client.post("/vms/vm1/tunnel-ticket", json={"port": 22}, headers=observer).status_code == 403
    assert client.post("/vms/vm1/tunnel-ticket", json={"port": 25}, headers=admin).status_code == 403
    ok = client.post("/vms/vm1/tunnel-ticket", json={"port": 22}, headers=admin).json()
    entry = workstation.take_tunnel_ticket(ok["ticket"], "vm1")
    assert entry["ip"] == "192.168.122.50" and entry["port"] == 22
    assert workstation.take_tunnel_ticket(ok["ticket"], "vm1") is None  # single use

    monkeypatch.setattr(tunnel, "open_conn", lambda: _Conn(_Domain(active=False)))
    assert client.post("/vms/vm1/tunnel-ticket", json={"port": 22}, headers=admin).status_code == 409
    monkeypatch.setenv("HYPERLITE_TUNNEL_PORTS", "")
    refused = client.post("/vms/vm1/tunnel-ticket", json={"port": 22}, headers=admin)
    assert refused.status_code == 403 and "disabled" in refused.json()["detail"]


def test_scoped_operator_role_grants_the_tunnel():
    from app.core.permissions import ROLES

    assert "vm.tunnel" in ROLES["operateur"]["privileges"] and "vm.tunnel" in ROLES["gestionnaire"]["privileges"]
    assert "vm.tunnel" not in ROLES["lecteur"]["privileges"]


def _echo_server():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        with conn:
            while data := conn.recv(65536):
                conn.sendall(data)
        srv.close()

    threading.Thread(target=serve, daemon=True).start()
    return srv.getsockname()[1]


def test_tunnel_relays_bytes_both_ways(client, make_user):
    make_user("alice")
    port = _echo_server()
    ticket = workstation.issue_tunnel_ticket("vm1", "127.0.0.1", port, "alice")
    with client.websocket_connect(f"/vms/vm1/tunnel?ticket={ticket}") as ws:
        ws.send_bytes(b"SSH-2.0-test\r\n")
        assert ws.receive_bytes() == b"SSH-2.0-test\r\n"
        assert workstation.open_tunnel_count("alice") == 1
    assert workstation.open_tunnel_count("alice") == 0


def test_tunnel_refuses_a_bad_ticket_with_a_reason(client):
    from starlette.websockets import WebSocketDisconnect

    with client.websocket_connect("/vms/vm1/tunnel?ticket=nope") as ws, pytest.raises(WebSocketDisconnect) as closed:
        ws.receive_bytes()
    assert closed.value.code == 4401


def test_client_download_is_public_and_listed(client, auth_headers, tmp_path, monkeypatch):
    from app.routers import workstation as router

    monkeypatch.setattr(router, "CLI_DIST", tmp_path)
    (tmp_path / "windows-amd64").mkdir()
    (tmp_path / "windows-amd64" / "hyperlite.exe").write_bytes(b"MZ binary")
    got = client.get("/downloads/hyperlite/windows-amd64")
    assert got.status_code == 200 and got.content == b"MZ binary"
    assert client.get("/downloads/hyperlite/linux-amd64").status_code == 404
    assert client.get("/downloads/hyperlite/windows-amd64..").status_code == 404  # only listed platforms
    listed = client.get("/workstation/config", headers=auth_headers("alice")).json()["downloads"]
    assert [d["platform"] for d in listed] == ["windows-amd64"] and len(listed[0]["sha256"]) == 64


class _Net:
    def __init__(self, mode):
        self.mode = mode

    def XMLDesc(self, flags):
        forward = f"<forward mode='{self.mode}'/>" if self.mode else ""
        return f"<network><name>n</name>{forward}</network>"


class _AccessDomain(_Domain):
    def __init__(self, iface_xml):
        super().__init__()
        self.iface_xml = iface_xml

    def XMLDesc(self, flags):
        return f"<domain><devices>{self.iface_xml}</devices></domain>"


class _AccessConn(_Conn):
    def __init__(self, domain, mode):
        super().__init__(domain)
        self.mode = mode

    def networkLookupByName(self, name):
        return _Net(self.mode)


@pytest.mark.parametrize(
    ("iface", "mode", "direct"),
    [
        ("<interface type='network'><source network='lan'/></interface>", "bridge", True),
        ("<interface type='network'><source network='default'/></interface>", "nat", False),
        ("<interface type='network'><source network='lab'/></interface>", None, False),
        ("<interface type='bridge'><source bridge='br0'/></interface>", None, True),
    ],
)
def test_access_says_whether_the_vm_is_directly_reachable(client, auth_headers, monkeypatch, iface, mode, direct):
    from app.routers.vms import tunnel

    headers = auth_headers("alice")
    monkeypatch.setattr(tunnel, "open_conn", lambda: _AccessConn(_AccessDomain(iface), mode))
    monkeypatch.setattr(tunnel, "get_vm_ssh_user", lambda name: "debian")
    info = client.get("/vms/vm1/access", headers=headers).json()
    assert info == {"ip": "192.168.122.50", "direct": direct, "ssh_user": "debian", "tunnel_ports": [22, 3389]}
