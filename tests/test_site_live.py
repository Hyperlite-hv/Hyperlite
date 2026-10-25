"""The root part of a live migration between clusters (app/core/site_live.py, hyperlite-daemon's transfer.live_*):
the destination's definition the source sends, the sockets at both ends, the CPU check and what the target defines
once the VM runs there. libvirt is a stand-in; the real migration is the lab's (docs/design/multi-cluster.md, 9.2)."""

import os
import shutil
import socket
import stat
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import libvirt
import pytest

from app.core import privileged, site_live, site_transfer
from app.daemon.registry import DaemonError
from tests.test_site_relay import RUNNING, UUID

INCOMING = "a" * 32


class Domain:
    def __init__(self, xml, active=True):
        self.xml, self.active = xml, active

    def XMLDesc(self, flags=0):
        return self.xml

    def isActive(self):
        return self.active


class Conn:
    def __init__(self, domains=(), networks=("prod",)):
        self.domains, self.networks, self.defined = (
            {d.xml and ET.fromstring(d.xml).findtext("name"): d for d in domains},
            networks,
            [],
        )

    def lookupByName(self, name):
        if name not in self.domains:
            raise libvirt.libvirtError("no domain")
        return self.domains[name]

    def networkLookupByName(self, name):
        if name not in self.networks:
            raise libvirt.libvirtError("no network")

    def getCapabilities(self):
        return "<capabilities><guest><arch name='x86_64'><emulator>/usr/bin/qemu-system-x86_64</emulator></arch></guest></capabilities>"

    def defineXML(self, xml):
        self.defined.append(xml)

    def compareHypervisorCPU(self, *args):
        return libvirt.VIR_CPU_COMPARE_INCOMPATIBLE if "Sapphire" in args[4] else libvirt.VIR_CPU_COMPARE_SUPERSET


@pytest.fixture()
def dirs(monkeypatch):
    """A short directory: a Unix socket's path is at most 107 bytes, which pytest's own temporary paths pass."""
    base = Path(tempfile.mkdtemp(prefix="hl"))
    monkeypatch.setattr(site_transfer, "STATE", base / "state")
    monkeypatch.setattr(site_transfer, "RUN", base / "run")
    monkeypatch.setattr(site_live, "RUN", base / "run")
    yield base
    shutil.rmtree(base, ignore_errors=True)


def _incoming(volumes=("/var/lib/libvirt/images/web-disk0.qcow2",)):
    spec = {"name": "web", "uuid": UUID, "disks": [{"dev": "sda"}], "nics": [{"mac": "52:54:00:12:34:56"}]}
    site_transfer._save(
        {
            "id": INCOMING,
            "spec": spec,
            "created_at": 0,
            "disks": [{"path": p, "kind": "file", "format": "qcow2", "written": False} for p in volumes],
        }
    )


def test_the_source_sends_its_running_vm_on_the_targets_volumes_and_networks():
    source = (
        RUNNING.replace("/var/lib/libvirt/images/web-disk0.qcow2", "/srv/source/web.raw")
        .replace("type='qcow2'", "type='raw'")
        .replace("network='prod'", "network='lan'")
    )
    xml, devs = site_live.destination_xml(
        Conn([Domain(source)]), "web", ["/var/lib/libvirt/images/web-disk0.qcow2"], ["prod"]
    )
    root = ET.fromstring(xml)
    disk = root.find("devices/disk[@device='disk']")
    assert devs == ["sda"] and disk.find("source").get("file") == "/var/lib/libvirt/images/web-disk0.qcow2"
    assert disk.find("driver").get("type") == "qcow2" and "/srv/source" not in xml
    assert root.find("devices/interface/source").attrib == {"network": "prod"}
    with pytest.raises(site_transfer.TransferError, match="A volume for every disk"):
        site_live.destination_xml(Conn([Domain(source)]), "web", [], ["prod"])


