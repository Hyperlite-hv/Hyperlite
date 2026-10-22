"""SPICE consoles (docs/design/spice-console.md): a SPICE display added with an expired password, a .vv file whose
password lives a few seconds, the proxy that admits only a ticket's own connections, and the node's setup script. The
real path (virt-viewer through the proxy, TLS checked against the node's CA, QEMU refusing an expired password) is in
the design document."""

import asyncio
import os
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import libvirt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.core import spice, spice_proxy
from app.routers.vms import console

REPO = Path(__file__).resolve().parent.parent
VNC_ONLY = (
    "<domain><name>web</name><devices><disk/>"
    "<graphics type='vnc' port='-1' autoport='yes' listen='127.0.0.1'/></devices></domain>"
)
WITH_SPICE_LIVE = (
    "<domain><name>web</name><devices><graphics type='vnc' port='5900'/>"
    "<graphics type='spice' tlsPort='5901' autoport='yes' defaultMode='secure' passwd='old' "
    "passwdValidTo='1970-01-01T00:00:00'><listen type='address' address='127.0.0.1'/></graphics></devices></domain>"
)


class Domain:
    def __init__(self, live, inactive=None, active=True):
        self.live, self.inactive, self.active = live, inactive or live, active
        self.flags_read, self.updates = [], []

    def XMLDesc(self, flags=0):
        self.flags_read.append(flags)
        return self.inactive if flags & libvirt.VIR_DOMAIN_XML_INACTIVE else self.live

    def isActive(self):
        return self.active

    def updateDeviceFlags(self, xml, flags):
        self.updates.append((xml, flags))


class Conn:
    def __init__(self, domain):
        self.domain, self.defined = domain, []

    def lookupByName(self, name):
        if self.domain is None or name != "web":
            raise libvirt.libvirtError("no domain")
        return self.domain

    def defineXML(self, xml):
        self.defined.append(xml)
        self.domain.inactive = xml

    def close(self):
        pass


