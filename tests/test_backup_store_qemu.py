"""Backups into a backup store on a real QEMU VM (TCG is enough; docs/design/backup-store.md, step 1): every disk
type through the same path (a qcow2 file, a raw file, a block device), a running VM's disks frozen as they were when the
backup began while the guest keeps writing, a stopped VM, a second backup that stores nothing new, and restores to
files and to a block device that read back identical. A damaged chunk stops a restore instead of writing a wrong disk.

Needs a libvirt system daemon, QEMU, qemu-nbd and libnbd's tools, and the AppArmor rule of
scripts/ensure-backup-apparmor.sh where AppArmor runs: set HYPERLITE_TEST_LIBVIRT=1 to run it (the CI's e2e job
does)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.core import backup_store, backup_store_vm, paths

pytestmark = pytest.mark.skipif(os.environ.get("HYPERLITE_TEST_LIBVIRT") != "1", reason="needs libvirt and QEMU")

IMAGES = Path("/var/lib/libvirt/images")
VM = "e2e-backup-store"
MIB = 1024 * 1024


def qemu_io(fmt, path, command, readonly=False):
    args = ["qemu-io", "-f", fmt, *(["-r"] if readonly else []), str(path), "-c", command]
    return subprocess.run(args, capture_output=True, text=True, check=True)


def same(fmt_a, a, fmt_b, b):
    r = subprocess.run(["qemu-img", "compare", "-U", "-f", fmt_a, "-F", fmt_b, str(a), str(b)], capture_output=True)
    return r.returncode == 0


@pytest.fixture()
def vm(database, monkeypatch):
    import libvirt

    # QEMU writes its scratch files in the job's directory: somewhere its user can reach, as the service's TMP is.
    tmp = IMAGES / f"{VM}-tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(mode=0o755)
    monkeypatch.setattr(paths, "TMP", tmp)
    qcow2, raw = IMAGES / f"{VM}.qcow2", IMAGES / f"{VM}.raw"
    subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", str(qcow2), "40M"], check=True)
    subprocess.run(["qemu-img", "create", "-q", "-f", "raw", str(raw), "20M"], check=True)
    loop_file = IMAGES / f"{VM}-loop.img"
    subprocess.run(["truncate", "-s", "24M", str(loop_file)], check=True)
    loop = subprocess.run(["losetup", "-f", "--show", str(loop_file)], capture_output=True, text=True, check=True)
    device = loop.stdout.strip()
    qemu_io("qcow2", qcow2, f"write -P 0xab 0 {9 * MIB}")
    qemu_io("qcow2", qcow2, f"write -P 0xcd {30 * MIB} {MIB}")
    qemu_io("raw", raw, f"write -P 0x11 {MIB} {3 * MIB}")
    qemu_io("raw", device, f"write -P 0x22 0 {5 * MIB}")
    conn = libvirt.open("qemu:///system")
    domain = conn.defineXML(
        f"""<domain type='qemu'><name>{VM}</name><memory unit='MiB'>64</memory><vcpu>1</vcpu>
        <os><type arch='x86_64' machine='q35'>hvm</type></os><devices>
        <disk type='file' device='disk'><driver name='qemu' type='qcow2'/><source file='{qcow2}'/>
        <target dev='vda' bus='virtio'/></disk>
        <disk type='file' device='disk'><driver name='qemu' type='raw'/><source file='{raw}'/>
        <target dev='vdb' bus='virtio'/></disk>
        <disk type='block' device='disk'><driver name='qemu' type='raw'/><source dev='{device}'/>
        <target dev='vdc' bus='virtio'/></disk></devices></domain>"""
    )
    store = backup_store.create(IMAGES / f"{VM}-store")
    yield {"domain": domain, "store": store, "qcow2": qcow2, "raw": raw, "device": device, "tmp": tmp}
    if domain.isActive():
        domain.destroy()
    domain.undefineFlags(libvirt.VIR_DOMAIN_UNDEFINE_CHECKPOINTS_METADATA)  # a backup's checkpoints with it
    subprocess.run(["losetup", "-d", device], check=False)
    for path in (qcow2, raw, loop_file):
        path.unlink(missing_ok=True)
    for directory in (tmp, store.root):
        shutil.rmtree(directory, ignore_errors=True)
    for restored in IMAGES.glob(f"{VM}-restored-*"):
        restored.unlink()
    conn.close()


def _restored(store, vm_uuid, stamp, dev, fmt):
    target = IMAGES / f"{VM}-restored-{dev}.{fmt}"
    backup_store_vm.restore_disk(store, vm_uuid, stamp, dev, target, fmt)
    return target


def test_every_disk_of_a_running_vm_restores_identical(vm):
    domain, store = vm["domain"], vm["store"]
    domain.create()
    backup_store_vm.backup_vm(domain, store)
    [(vm_uuid, stamp, manifest)] = store.backups()
    assert manifest["running"] and manifest["block"] == ["vdc"] and set(manifest["disks"]) == {"vda", "vdb", "vdc"}
    assert store.verify(vm_uuid, stamp) == []
    assert same("qcow2", vm["qcow2"], "qcow2", _restored(store, vm_uuid, stamp, "vda", "qcow2"))
    assert same("raw", vm["raw"], "raw", _restored(store, vm_uuid, stamp, "vdb", "raw"))
    assert same("raw", vm["device"], "qcow2", _restored(store, vm_uuid, stamp, "vdc", "qcow2"))
    assert domain.isActive()  # it ran all along
    domain.destroy()
    # Into a block device: written into, over what it held.
    qemu_io("raw", vm["device"], f"write -P 0x77 0 {MIB}")
    backup_store_vm.restore_disk(store, vm_uuid, stamp, "vdc", vm["device"])
    assert qemu_io("raw", vm["device"], f"read -P 0x22 0 {5 * MIB}", readonly=True).stdout.count("failed") == 0


def test_a_running_vms_backup_holds_its_disks_as_they_were_when_it_began(vm):
    """The guest writes while its disk is read: the write reaches the disk, never the backup."""
    domain, store = vm["domain"], vm["store"]
    domain.create()
    disks = [d for d in backup_store_vm.disks_of(domain) if d.dev == "vda"]
    work = backup_store_vm._workdir(for_qemu=True)
    with backup_store_vm.exported_running(domain, disks, work) as uris:
        # Through the virtio disk's own block backend, as the guest's writes go.
        command = f'qemu-io -d /machine/peripheral/virtio-disk0/virtio-backend "write -P 0xee 0 {4 * MIB}"'
        subprocess.run(["virsh", "qemu-monitor-command", "--hmp", VM, command], check=True, capture_output=True)
        index = backup_store_vm.read_into_store(store, uris["vda"], backup_store_vm.export_size(uris["vda"]))
    assert store.get(index.digests[0])[: 4 * MIB] == b"\xab" * (4 * MIB)
    domain.destroy()
    assert "failed" not in qemu_io("qcow2", vm["qcow2"], f"read -P 0xee 0 {4 * MIB}", readonly=True).stdout


def test_a_stopped_vm_and_a_second_backup_that_stores_nothing_new(vm):
    domain, store = vm["domain"], vm["store"]
    backup_store_vm.backup_vm(domain, store)
    chunks = sorted((store.root / backup_store.CHUNKS).rglob("*"))
    domain.create()
    backup_store_vm.backup_vm(domain, store)  # the same disks, running this time
    assert sorted((store.root / backup_store.CHUNKS).rglob("*")) == chunks
    first, second = store.backups()
    assert not first[2]["running"] and second[2]["running"]
    assert same("qcow2", vm["qcow2"], "qcow2", _restored(store, first[0], first[1], "vda", "qcow2"))


def test_a_damaged_chunk_stops_the_restore_and_leaves_no_disk(vm):
    domain, store = vm["domain"], vm["store"]
    backup_store_vm.backup_vm(domain, store)
    [(vm_uuid, stamp, _)] = store.backups()
    digest = store.index_of(vm_uuid, stamp, "vda").digests[0]
    path = store.chunk_path(digest)
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 0xFF
    path.write_bytes(bytes(raw))
    target = IMAGES / f"{VM}-restored-vda.qcow2"
    with pytest.raises(backup_store.DamagedChunk):
        backup_store_vm.restore_disk(store, vm_uuid, stamp, "vda", target)
    assert not target.exists()


@pytest.mark.parametrize("encrypted", [False, True])
def test_a_backup_job_into_a_store_and_its_restores(vm, monkeypatch, encrypted):
    """The whole path Hyperlite takes: run_backup into a named store, then restore_backup over the VM (its file
    disks swapped in, its block device written in place) and to a new VM."""
    import libvirt

    from app.core import backup_stores, backups

    domain, store = vm["domain"], vm["store"]
    monkeypatch.setattr(backup_stores, "_pool_dir", lambda name: store.root.parent)
    if encrypted:  # a new encrypted store beside the fixture's, with a master recovery key
        _, public = backup_store.new_master_key()
        backup_stores.create("e2e", "pool", f"{VM}-enc", encrypted=True, master_public_key=public.decode())
    else:
        backup_stores.create("e2e", "pool", store.root.name)
    domain.create()
    backup_id = backups.run_backup(VM, store="e2e", username="ana")
    domain.destroy()
    for fmt, path in (("qcow2", vm["qcow2"]), ("raw", vm["raw"]), ("raw", vm["device"])):
        qemu_io(fmt, path, f"write -P 0x99 0 {2 * MIB}")  # what the restore must undo
    assert backups.restore_backup(backup_id, "overwrite", username="ana") == {"vm": VM, "mode": "overwrite"}
    assert "failed" not in qemu_io("qcow2", vm["qcow2"], f"read -P 0xab 0 {9 * MIB}", readonly=True).stdout
    assert "failed" not in qemu_io("raw", vm["raw"], f"read -P 0x11 {MIB} {3 * MIB}", readonly=True).stdout
    assert "failed" not in qemu_io("raw", vm["device"], f"read -P 0x22 0 {5 * MIB}", readonly=True).stdout
    assert not list(IMAGES.glob(f".{VM}*.restore-*"))  # nothing staged left behind

    conn = libvirt.open("qemu:///system")
    networks = [n.name() for n in conn.listAllNetworks()]
    if not networks:
        pytest.skip("no libvirt network to restore a new VM on")
    copy = f"{VM}-copy"
    try:
        backups.restore_backup(backup_id, "new", new_name=copy, username="ana", network=networks[0])
        restored = conn.lookupByName(copy)
        assert len(backup_store_vm.disks_of(restored)) == 3
        first = backup_store_vm.disks_of(restored)[0].source
        assert same("qcow2", vm["qcow2"], "qcow2", first)
    finally:
        try:
            restored = conn.lookupByName(copy)
            for disk in backup_store_vm.disks_of(restored):
                Path(disk.source).unlink(missing_ok=True)
            restored.undefineFlags(libvirt.VIR_DOMAIN_UNDEFINE_NVRAM)
        except libvirt.libvirtError:
            pass
        conn.close()
        shutil.rmtree(store.root.parent / f"{VM}-enc", ignore_errors=True)


def _write_through_the_guest(offset_mib, length_mib, pattern):
    """A write as the guest makes it: through the virtio disk's own block backend, so its dirty bitmap counts it."""
    command = (
        f'qemu-io -d /machine/peripheral/virtio-disk0/virtio-backend "write -P {pattern} {offset_mib}M {length_mib}M"'
    )
    subprocess.run(["virsh", "qemu-monitor-command", "--hmp", VM, command], check=True, capture_output=True)


