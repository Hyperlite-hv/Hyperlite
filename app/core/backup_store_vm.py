"""A VM backed up into a backup store, and restored from one (docs/design/backup-store.md, step 1).

Every disk type takes the same path: files (qcow2, raw) and block devices (ZFS zvols, LVM volumes, iSCSI LUNs).

- **A running VM**: libvirt's backup API in pull mode. QEMU exports each disk, frozen as it was when the job began,
  as a read-only NBD export on a Unix socket while the VM runs; the guest agent freezes the file systems while the
  job starts. Writes the guest makes meanwhile go on as usual: QEMU keeps the blocks they replace in a scratch file
  until they are read. The job ends when Hyperlite has read every disk (abortJob, as libvirt's pull mode wants).
- **A stopped VM**: each disk served the same way by `qemu-nbd`, read-only (the caller holds the VM's lock, so it is
  not started meanwhile).
- **Reading**: `nbdcopy` (libnbd) streams each export to a pipe, which is cut into the store's chunks, a few chunks
  digested, compressed and written at once.
- **Restoring**: `qemu-nbd` opens the disk to write (a new qcow2 or raw file, or a block device) and `nbdcopy`
  writes into it what Hyperlite reads from the store, zero chunks skipped on a new file.
"""

import contextlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import libvirt

from app.core import backup_integrity, backup_store, guest_agent, nfs_permissions, paths

logger = logging.getLogger(__name__)

WORKERS = 4  # chunks digested, compressed and written at once
READY_TIMEOUT_S = 30  # for an export's socket to appear
# Where QEMU binds a running VM's export: a fixed place, which the QEMU AppArmor rule installed with the package
# allows (scripts/ensure-backup-apparmor.sh); libvirt adds the scratch files to a VM's profile, not this socket.
SOCKETS = Path("/run/hyperlite/backup")


class BackupFailed(Exception):
    pass


@dataclass(frozen=True)
class Disk:
    dev: str  # the guest's name for it (vda, sdb...): the export's and the index's name
    source: str  # a file or a block device
    format: str  # qcow2 or raw, as QEMU opens it

    @property
    def block(self):
        return not Path(self.source).is_file()


def disks_of(domain):
    """The VM's disks that a backup copies: every disk device with a source, file or block (CD-ROMs are not)."""
    root = ET.fromstring(domain.XMLDesc(0))
    out = []
    for disk in root.findall("devices/disk"):
        if disk.get("device") != "disk":
            continue
        target, source, driver = disk.find("target"), disk.find("source"), disk.find("driver")
        path = None if source is None else source.get("file") or source.get("dev")
        if target is None or not path:
            continue
        out.append(Disk(target.get("dev"), path, (driver.get("type") if driver is not None else None) or "raw"))
    return out


def _wait_for(path, process=None):
    deadline = time.monotonic() + READY_TIMEOUT_S
    while not Path(path).exists():
        if process is not None and process.poll() is not None:
            raise BackupFailed(f"the export stopped before it served: {process.stderr.read().strip()[:300]}")
        if time.monotonic() > deadline:
            raise BackupFailed("the disk export did not start in time")
        time.sleep(0.1)


def _workdir(for_qemu=False):
    """A private directory for one job. for_qemu: QEMU writes its export's socket and scratch files there, so it
    belongs to the user QEMU runs as, and is reachable by it (the service's TMP is 0755)."""
    base = paths.TMP / "backup-store"
    base.mkdir(parents=True, exist_ok=True)
    os.chmod(base, 0o711)  # noqa: S103 - traversable, not listable: QEMU reaches its own job's directory
    directory = Path(tempfile.mkdtemp(prefix="job-", dir=base))
    if for_qemu:
        uid, gid, _ = nfs_permissions.qemu_identity()
        os.chown(directory, uid, gid)
        os.chmod(directory, 0o770)  # noqa: S103 - QEMU's user and group only
    return directory


