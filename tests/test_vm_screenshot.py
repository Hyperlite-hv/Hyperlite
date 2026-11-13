"""Console thumbnail (app/core/screenshot.py, daemon operation vm.screenshot): libvirt's PPM screen scaled down and
written as a PNG, only for a running VM, only with vm.console."""

import struct
import zlib

import libvirt
import pytest

from app.core import privileged, screenshot
from app.daemon.registry import DaemonError


def _ppm(w, h, rgb=(10, 20, 30)):
    return f"P6\n# made by a test\n{w} {h}\n255\n".encode() + bytes(rgb) * (w * h)


def _png_size_and_pixels(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", data[16:24])
    idat_len = struct.unpack(">I", data[33:37])[0]
    raw = zlib.decompress(data[41 : 41 + idat_len])
    return w, h, raw


def test_a_screen_is_scaled_and_written_as_png():
    w, h, raw = _png_size_and_pixels(screenshot.thumbnail(_ppm(1024, 768), 256))
    assert (w, h) == (256, 192)
    assert len(raw) == h * (1 + w * 3) and raw[1:4] == bytes((10, 20, 30))
    assert _png_size_and_pixels(screenshot.thumbnail(_ppm(200, 100), 256))[:2] == (200, 100)  # never enlarged


@pytest.mark.parametrize(
    "bad", [b"P3\n1 1\n255\n0 0 0", b"P6\n2 2\n65535\n" + b"\0" * 24, b"P6\n4 4\n255\n" + b"\0" * 5]
)
def test_anything_else_is_refused(bad):
    with pytest.raises(ValueError):
        screenshot.parse_ppm(bad)


class _Stream:
    def __init__(self, data):
        self.data, self.done = data, False

    def recvAll(self, handler, opaque):
        for i in range(0, len(self.data), 4096):
            handler(self, self.data[i : i + 4096], opaque)

    def finish(self):
        self.done = True

    def abort(self):
        pass


class _Dom:
    def __init__(self, name, running):
        self._name, self.running = name, running

    def isActive(self):
        return self.running

    def screenshot(self, stream, screen, flags):
        return "image/x-portable-pixmap"


class _Conn:
    def __init__(self):
        self.domains = {"web": _Dom("web", True), "off": _Dom("off", False)}

    def lookupByName(self, name):
        if name not in self.domains:
            raise libvirt.libvirtError("no domain")
        return self.domains[name]

    def newStream(self, flags):
        return _Stream(_ppm(640, 480))


def test_the_daemon_takes_a_running_vms_screen(daemon_libvirt):
    daemon_libvirt(_Conn())
    shot = privileged.run("vm.screenshot", vm="web", max_width=320)
    assert (shot["width"], shot["height"]) == (320, 240)
    with pytest.raises(DaemonError) as e:
        privileged.run("vm.screenshot", vm="off")
    assert e.value.kind == "refused"


def test_the_route_answers_a_png_or_why_not(client, auth_headers, daemon_libvirt):
    daemon_libvirt(_Conn())
    admin = auth_headers("root")
    r = client.get("/vms/web/screenshot?width=160", headers=admin)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"
    assert client.get("/vms/off/screenshot", headers=admin).status_code == 422
