"""Replication on a real QEMU VM (TCG is enough): a full point, incrementals that hold exactly the writes made between
them, a stopped VM left alone or started paused for its copy, and a chain that still restores after the share moved.

Needs a libvirt system daemon and QEMU: set HYPERLITE_TEST_LIBVIRT=1 to run it (the CI's e2e job does)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.core import backup_integrity, replication

pytestmark = pytest.mark.skipif(os.environ.get("HYPERLITE_TEST_LIBVIRT") != "1", reason="needs libvirt and QEMU")

IMAGES = Path("/var/lib/libvirt/images")
VM = "e2e-replication"


def qemu_io(path, command, readonly=False):
    args = ["qemu-io", "-f", "qcow2", *(["-r"] if readonly else []), str(path), "-c", command]
    return subprocess.run(args, capture_output=True, text=True, check=False)


def holds(path, offset, pattern):
    r = qemu_io(path, f"read -P {pattern} {offset} 64k", readonly=True)
    return r.returncode == 0 and "Pattern verification failed" not in r.stdout


@pytest.fixture()
def vm(database):
    import libvirt

    conn = libvirt.open("qemu:///system")
    disk = IMAGES / f"{VM}.qcow2"
    target = IMAGES / f"{VM}-target"
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir()
    subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", str(disk), "64M"], check=True)
    domain = conn.defineXML(
        f"""<domain type='qemu'><name>{VM}</name><memory unit='MiB'>64</memory><vcpu>1</vcpu>
        <os><type arch='x86_64' machine='q35'>hvm</type></os><devices>
        <disk type='file' device='disk'><driver name='qemu' type='qcow2'/><source file='{disk}'/>
        <target dev='vda' bus='virtio'/></disk></devices></domain>"""
    )
    yield domain, disk, target
    if not domain.isActive():
        domain.create()
    for checkpoint in reversed(domain.listAllCheckpoints()):
        checkpoint.delete(0)
    domain.destroy()
    domain.undefine()
    disk.unlink(missing_ok=True)
    shutil.rmtree(target, ignore_errors=True)
    conn.close()


def test_a_chain_of_points_follows_every_write_and_restores_anywhere(vm, tmp_path):
    domain, disk, target = vm
    qemu_io(disk, "write -P 0x11 0 64k")
    domain.create()
    assert replication.replicate(VM, str(target)) == "complet"
    domain.destroy()
    qemu_io(disk, "write -P 0x22 1048576 64k")  # written while stopped: the bitmap in the qcow2 tracks it
    domain.create()
    assert replication.replicate(VM, str(target)) == "incremental"
    domain.destroy()
    assert replication.replicate(VM, str(target)) == "incremental"  # it ran since: started paused for its copy
    assert not domain.isActive()  # and stopped again, the guest never ran
    assert replication.replicate(VM, str(target)) == "inchange"
    domain.create()
    domain.destroy()
    qemu_io(disk, "write -P 0x33 2097152 64k")
    assert replication.replicate(VM, str(target)) == "incremental"  # started and written between two runs

    points = sorted((target / VM).iterdir())
    assert len(points) == 4
    newest = points[-1]
    assert backup_integrity.verify(newest)[0] == backup_integrity.VERIFIED
    assert backup_integrity.read_manifest(newest)["replication"]["parent"] == points[-2].name
    for offset, pattern in ((0, "0x11"), (1048576, "0x22"), (2097152, "0x33")):
        assert holds(newest / "vda.qcow2", offset, pattern)
    assert not holds(points[0] / "vda.qcow2", 1048576, "0x22")  # the first point is the disk as it was then

    # Only the newest checkpoint stays, so the disk carries one bitmap.
    assert [c.getName() for c in domain.listAllCheckpoints()] == [f"hl-{newest.name}"]

    # The other site mounts the share elsewhere: the relative backing files still lead through the chain.
    moved = IMAGES / f"{VM}-moved"
    shutil.rmtree(moved, ignore_errors=True)
    shutil.copytree(target, moved)
    try:
        restored = tmp_path / "restored.qcow2"
        subprocess.run(
            ["qemu-img", "convert", "-O", "qcow2", str(moved / VM / newest.name / "vda.qcow2"), str(restored)],
            check=True,
        )
        assert subprocess.run(["qemu-img", "compare", "-U", str(disk), str(restored)], check=False).returncode == 0
    finally:
        shutil.rmtree(moved, ignore_errors=True)


def test_a_hot_backup_of_a_replicated_vm_keeps_the_chain(vm, tmp_path):
    from app.core import backups

    domain, disk, target = vm
    qemu_io(disk, "write -P 0x44 0 64k")
    domain.create()
    replication.replicate(VM, str(target))
    dest = IMAGES / f"{VM}-backup"
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir()
    try:
        paths = backups.backup_hot(None, domain, VM, dest, task_id="t", disks=None)
        assert holds(paths[0], 0, "0x44")
        assert replication.replicate(VM, str(target)) == "incremental"  # the checkpoint survived the backup
    finally:
        shutil.rmtree(dest, ignore_errors=True)