def _pull_xml(disks, socket, scratch, skipped):
    root = ET.Element("domainbackup", mode="pull")
    ET.SubElement(root, "server", transport="unix", socket=str(socket))
    container = ET.SubElement(root, "disks")
    for disk in disks:
        entry = ET.SubElement(container, "disk", name=disk.dev, backup="yes", type="file", exportname=disk.dev)
        ET.SubElement(entry, "scratch", file=str(scratch / f"{disk.dev}.scratch"))
    for dev in skipped:
        ET.SubElement(container, "disk", name=dev, backup="no")
    return ET.tostring(root, encoding="unicode")


@contextlib.contextmanager
def exported_running(domain, disks, workdir):
    """{dev: NBD URI} of a running VM's disks, frozen as they were when the job began, for as long as the block runs."""
    SOCKETS.mkdir(parents=True, exist_ok=True)
    uid, gid, _ = nfs_permissions.qemu_identity()
    os.chown(SOCKETS, uid, gid)
    os.chmod(SOCKETS, 0o770)  # noqa: S103 - QEMU's user and group only
    socket = SOCKETS / f"{workdir.name}.sock"
    others = [d.dev for d in disks_of(domain) if d.dev not in {x.dev for x in disks}]
    frozen = False
    if guest_agent.state(domain) == guest_agent.CONNECTED and guest_agent.bound(domain):
        try:
            domain.fsFreeze()
            frozen = True
        except libvirt.libvirtError:
            logger.info("The guest agent of %s did not freeze its file systems", domain.name(), exc_info=True)
    try:
        domain.backupBegin(_pull_xml(disks, socket, workdir, others), None, 0)
    finally:
        # The export holds the disks as they were when the job began: the guest only needs to be frozen that long.
        if frozen:
            try:
                domain.fsThaw()
            except libvirt.libvirtError:
                logger.warning("Could not thaw the file systems of %s", domain.name(), exc_info=True)
    try:
        _wait_for(socket)
        yield {d.dev: f"nbd+unix:///{d.dev}?socket={socket}" for d in disks}
    finally:
        with contextlib.suppress(libvirt.libvirtError):  # a pull-mode job ends when its reader says so
            domain.abortJob()
        with contextlib.suppress(FileNotFoundError):
            socket.unlink()


@contextlib.contextmanager
def exported_stopped(disks, workdir):
    """{dev: NBD URI} of a stopped VM's disks, each served read-only by qemu-nbd."""
    servers, uris = [], {}
    try:
        for disk in disks:
            socket = workdir / f"{disk.dev}.sock"
            process = subprocess.Popen(
                ["qemu-nbd", "--read-only", "--persistent", "--shared=4", "--cache=none", "--aio=threads",
                 f"--format={disk.format}", f"--socket={socket}", f"--export-name={disk.dev}", disk.source],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            )  # fmt: skip
            servers.append(process)
            _wait_for(socket, process)
            uris[disk.dev] = f"nbd+unix:///{disk.dev}?socket={socket}"
        yield uris
    finally:
        for process in servers:
            process.terminate()
        for process in servers:
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill()


def export_size(uri):
    r = subprocess.run(["nbdinfo", "--size", uri], capture_output=True, text=True, timeout=60, check=False)
    if r.returncode != 0:
        raise BackupFailed(f"the export cannot be read: {r.stderr.strip()[:300]}")
    return int(r.stdout.strip())


def _read_full(stream, n):
    buf = bytearray(n)
    view, got = memoryview(buf), 0
    while got < n:
        k = stream.readinto(view[got:])
        if not k:
            raise BackupFailed(f"the disk ended early: {got} of {n} bytes")
        got += k
    return bytes(buf)