def _manifest(store, index=-1):
    return store.backups()[index][2]


def test_a_second_backup_reads_only_the_chunks_the_guest_changed(vm):
    """The qcow2 disk keeps a dirty bitmap from one backup to the next; the raw file and the block device are read
    whole. Only this store's last checkpoint stays; a backup made incrementally restores identical."""
    domain, store = vm["domain"], vm["store"]
    domain.create()
    backup_store_vm.backup_vm(domain, store)
    first = _manifest(store)
    assert first["read"] == {"vda": "full", "vdb": "full", "vdc": "full"} and first["checkpoint"]
    _write_through_the_guest(8, 10, "0x5a")  # chunks 2, 3 and 4 of 4 MiB
    backup_store_vm.backup_vm(domain, store)
    second = _manifest(store)
    assert second["read"] == {"vda": "incremental", "vdb": "full", "vdc": "full"}
    assert second["chunks_read"]["vda"] == 3 and second["full_at"] == first["full_at"]
    assert [c.getName() for c in domain.listAllCheckpoints()] == [second["checkpoint"]]
    backup_store_vm.backup_vm(domain, store)
    assert _manifest(store)["chunks_read"]["vda"] == 0  # nothing changed
    domain.destroy()
    vm_uuid, stamp, _ = store.backups()[1]
    assert same("qcow2", vm["qcow2"], "qcow2", _restored(store, vm_uuid, stamp, "vda", "qcow2"))
    assert store.verify(vm_uuid, stamp) == []


