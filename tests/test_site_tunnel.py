"""The tunnel of a live migration between clusters (app/core/site_tunnel.py), the target's end: each channel the
source opens is connected through the daemon (a stand-in socket pair here), its bytes carried both ways, and the
`libvirt` channel goes through the relay, which closes the whole tunnel on a call that is not a migration's."""

import asyncio
import contextlib
import socket
import threading

from app.core import site_relay, site_tunnel
from tests.test_site_relay import EXPECTED, call


class FakeWebSocket:
    def __init__(self, incoming):
        self.incoming, self.sent = list(incoming), []

    async def receive_bytes(self):
        if not self.incoming:
            await asyncio.sleep(0.2)  # what the far end answered is read meanwhile
            raise ConnectionError("the source closed the tunnel")
        return self.incoming.pop(0)

    async def send_bytes(self, data):
        self.sent.append(data)


def _run(ws, peers, refused, answer=None):
    def connect(kind):
        ours, theirs = socket.socketpair()
        peers[kind] = theirs
        if answer:  # the far end (a QEMU), answering on a thread of its own
            threading.Thread(target=answer, args=(kind, theirs), daemon=True).start()
        return ours

    async def go():
        with contextlib.suppress(ConnectionError):  # the source closed the tunnel: the end of the test
            await site_tunnel.serve(ws, connect, lambda: site_relay.CallFilter(EXPECTED), refused.append)

    asyncio.run(go())


def test_a_channel_carries_both_ways():
    peers, refused, got = {}, [], []

    def qemu(kind, sock):
        got.append((kind, sock.recv(3)))
        sock.sendall(b"ok")

    ws = FakeWebSocket(
        [site_tunnel.frame(site_tunnel.OPEN, 1, bytes([1])), site_tunnel.frame(site_tunnel.DATA, 1, b"ram")]
    )
    _run(ws, peers, refused, qemu)
    assert got == [("memory", b"ram")] and site_tunnel.frame(site_tunnel.DATA, 1, b"ok") in ws.sent and refused == []


def test_the_relay_closes_the_tunnel_on_a_call_that_is_not_a_migrations():
    peers, refused = {}, []
    ws = FakeWebSocket([
        site_tunnel.frame(site_tunnel.OPEN, 1, bytes([0])),
        site_tunnel.frame(site_tunnel.DATA, 1, call(1)),
        site_tunnel.frame(site_tunnel.DATA, 1, call(11)),  # define a domain: not a migration's
        site_tunnel.frame(site_tunnel.DATA, 1, call(2)),
    ])  # fmt: skip
    _run(ws, peers, refused)
    assert refused and "Not a migration's call" in refused[0]
    peers["libvirt"].settimeout(1)
    assert peers["libvirt"].recv(1024) == call(1)  # what passed before; nothing after
    assert ws.incoming == [site_tunnel.frame(site_tunnel.DATA, 1, call(2))]


def test_a_channel_of_an_unknown_kind_ends_the_tunnel():
    peers, refused = {}, []
    ws = FakeWebSocket([site_tunnel.frame(site_tunnel.OPEN, 1, bytes([9]))])
    try:
        _run(ws, peers, refused)
    except ValueError as e:
        assert "does not open" in str(e)
    else:
        raise AssertionError("an unknown channel was opened")
    assert peers == {}
