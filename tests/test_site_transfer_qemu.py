"""A migration between clusters on real disks (docs/design/multi-cluster.md, section 9.1): a stopped VM's qcow2 and raw
disks read through hyperlite-daemon's exports, sent as the stream another cluster receives, written into new volumes
and defined there; both ends are this host, the VM arriving under another name and UUID. A cancelled incoming leaves
no volume behind.

Needs a libvirt system daemon, qemu-nbd and python3-libnbd: set HYPERLITE_TEST_LIBVIRT=1 to run it (the CI's e2e job
does)."""

import contextlib
import os
import subprocess
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from app.core import site_stream, site_transfer

pytestmark = pytest.mark.skipif(os.environ.get("HYPERLITE_TEST_LIBVIRT") != "1", reason="needs libvirt and QEMU")

IMAGES = Path("/var/lib/libvirt/images")
VM = "e2e-site-transfer"
MIB = 1024 * 1024


def qemu_io(fmt, path, command):
    subprocess.run(["qemu-io", "-f", fmt, str(path), "-c", command], capture_output=True, text=True, check=True)


def same(fmt_a, a, fmt_b, b):
    return (
        subprocess.run(["qemu-img", "compare", "-U", "-f", fmt_a, "-F", fmt_b, str(a), str(b)], check=False).returncode
        == 0
    )


@pytest.fixture()
def conn(database, tmp_path, monkeypatch):
    import libvirt

    monkeypatch.setattr(site_transfer, "STATE", tmp_path / "transfer")
    connection = libvirt.open("qemu:///system")
    _forget_moved(connection)
    qcow2, raw = IMAGES / f"{VM}.qcow2", IMAGES / f"{VM}.raw"
    subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", str(qcow2), "40M"], check=True)
    subprocess.run(["qemu-img", "create", "-q", "-f", "raw", str(raw), "20M"], check=True)
    qemu_io("qcow2", qcow2, f"write -P 0xab 0 {9 * MIB}")
    qemu_io("qcow2", qcow2, f"write -P 0xcd {30 * MIB} {MIB}")
    qemu_io("raw", raw, f"write -P 0x11 {MIB} {3 * MIB}")
    connection.defineXML(
        f"""<domain type='qemu'><name>{VM}</name><uuid>{uuid.uuid4()}</uuid><memory unit='MiB'>64</memory>
        <vcpu>1</vcpu><os><type arch='x86_64' machine='q35'>hvm</type></os><devices>
        <disk type='file' device='disk'><driver name='qemu' type='qcow2'/><source file='{qcow2}'/>
        <target dev='vda' bus='virtio'/></disk>
        <disk type='file' device='disk'><driver name='qemu' type='raw'/><source file='{raw}'/>
        <target dev='vdb' bus='virtio'/></disk></devices></domain>"""
    )
    yield connection, {"vda": ("qcow2", qcow2), "vdb": ("raw", raw)}
    for name in (VM, f"{VM}-moved"):
        with contextlib.suppress(libvirt.libvirtError):
            connection.lookupByName(name).undefine()
    _forget_moved(connection)
    for path in (qcow2, raw):
        path.unlink(missing_ok=True)
    connection.close()


def _forget_moved(connection):
    """The volumes a previous run left in the default pool, removed through libvirt (it keeps a list of its own)."""
    from app.core import libvirt_utils

    pool = libvirt_utils.lookup_pool(connection, "default")
    pool.refresh(0)
    for volume in pool.listAllVolumes(0):
        if volume.name().startswith(f"{VM}-moved-"):
            volume.delete(0)


def _incoming(connection):
    described = site_transfer.describe(connection, VM)
    spec = {**described["spec"], "name": f"{VM}-moved", "uuid": str(uuid.uuid4())}
    devs = [d["dev"] for d in spec["disks"]]
    return site_transfer.prepare(connection, spec, ["default"] * len(devs), [described["allocated"][d] for d in devs])


def test_a_stopped_vm_arrives_identical(conn):
    connection, sources = conn
    prepared = _incoming(connection)
    for disk in prepared["disks"]:
        out_fd, size = site_transfer.export(connection, VM, disk["dev"])
        in_fd, in_size = site_transfer.import_disk(prepared["incoming"], disk["dev"])
        assert in_size == size
        with os.fdopen(out_fd, "rb") as source, os.fdopen(in_fd, "wb") as target:
            sink = site_stream.ImportSink(target)
            chunks = site_stream.encode(site_stream.pieces_from_export(source))
            site_stream.decode(chunks, size, sink.write, sink.zero)
            sink.end()
    moved = site_transfer.define(connection, prepared["incoming"], [])
    assert moved == {"name": f"{VM}-moved", "started": False}
    root = ET.fromstring(connection.lookupByName(f"{VM}-moved").XMLDesc(0))
    paths = {
        d.find("target").get("dev"): d.find("source").get("file") for d in root.findall("devices/disk[@device='disk']")
    }
    # A raw source arrives as a qcow2 volume of the file pool, and the definition says so (the lab's two-cluster run
    # found it declared raw).
    assert {d.find("driver").get("type") for d in root.findall("devices/disk[@device='disk']")} == {"qcow2"}
    for dev, (fmt, path) in sources.items():
        assert paths[dev] != str(path) and same(fmt, path, "qcow2", paths[dev])


def test_a_cancelled_incoming_leaves_no_volume(conn):
    connection, _ = conn
    prepared = _incoming(connection)
    assert list(IMAGES.glob(f"{VM}-moved-disk*"))
    site_transfer.abort(connection, prepared["incoming"])
    assert not list(IMAGES.glob(f"{VM}-moved-disk*"))
    with pytest.raises(site_transfer.TransferError, match="No such incoming"):
        site_transfer.define(connection, prepared["incoming"], [])