def _cert(subject, issuer_key=None, issuer=None):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Hyperlite"), x509.NameAttribute(NameOID.COMMON_NAME, subject)]
    )
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(issuer or name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now.replace(year=now.year + 1))
        .sign(issuer_key or key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


@pytest.fixture()
def node_tls(tmp_path, monkeypatch):
    """This host set up as scripts/ensure-spice-tls.sh leaves it: certificates and spice_tls in qemu.conf."""
    tls = tmp_path / "pki"
    tls.mkdir()
    (tls / "ca-cert.pem").write_text(_cert("Hyperlite SPICE CA node1"))
    (tls / "server-cert.pem").write_text(_cert("node1"))
    conf = tmp_path / "qemu.conf"
    conf.write_text('#spice_tls = 1\nspice_tls = 1\nspice_tls_x509_cert_dir = "/x"\n')
    monkeypatch.setattr(spice, "TLS_DIR", tls)
    monkeypatch.setattr(spice, "QEMU_CONF", conf)
    return {"dir": tls, "conf": conf}


@pytest.fixture()
def vm(monkeypatch):
    state = {"conn": Conn(None), "nodes": []}

    def fake_open(node=None):
        state["nodes"].append(node)
        return state["conn"]

    monkeypatch.setattr(console, "open_conn", fake_open)

    def set_vm(domain):
        state["conn"] = Conn(domain)
        return state["conn"]

    state["set"] = set_vm
    return state


@pytest.fixture(autouse=True)
def _no_tickets_left():
    spice_proxy.TICKETS.clear()
    yield
    spice_proxy.TICKETS.clear()


# ---- The display and its password ----


def test_a_spice_display_is_added_tls_only_on_loopback_with_an_expired_password():
    domain = Domain(VNC_ONLY)
    conn = Conn(domain)
    spice.add_display(conn, domain)
    assert domain.flags_read[-1] & libvirt.VIR_DOMAIN_XML_SECURE, "the other displays' passwords are kept"
    root = ET.fromstring(conn.defined[0])
    graphics = root.find("devices/graphics[@type='spice']")
    assert graphics.get("defaultMode") == "secure" and graphics.get("autoport") == "yes"
    assert graphics.find("listen").attrib == {"type": "address", "address": "127.0.0.1"}
    assert len(graphics.get("passwd")) >= 24, "never a display without a password: it would admit anyone"
    assert graphics.get("passwdValidTo") == "1970-01-01T00:00:00"
    assert root.find("devices/graphics[@type='vnc']") is not None, "noVNC keeps its display"


def test_a_ticket_password_lives_a_few_seconds_and_keeps_open_sessions():
    domain = Domain(WITH_SPICE_LIVE)
    before = time.time()
    password = spice.set_ticket_password(domain, ttl_s=30)
    xml, flags = domain.updates[0]
    assert flags == libvirt.VIR_DOMAIN_AFFECT_LIVE, "the stored configuration keeps its expired password"
    graphics = ET.fromstring(xml)
    assert graphics.get("passwd") == password != "old" and graphics.get("connected") == "keep"
    valid_to = datetime.strptime(graphics.get("passwdValidTo"), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC).timestamp()
    assert before + 28 <= valid_to <= time.time() + 31
    assert spice.tls_port(WITH_SPICE_LIVE) == 5901 and spice.tls_port(VNC_ONLY) is None


def test_the_node_must_have_spice_in_tls(node_tls):
    ca, subject = spice.node_tls()
    assert ca.startswith("-----BEGIN CERTIFICATE-----")
    assert subject == "O=Hyperlite,CN=node1", "the certificate's order: spice-gtk compares it as written"
    node_tls["conf"].write_text("#spice_tls = 1\n")
    with pytest.raises(spice.SpiceUnavailable, match="not enabled"):
        spice.node_tls()
    node_tls["conf"].write_text("spice_tls = 1\n")
    (node_tls["dir"] / "server-cert.pem").unlink()
    with pytest.raises(spice.SpiceUnavailable, match="not set up"):
        spice.node_tls()


@pytest.mark.parametrize(
    ("header", "host"),
    [
        ("10.0.0.5:8000", "10.0.0.5"),
        ("hv1.example.org:8000", "hv1.example.org"),
        ("hv1", "hv1"),
        ("[fd00::5]:8000", "[fd00::5]"),
        ("", None),
        ("evil.example\r\nproxy=http://x", None),
        ("a b", None),
    ],
)
def test_the_proxy_is_reached_where_the_browser_reached_hyperlite(header, host):
    assert spice.proxy_host(header) == host


def test_the_vv_file_goes_through_the_proxy_in_tls():
    text = spice.vv_file(
        title="web", ticket="ab12", port=5901, password="pw", proxy="10.0.0.5", ca_pem="L1\nL2\n", subject="O=H,CN=n"
    )
    lines = dict(line.split("=", 1) for line in text.splitlines()[1:])
    assert text.startswith("[virt-viewer]\n")
    assert lines["type"] == "spice" and lines["host"] == "ab12" and lines["tls-port"] == "5901"
    assert lines["proxy"] == f"http://10.0.0.5:{spice.PROXY_PORT}" and lines["password"] == "pw"
    assert lines["ca"] == "L1\\nL2" and lines["host-subject"] == "O=H,CN=n" and lines["delete-this-file"] == "1"
    assert "port" not in lines, "no plain SPICE port: TLS only"


# ---- The API ----


def test_a_running_vm_gets_a_vv_file_and_a_proxy_ticket(client, auth_headers, vm, node_tls):
    domain = Domain(WITH_SPICE_LIVE)
    vm["set"](domain)
    r = client.post("/vms/web/spice-ticket", headers=auth_headers("root"))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/x-virt-viewer")
    assert (
        r.headers["content-disposition"] == 'attachment; filename="web.vv"' and r.headers["cache-control"] == "no-store"
    )
    lines = dict(line.split("=", 1) for line in r.text.splitlines()[1:])
    assert lines["proxy"] == f"http://testserver:{spice.PROXY_PORT}"
    assert lines["password"] == ET.fromstring(domain.updates[0][0]).get("passwd")
    assert spice_proxy.TICKETS[lines["host"]][:3] == ("web", None, 5901)
    assert spice_proxy.admit(f"CONNECT {lines['host']}:5901 HTTP/1.0") == (None, 5901)


def test_no_ticket_for_a_stopped_vm_one_without_spice_or_an_unready_node(client, auth_headers, vm, node_tls):
    admin = auth_headers("root")
    assert client.post("/vms/web/spice-ticket", headers=admin).status_code == 404
    vm["set"](Domain(WITH_SPICE_LIVE, active=False))
    assert "must be started" in client.post("/vms/web/spice-ticket", headers=admin).json()["detail"]
    vm["set"](Domain(VNC_ONLY))
    assert "add one" in client.post("/vms/web/spice-ticket", headers=admin).json()["detail"]
    vm["set"](Domain(VNC_ONLY, inactive=WITH_SPICE_LIVE))
    assert "next start" in client.post("/vms/web/spice-ticket", headers=admin).json()["detail"]
    domain = Domain(WITH_SPICE_LIVE)
    vm["set"](domain)
    node_tls["conf"].write_text("")
    r = client.post("/vms/web/spice-ticket", headers=admin)
    assert r.status_code == 409 and "not enabled" in r.json()["detail"]
    assert domain.updates == [] and spice_proxy.TICKETS == {}, "no password set, no ticket"


def test_the_ticket_takes_the_console_privilege(client, auth_headers, vm, node_tls):
    vm["set"](Domain(WITH_SPICE_LIVE))
    assert client.post("/vms/web/spice-ticket").status_code == 401
    assert client.post("/vms/web/spice-ticket", headers=auth_headers("eve", role="viewer")).status_code == 200


def test_a_display_is_added_where_the_node_is_ready_by_the_hardware_privilege(client, auth_headers, vm, node_tls):
    admin = auth_headers("root")
    conn = vm["set"](Domain(VNC_ONLY, active=False))
    assert client.post("/vms/web/spice-display", headers=auth_headers("eve", role="viewer")).status_code == 403
    r = client.post("/vms/web/spice-display", headers=admin)
    assert r.status_code == 200 and r.json() == {"spice": True, "effective": "now"}
    assert spice.spice_graphics(conn.defined[0]) is not None
    again = client.post("/vms/web/spice-display", headers=admin)
    assert again.status_code == 409 and len(conn.defined) == 1

    conn = vm["set"](Domain(VNC_ONLY, active=True))
    node_tls["conf"].write_text("")
    r = client.post("/vms/web/spice-display", headers=admin)
    assert r.status_code == 409 and "not enabled" in r.json()["detail"] and conn.defined == []
    node_tls["conf"].write_text("spice_tls = 1\n")
    assert client.post("/vms/web/spice-display", headers=admin).json()["effective"] == "next_start"


# ---- The proxy ----


async def _upstream():
    """A stand-in for QEMU's TLS port: echoes what it receives, prefixed."""

    async def echo(reader, writer):
        while data := await reader.read(1024):
            writer.write(b"qemu:" + data)
            await writer.drain()
        writer.close()

    return await asyncio.start_server(echo, "127.0.0.1", 0)


async def _connect(port, line):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(line.encode() + b"\r\nHost: x\r\n\r\n")
    await writer.drain()
    status = await asyncio.wait_for(reader.readline(), 5)
    return reader, writer, status.decode()


def test_the_proxy_admits_a_tickets_connections_and_nothing_else():
    async def scenario():
        upstream = await _upstream()
        qemu_port = upstream.sockets[0].getsockname()[1]
        server = await spice_proxy.start("127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            ticket = spice_proxy.issue("web", None, qemu_port)
            channels = []
            for _ in range(4):  # one connection per SPICE channel, on one ticket
                reader, writer, status = await _connect(port, f"CONNECT {ticket}:{qemu_port} HTTP/1.0")
                assert status.startswith("HTTP/1.0 200")
                assert await asyncio.wait_for(reader.readexactly(2), 5) == b"\r\n", "the end of the answer"
                channels.append((reader, writer))
            for i, (reader, writer) in enumerate(channels):
                writer.write(f"channel{i}".encode())
                await writer.drain()
                assert await asyncio.wait_for(reader.read(100), 5) == f"qemu:channel{i}".encode()
                writer.close()

            refused = [
                f"CONNECT {'0' * 40}:{qemu_port} HTTP/1.0",  # no such ticket
                f"CONNECT {ticket}:{qemu_port + 1} HTTP/1.0",  # another port than the ticket's
                f"GET http://{ticket}:{qemu_port}/ HTTP/1.0",  # not a tunnel
                "garbage",
            ]
            for line in refused:
                _reader, writer, status = await _connect(port, line)
                assert status.startswith("HTTP/1.0 403"), line
                writer.close()

            spice_proxy.TICKETS[ticket] = (*spice_proxy.TICKETS[ticket][:3], time.time() - 1)
            _reader, writer, status = await _connect(port, f"CONNECT {ticket}:{qemu_port} HTTP/1.0")
            assert status.startswith("HTTP/1.0 403"), "an expired ticket admits nothing"
            writer.close()
        finally:
            await spice_proxy.stop()
            upstream.close()

    asyncio.run(scenario())


def test_the_proxy_says_when_the_vm_cannot_be_reached():
    async def scenario():
        server = await spice_proxy.start("127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            closed = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
            dead_port = closed.sockets[0].getsockname()[1]
            closed.close()
            await closed.wait_closed()
            ticket = spice_proxy.issue("web", None, dead_port)
            _reader, writer, status = await _connect(port, f"CONNECT {ticket}:{dead_port} HTTP/1.0")
            assert status.startswith("HTTP/1.0 502")
            writer.close()
        finally:
            await spice_proxy.stop()

    asyncio.run(scenario())


def test_an_open_session_does_not_hold_the_services_stop():
    async def scenario():
        upstream = await _upstream()
        qemu_port = upstream.sockets[0].getsockname()[1]
        server = await spice_proxy.start("127.0.0.1", 0)
        ticket = spice_proxy.issue("web", None, qemu_port)
        reader, _writer, status = await _connect(
            server.sockets[0].getsockname()[1], f"CONNECT {ticket}:{qemu_port} HTTP/1.0"
        )
        assert status.startswith("HTTP/1.0 200")
        await asyncio.wait_for(spice_proxy.stop(), 10)
        assert await asyncio.wait_for(reader.read(), 5) in (b"\r\n", b""), "the session is closed"
        upstream.close()

    asyncio.run(scenario())


def test_the_proxy_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(spice, "PROXY_PORT", 0)
    assert asyncio.run(spice_proxy.start()) is None


# ---- The node's setup ----


@pytest.mark.skipif(
    os.geteuid() != 0 or shutil.which("openssl") is None, reason="the script runs as root, with openssl"
)
def test_the_setup_script_makes_certificates_and_enables_tls_once(tmp_path):
    conf = tmp_path / "qemu.conf"
    conf.write_text('#spice_tls = 1\n#spice_tls_x509_cert_dir = "/etc/pki/libvirt-spice"\n')
    pki = tmp_path / "pki"
    # A systemctl of its own: the node's real libvirt is never restarted by a test. libvirtd "runs", virtqemud does not.
    bin_dir, calls = tmp_path / "bin", tmp_path / "systemctl.calls"
    bin_dir.mkdir()
    (bin_dir / "systemctl").write_text(
        f'#!/bin/sh\necho "$*" >> {calls}\n[ "$1" = is-active ] && [ "$3" != libvirtd.service ] && exit 3\nexit 0\n'
    )
    (bin_dir / "systemctl").chmod(0o755)
    env = {
        **os.environ,
        "HYPERLITE_SPICE_TLS_DIR": str(pki),
        "HYPERLITE_QEMU_CONF": str(conf),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
    }
    script = str(REPO / "scripts" / "ensure-spice-tls.sh")
    first = subprocess.run(["bash", script], env=env, capture_output=True, text=True, check=True)
    assert "SPICE in TLS" in first.stdout
    assert calls.read_text().splitlines() == [
        "is-active --quiet libvirtd.service",
        "restart libvirtd.service",
        "is-active --quiet virtqemud.service",
    ], "only the running daemon is restarted"
    text = conf.read_text()
    assert text.count("\nspice_tls = 1\n") == 1 and f'spice_tls_x509_cert_dir = "{pki}"' in text
    subprocess.run(
        ["openssl", "verify", "-CAfile", pki / "ca-cert.pem", pki / "server-cert.pem"], check=True, capture_output=True
    )
    assert sorted(p.name for p in pki.iterdir()) == ["ca-cert.pem", "server-cert.pem", "server-key.pem"], (
        "no CA key left"
    )
    assert oct((pki / "server-key.pem").stat().st_mode & 0o777) == "0o400"

    files = {p: p.read_bytes() for p in [conf, *pki.iterdir()]}
    second = subprocess.run(["bash", script], env=env, capture_output=True, text=True, check=True)
    assert second.stdout == "" and {p: p.read_bytes() for p in files} == files, "idempotent: nothing changed"
    assert len(calls.read_text().splitlines()) == 3, "and libvirt is not restarted again"

    conf.write_text("spice_tls = 0\n")
    subprocess.run(["bash", script], env=env, capture_output=True, text=True, check=True)
    assert conf.read_text() == "spice_tls = 0\n", "an administrator's own setting is theirs"
