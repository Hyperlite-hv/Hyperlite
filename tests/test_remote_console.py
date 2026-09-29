"""The console of a VM on a remote node is relayed through that node's SSH connection (checked host key), not
opened on the local host."""

import asyncio
import threading

import asyncssh
import pytest
from starlette.websockets import WebSocketDisconnect

from app.routers.vms import console, runtime


class _Node:
    """A real SSH server standing for a cluster node, and a TCP server standing for the VM's VNC server on it."""

    def __init__(self, tmp_path):
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.tmp_path = tmp_path
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._start())
        self.ready.set()
        self.loop.run_forever()

    async def _start(self):
        host_key = asyncssh.generate_private_key("ssh-ed25519")
        self.client_key = asyncssh.generate_private_key("ssh-ed25519")
        (self.tmp_path / "id").write_bytes(self.client_key.export_private_key())
        (self.tmp_path / "known_hosts").write_text(
            f"[127.0.0.1]:{{port}} {host_key.export_public_key().decode().strip()}\n"
        )

        async def vnc(reader, writer):
            data = await reader.read(100)
            writer.write(b"VNC:" + data)
            await writer.drain()
            writer.close()

        self.vnc = await asyncio.start_server(vnc, "127.0.0.1", 0)
        self.vnc_port = self.vnc.sockets[0].getsockname()[1]
        authorized = asyncssh.import_authorized_keys(self.client_key.export_public_key().decode())

        class Server(asyncssh.SSHServer):
            def connection_requested(self, dest_host, dest_port, orig_host, orig_port):
                return True  # direct-tcpip, what `ssh -W` and the relay use

        self.ssh = await asyncssh.listen(
            "127.0.0.1", 0, server_host_keys=[host_key], authorized_client_keys=authorized, server_factory=Server
        )
        self.ssh_port = self.ssh.sockets[0].getsockname()[1]
        text = (self.tmp_path / "known_hosts").read_text().replace("{port}", str(self.ssh_port))
        (self.tmp_path / "known_hosts").write_text(text)

    def start(self):
        self.thread.start()
        self.ready.wait(10)
        return self

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)


@pytest.fixture()
def node(tmp_path, monkeypatch):
    n = _Node(tmp_path).start()
    monkeypatch.setattr(
        console,
        "get_node",
        lambda name: (
            {"hostname": "127.0.0.1", "ssh_port": n.ssh_port, "ssh_user": "root"} if name == "node-b" else None
        ),
    )
    monkeypatch.setattr(console, "get_cluster_private_key_path", lambda: tmp_path / "id")
    monkeypatch.setattr(console, "KNOWN_HOSTS", tmp_path / "known_hosts")
    yield n
    n.stop()


def test_the_console_of_a_remote_vm_goes_through_its_node(client, node):
    runtime.CONSOLE_TICKETS["t1"] = ("web", node.vnc_port, 10**12, "node-b")
    with client.websocket_connect("/vms/web/console?ticket=t1") as ws:
        ws.send_bytes(b"hello")
        assert ws.receive_bytes() == b"VNC:hello"


def test_an_unknown_host_key_is_refused(client, node, tmp_path):
    (tmp_path / "known_hosts").write_text("")  # the node's key was never recorded
    runtime.CONSOLE_TICKETS["t2"] = ("web", node.vnc_port, 10**12, "node-b")
    with pytest.raises(WebSocketDisconnect) as closed, client.websocket_connect("/vms/web/console?ticket=t2") as ws:
        ws.receive_bytes()
    assert closed.value.code == 1011


def test_a_ticket_is_bound_to_its_vm(client):
    runtime.CONSOLE_TICKETS["t3"] = ("other", 5900, 10**12, None)
    with pytest.raises(WebSocketDisconnect) as closed, client.websocket_connect("/vms/web/console?ticket=t3") as ws:
        ws.receive_bytes()
    assert closed.value.code == 4401


def test_the_ticket_endpoint_looks_the_vm_up_on_the_requested_node(client, auth_headers, monkeypatch):
    seen = []

    class Conn:
        def lookupByName(self, name):
            raise console.libvirt.libvirtError("no domain")

        def close(self):
            pass

    def fake_open(node=None):
        seen.append(node)
        return Conn()

    monkeypatch.setattr(console, "open_conn", fake_open)
    headers = auth_headers("alice")
    client.post("/vms/web/console-ticket?node=node-b", headers=headers)
    client.post("/vms/web/console-ticket?node=local", headers=headers)
    client.post("/vms/web/terminal-ticket?node=node-b", headers=headers)
    assert seen == ["node-b", None, "node-b"]