def test_the_target_prepares_expects_and_defines_what_it_checked(dirs):
    _incoming()
    conn = Conn()
    with pytest.raises(site_transfer.TransferError, match="No network 'mgmt'"):
        site_live.prepare(conn, INCOMING, ["mgmt"])
    ready = site_live.prepare(conn, INCOMING, ["prod"])
    assert ready["sockets"].endswith(f"/{INCOMING}/") and (dirs / "run" / INCOMING).is_dir()
    want = site_live.expected(conn, INCOMING)
    assert (
        want["networks"] == ["prod"] and want["emulators"] == ["/usr/bin/qemu-system-x86_64"] and want["uuid"] == UUID
    )
    # Arrived running: defined from what it runs, once checked; its record and sockets gone.
    conn.domains["web"] = Domain(RUNNING)
    assert site_live.define(conn, INCOMING) == {"name": "web"} and conn.defined == [RUNNING]
    assert not (dirs / "run" / INCOMING).exists()
    with pytest.raises(site_transfer.TransferError, match="No such incoming"):
        site_live.expected(conn, INCOMING)


def test_what_runs_is_checked_again_before_it_is_defined(dirs):
    _incoming()
    conn = Conn([Domain(RUNNING.replace("</devices>", "<hostdev mode='subsystem' type='pci'/></devices>"))])
    site_live.prepare(conn, INCOMING, ["prod"])
    with pytest.raises(site_transfer.TransferError, match="not what was prepared"):
        site_live.define(conn, INCOMING)
    assert conn.defined == []


def test_the_source_listens_on_three_sockets_the_api_accepts_on(dirs):
    fds = site_live.listen(INCOMING)
    try:
        names = sorted(os.listdir(dirs / "run" / INCOMING))
        assert names == ["disks.sock", "libvirt.sock", "memory.sock"]
        assert stat.S_IMODE(os.stat(dirs / "run" / INCOMING / "libvirt.sock").st_mode) == 0o600
        listener = socket.socket(fileno=fds[1])
        client = socket.socket(socket.AF_UNIX)
        client.connect(str(dirs / "run" / INCOMING / "memory.sock"))
        accepted, _ = listener.accept()
        client.sendall(b"ram")
        assert accepted.recv(3) == b"ram"
        for s in (client, accepted, listener):
            s.close()
    finally:
        for fd in (fds[0], fds[2]):
            os.close(fd)
    site_live.close(INCOMING)
    assert not (dirs / "run" / INCOMING).exists()


def test_the_cpu_check_says_whether_the_target_runs_the_vms_cpu():
    assert site_live.cpu_check(Conn(), "<cpu mode='custom'><model>Skylake-Client</model></cpu>")["compatible"]
    assert site_live.cpu_check(Conn(), "<cpu mode='custom'><model>SapphireRapids</model></cpu>") == {
        "compatible": False,
        "detail": "incompatible",
    }
    with pytest.raises(site_transfer.TransferError, match="Not a CPU"):
        site_live.cpu_check(Conn(), "<cpu")


@pytest.mark.parametrize(
    ("op", "args"),
    [
        ("transfer.live_connect", {"incoming": INCOMING, "kind": "ssh"}),
        ("transfer.live_connect", {"incoming": "../x", "kind": "libvirt"}),
        ("transfer.live_run", {"incoming": INCOMING, "vm": "web", "volumes": [], "networks": []}),
        ("transfer.live_run", {"incoming": INCOMING, "vm": "web", "volumes": ["/x"], "flags": 8}),
        ("transfer.live_prepare", {"incoming": INCOMING, "networks": ["x"] * 17}),
        ("transfer.cpu_check", {"cpu_xml": ""}),
    ],
)
def test_the_daemon_refuses_arguments_out_of_bounds(op, args):
    with pytest.raises(DaemonError) as e:
        privileged.run(op, user="ana", **args)
    assert e.value.kind == "invalid_arguments"