def test_a_backup_of_the_stopped_vm_keeps_the_next_one_incremental(vm):
    domain, store = vm["domain"], vm["store"]
    domain.create()
    backup_store_vm.backup_vm(domain, store)
    domain.destroy()
    backup_store_vm.backup_vm(domain, store)  # stopped: read whole, no checkpoint of its own
    stopped = _manifest(store)
    assert stopped["read"]["vda"] == "full" and "checkpoint" not in stopped
    domain.create()
    _write_through_the_guest(0, 1, "0x3c")
    backup_store_vm.backup_vm(domain, store)
    last = _manifest(store)
    assert last["read"]["vda"] == "incremental" and last["chunks_read"]["vda"] == 1
    domain.destroy()
    vm_uuid, stamp, _ = store.backups()[-1]
    assert same("qcow2", vm["qcow2"], "qcow2", _restored(store, vm_uuid, stamp, "vda", "qcow2"))


def test_without_its_checkpoint_a_backup_reads_whole(vm):
    """Hyperlite drops the checkpoints before what libvirt refuses with one (a snapshot, a move, a restore): the next
    backup cannot count the changed blocks and reads the disk whole."""
    from app.core import checkpoints

    domain, store = vm["domain"], vm["store"]
    domain.create()
    backup_store_vm.backup_vm(domain, store)
    checkpoints.release(domain)
    backup_store_vm.backup_vm(domain, store)
    assert _manifest(store)["read"]["vda"] == "full"
    domain.destroy()
