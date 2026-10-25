"""The serial console of a VM: a ticket for a running VM with a serial port, a serial port added to a VM without one
(effective at its next start), `virsh console` on the VM's own node, relayed to the browser through a pty."""

import xml.etree.ElementTree as ET

import pytest
from starlette.websockets import WebSocketDisconnect

from app.routers.vms import console

WITH_SERIAL = "<domain><name>web</name><devices><disk/><console type='pty'/></devices></domain>"
WITHOUT = "<domain><name>web</name><devices><disk/></devices></domain>"


class Domain:
    def __init__(self, live, inactive=None, active=True):
        self.live = live
        self.inactive = inactive or live
        self.active = active

    def XMLDesc(self, flags=0):
        return self.inactive if flags & console.libvirt.VIR_DOMAIN_XML_INACTIVE else self.live

    def isActive(self):
        return self.active

    def UUIDString(self):
        return "6f1c2d3e-0000-4000-8000-000000000001"


class Conn:
    def __init__(self, domain):
        self.domain = domain
        self.defined = []

    def lookupByName(self, name):
        if self.domain is None or name != "web":
            raise console.libvirt.libvirtError("no domain")
        return self.domain

    def defineXML(self, xml):
        self.defined.append(xml)
        self.domain.inactive = xml

    def close(self):
        pass


@pytest.fixture()
def libvirt_vm(monkeypatch, daemon_libvirt):
    """open_conn() (read-only) and hyperlite-daemon's connection answer with the VM this test sets; the nodes the API
    asked for are recorded."""
    state = {"conn": daemon_libvirt(Conn(None)), "nodes": []}

    def fake_open(node=None, readonly=False):
        assert readonly, "the API reads the VM, the daemon adds its serial port"
        state["nodes"].append(node)
        return state["conn"]

    monkeypatch.setattr(console, "open_conn", fake_open)

    def set_vm(domain):
        state["conn"] = daemon_libvirt(Conn(domain))
        return state["conn"]

    state["set"] = set_vm
    return state


@pytest.fixture(autouse=True)
def _no_tickets_left():
    console.SERIAL_TICKETS.clear()
    yield
    console.SERIAL_TICKETS.clear()


def test_a_running_vm_with_a_serial_port_gets_a_ticket_on_its_node(client, auth_headers, libvirt_vm):
    libvirt_vm["set"](Domain(WITH_SERIAL))
    answer = client.post("/vms/web/serial-ticket?node=node-b", headers=auth_headers("root"))
    assert answer.status_code == 200
    assert libvirt_vm["nodes"] == ["node-b"]
    name, _expiry, node, username = console.SERIAL_TICKETS[answer.json()["ticket"]]
    assert (name, node, username) == ("web", "node-b", "root")


def test_a_vm_without_a_serial_port_gets_one_for_its_next_start(client, auth_headers, libvirt_vm):
    conn = libvirt_vm["set"](Domain(WITHOUT, active=True))
    headers = auth_headers("root")
    first = client.post("/vms/web/serial-ticket", headers=headers)
    assert first.status_code == 409 and "next start" in first.json()["detail"]
    devices = ET.fromstring(conn.defined[0]).find("devices")
    assert devices.find("serial").get("type") == "pty" and devices.find("serial/target").get("port") == "0"
    assert devices.find("console/target").attrib == {"type": "serial", "port": "0"}
    assert devices.find("disk") is not None, "the rest of the configuration is kept"

    again = client.post("/vms/web/serial-ticket", headers=headers)
    assert again.status_code == 409 and "takes effect at its next start" in again.json()["detail"]
    assert len(conn.defined) == 1, "added once"
    assert console.SERIAL_TICKETS == {}


def test_a_stopped_vm_without_one_gets_it_and_must_be_started(client, auth_headers, libvirt_vm):
    conn = libvirt_vm["set"](Domain(WITHOUT, active=False))
    answer = client.post("/vms/web/serial-ticket", headers=auth_headers("root"))
    assert answer.status_code == 409 and "Start the VM" in answer.json()["detail"]
    assert len(conn.defined) == 1


def test_a_stopped_vm_or_an_unknown_one_gets_no_ticket(client, auth_headers, libvirt_vm):
    headers = auth_headers("root")
    assert client.post("/vms/web/serial-ticket", headers=headers).status_code == 404
    libvirt_vm["set"](Domain(WITH_SERIAL, active=False))
    stopped = client.post("/vms/web/serial-ticket", headers=headers)
    assert stopped.status_code == 409 and "must be started" in stopped.json()["detail"]
    assert console.SERIAL_TICKETS == {}


def test_it_takes_the_graphical_console_privilege_not_the_administrator_role(client, auth_headers, libvirt_vm):
    """The same login prompt as the graphical console, which an observer opens: not the SSH terminal's root shell."""
    libvirt_vm["set"](Domain(WITH_SERIAL))
    assert client.post("/vms/web/serial-ticket").status_code == 401
    assert client.post("/vms/web/serial-ticket", headers=auth_headers("eve", role="viewer")).status_code == 200
    assert client.post("/vms/web/terminal-ticket", headers=auth_headers("bob", role="viewer")).status_code == 403


def test_the_serial_console_is_virsh_on_the_vms_node_by_its_uuid_relayed_both_ways(client, monkeypatch, libvirt_vm):
    """hyperlite-daemon runs `virsh console` on the VM's node, the VM named by the UUID libvirt gave it (no name can
    pass for an option of virsh), and hands its terminal to the API, which relays it."""
    from app.core import cluster, console_sessions

    libvirt_vm["set"](Domain(WITH_SERIAL))
    monkeypatch.setattr(cluster, "get_node", lambda name: {"name": name, "hostname": "10.0.0.2"})
    monkeypatch.setattr(cluster, "libvirt_uri_from_here", lambda node: f"qemu+ssh://root@{node['hostname']}/system")
    seen, real = [], console_sessions.open_pty

    def open_pty(argv, env, cols=80, rows=24):
        seen.append(argv)
        return real(["sh", "-c", "printf 'login: '; read who; echo \"hello $who\""], env, cols, rows)

    monkeypatch.setattr(console_sessions, "open_pty", open_pty)
    console.SERIAL_TICKETS["s1"] = ("web", 10**12, "node-b", "alice")
    with client.websocket_connect("/api/v1/vms/web/serial?ticket=s1") as ws:
        out = ""
        while "login: " not in out:
            out += ws.receive_text()
        ws.send_text("alice\r")
        while "hello alice" not in out:
            out += ws.receive_text()
    uuid = "6f1c2d3e-0000-4000-8000-000000000001"
    assert seen == [["virsh", "--quiet", "--connect", "qemu+ssh://root@10.0.0.2/system", "console", "--domain", uuid]]
    assert "s1" not in console.SERIAL_TICKETS, "a ticket is used once"
    assert console_sessions.open_count() == 0, "the session ended with the console"


@pytest.mark.parametrize("ticket", [("other", 10**12, None, "alice"), ("web", 0, None, "alice")])
def test_a_ticket_is_bound_to_its_vm_and_expires(client, ticket):
    console.SERIAL_TICKETS["s2"] = ticket
    with (
        pytest.raises(WebSocketDisconnect) as closed,
        client.websocket_connect("/api/v1/vms/web/serial?ticket=s2") as ws,
    ):
        ws.receive_text()
    assert closed.value.code == 4401