def read_into_store(store, uri, size, progress=None):
    """Every byte of the export `uri` (`size` bytes) cut into the store's chunks; returns the disk's Index.
    `progress(done_bytes)` is called as chunks are stored."""
    process = subprocess.Popen(
        ["nbdcopy", "--", uri, "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0
    )  # fmt: skip
    digests = []
    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            pending = []
            for offset in range(0, size, store.chunk_size):
                content = _read_full(process.stdout, min(store.chunk_size, size - offset))
                pending.append(pool.submit(store.put, content))
                while len(pending) > 2 * WORKERS:  # memory bounded: a few chunks in flight
                    digests.append(pending.pop(0).result())
                    if progress:
                        progress(len(digests) * store.chunk_size)
            digests += [f.result() for f in pending]
        if process.stdout.read(1):
            raise BackupFailed("the disk is larger than its export said")
    finally:
        process.stdout.close()
        try:
            returncode = process.wait(60)
        except subprocess.TimeoutExpired:
            process.kill()
            returncode = process.wait()
    if returncode != 0:
        raise BackupFailed(f"reading the disk failed: {process.stderr.read().decode(errors='replace').strip()[:300]}")
    return backup_store.Index(size, store.chunk_size, tuple(digests))


def _firmware_files(domain):
    """The VM's NVRAM and TPM state, as files kept whole in the backup (they are small)."""
    with tempfile.TemporaryDirectory(dir=_workdir()) as tmp:
        names = backup_integrity.save_firmware_state(domain, Path(tmp))
        return {name: (Path(tmp) / name).read_bytes() for name in names}


def backup_vm(domain, store, mode="live", disks=None, progress=None, files=None, manifest_extra=None):
    """Back the VM up into `store`: every disk (or `disks`, a subset of disks_of), its definition, its firmware state
    and `files` ({name: bytes}, kept whole). Returns the backup's directory. A running VM keeps running; a stopped one
    must stay stopped meanwhile (the caller holds its lock)."""
    disks = disks if disks is not None else disks_of(domain)
    if not disks:
        raise BackupFailed("This VM has no disk to back up")
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    running = domain.isActive()
    workdir = _workdir(for_qemu=running)
    try:
        exported = exported_running(domain, disks, workdir) if running else exported_stopped(disks, workdir)
        indexes, total, done = {}, 0, 0
        with exported as uris:
            sizes = {dev: export_size(uri) for dev, uri in uris.items()}
            total = sum(sizes.values()) or 1
            for disk in disks:
                base = done

                def step(n, base=base):
                    if progress:
                        progress(min(99, int(100 * (base + n) / total)))

                indexes[disk.dev] = read_into_store(store, uris[disk.dev], sizes[disk.dev], step)
                done += sizes[disk.dev]
        files = {**(files or {}), **_firmware_files(domain)}
        files["domain.xml"] = domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE | libvirt.VIR_DOMAIN_XML_SECURE).encode()
        manifest = {
            **(manifest_extra or {}),
            "vm": domain.name(),
            "mode": mode,
            "running": bool(running),
            "formats": {d.dev: d.format for d in disks},
            "block": sorted(d.dev for d in disks if d.block),
        }
        return store.write_backup(domain.UUIDString(), stamp, indexes, manifest, files)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _device_size(path):
    r = subprocess.run(["blockdev", "--getsize64", str(path)], capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise BackupFailed(f"{path}: its size cannot be read: {r.stderr.strip()[:200]}")
    return int(r.stdout.strip())


def restore_disk(store, vm_uuid, stamp, dev, target, target_format="qcow2", progress=None):
    """Write a backup's disk `dev` to `target`: a new file (qcow2 or raw, created here at the disk's size) or an
    existing block device at least that large (a zvol, an LVM volume, a LUN), written into. A damaged or missing chunk
    stops the restore with an error: a disk is never written with a wrong block in it."""
    index = store.index_of(vm_uuid, stamp, dev)  # refused here, with a clear error, before any process starts
    target = Path(target)
    block = target.exists() and not target.is_file()
    if block:
        if _device_size(target) < index.size:
            raise BackupFailed(f"{target} is smaller than the disk to restore ({index.size} bytes)")
        fmt = "raw"
    else:
        if target.exists():
            raise BackupFailed(f"{target} already exists")
        fmt = target_format
        subprocess.run(["qemu-img", "create", "-q", "-f", fmt, str(target), str(index.size)], check=True)
    workdir = _workdir()
    socket = workdir / "target.sock"
    server = subprocess.Popen(
        ["qemu-nbd", f"--format={fmt}", "--cache=none", "--aio=threads", "--discard=unmap", "--detect-zeroes=unmap",
         f"--socket={socket}", str(target)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )  # fmt: skip
    try:
        _wait_for(socket, server)
        # A new file reads as zeros: its zero chunks are skipped. A device's are written, it may hold anything.
        copy = ["nbdcopy", *([] if block else ["--target-is-zero"]), "--", "-", f"nbd+unix:///?socket={socket}"]
        writer = subprocess.Popen(copy, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                pending = []

                def read(i):
                    return store.get(index.digests[i], min(index.chunk_size, index.size - i * index.chunk_size))

                for i in range(len(index.digests)):
                    pending.append(pool.submit(read, i))
                    while len(pending) > 2 * WORKERS or (pending and i == len(index.digests) - 1):
                        writer.stdin.write(pending.pop(0).result())
                        if progress:
                            progress(min(99, int(100 * (i + 1 - len(pending)) / max(1, len(index.digests)))))
        except BaseException:
            writer.kill()
            raise
        finally:
            with contextlib.suppress(BrokenPipeError):
                writer.stdin.close()
            returncode = writer.wait()
        if returncode != 0:
            raise BackupFailed(f"writing {dev} failed: {writer.stderr.read().decode(errors='replace').strip()[:300]}")
    except BaseException:
        if not block:
            target.unlink(missing_ok=True)  # a half-written new disk is no disk
        raise
    finally:
        server.terminate()
        try:
            server.wait(30)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(workdir, ignore_errors=True)


def store_backup_of(path):
    """(Store, vm uuid, time) when `path` is a backup in a store (<store>/vm/<uuid>/<time>), else None. An encrypted
    store is opened with its key from the cluster's secrets."""
    from app.core import backup_stores

    path = Path(path)
    if len(path.parents) < 3 or not (path.parents[2] / backup_store.CONFIG).is_file():
        return None
    return backup_stores.open_root(path.parents[2]), path.parent.name, path.name


@contextlib.contextmanager
def files_of(found):
    """A temporary directory holding a store backup's files decrypted (vm-config.json, the NVRAM, the TPM state), as
    a directory backup holds them: what the restore reads."""
    store, vm_uuid, stamp = found
    manifest = json.loads((store.backup_dir(vm_uuid, stamp) / "manifest.json").read_text())
    with tempfile.TemporaryDirectory(dir=_workdir()) as tmp:
        names = manifest.get("files") or {
            p.name: {}
            for p in store.backup_dir(vm_uuid, stamp).iterdir()
            if p.suffix != ".fidx" and p.name != "manifest.json"
        }
        for name in names:
            (Path(tmp) / name).write_bytes(store.read_file(vm_uuid, stamp, name))
        yield Path(tmp)


def verify(path):
    """(status, problems, disks checked) of a backup in a store, in backup_integrity's terms."""
    store, vm_uuid, stamp = store_backup_of(path)
    try:
        manifest = json.loads((Path(path) / "manifest.json").read_text())
        disks = len(manifest["disks"])
        bad = store.verify(vm_uuid, stamp)
    except (OSError, ValueError, KeyError) as e:
        return backup_integrity.CORRUPT, [f"The backup cannot be read: {e}"], 0
    problems = [f"chunk {d.hex()[:16]}... is damaged or missing" for d in bad]
    for name in manifest.get("files") or {}:
        try:
            store.read_file(vm_uuid, stamp, name)
        except (OSError, backup_store.DamagedChunk) as e:
            problems.append(f"{name}: {e}")
    return (backup_integrity.CORRUPT if problems else backup_integrity.VERIFIED), problems, disks
