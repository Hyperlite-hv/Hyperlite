"""Native VM backup. Before this, only qcow2 snapshots existed, and they do NOT
survive the loss of the source disk (same file). A backup is a complete,
self-contained copy stored elsewhere.

Two modes:
  - "cold" (VM stopped): a plain copy of the qcow2 disk(s) with qemu-img
    convert (native support for qcow2 conversion and compression).
  - "hot" (VM running): an EXTERNAL snapshot (a new overlay file, a backing
    file chain) freezes the source disk at an instant T without stopping the
    VM, the frozen file is copied, then blockCommit + pivot merge the overlay
    back into the current disk and remove the snapshot. Snapshots themselves
    avoid this mechanism because of libvirt/QEMU's restore limitation on
    external snapshots, but that limit does not apply here: the overlay is never
    restored directly, it is only used as a transient consistency point before
    being merged back.

Progress is REAL (unlike snapshots, where no statistic exists):
`qemu-img convert -p` writes a percentage on stdout, parsed here to feed
update_task_progress().

"""

import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path

import libvirt

from app.core import (
    backup_integrity,
    backup_retention,
    backup_use,
    checkpoints,
    cluster_lead,
    db_health,
    disk_images,
    disk_space,
    firmware,
    guest_agent,
    paths,
    replication,
    vm_locks,
)
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import open_conn, refresh_pools_for_paths
from app.core.safe_paths import safe_child
from app.core.tasks import create_task, finish_task, raise_if_cancelled, register_cancel, update_task_progress
from app.core.vm_builder import IMAGES_DIR
from app.core.vm_meta import get_vm_os_label, get_vm_ssh_user, set_vm_os_label, set_vm_ssh_user

logger = logging.getLogger(__name__)

DEFAULT_BACKUP_DIR = paths.BACKUPS
SCHEDULER_INTERVAL_S = 300  # checks due jobs every 5 minutes: enough, the granularity is the hour (HH:MM)

# Backups are serialized: only one at a time, the others wait their turn. Firing
# 4 manual backups in a burst on the same VM used to make UNRELATED requests
# (e.g. GET /networks) fail with 'database is locked' despite WAL mode and the
# 30 s timeout: 4 threads hammering the database at once (frequent progress
# updates during qemu-img convert, then possibly several simultaneous retention
# DELETEs) is enough to exceed even a generous timeout. Rather than raising the
# timeout again (which postpones the problem without solving it), backups run
# one by one. That also makes sense independently of SQLite, since several
# simultaneous qemu-img convert runs on the same host disk already fight for I/O
# bandwidth.
_backup_lock = threading.Lock()

_PROGRESS_RE = re.compile(r"\((\d+(?:\.\d+)?)/100%\)")


def _now():
    return datetime.now(UTC)


def _sha256_of(path):
    return backup_integrity.sha256_of(path)


def domain_disk_paths(domain):
    import xml.etree.ElementTree as ET

    root = ET.fromstring(domain.XMLDesc(0))
    paths = []
    for disk in root.findall(".//devices/disk"):
        if disk.get("device") != "disk":
            continue
        source = disk.find("source")
        target = disk.find("target")
        if source is not None and source.get("file") and target is not None:
            paths.append((target.get("dev"), source.get("file")))
    return paths


def block_disks(domain):
    """Target names (sda, vdb...) of a domain's disks that are block devices: ZFS zvols, iSCSI LUNs and LVM volumes."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(domain.XMLDesc(0))
    return [
        (d.find("target").get("dev") if d.find("target") is not None else "?")
        for d in root.findall(".//devices/disk")
        if d.get("device") == "disk" and d.get("type") == "block"
    ]


def block_disk_error(domain, action):
    """Why a backup or an export cannot run, or None. Both copy disk FILES only: a VM whose disks are all block
    devices failed with an unclear "No disk found", and one mixing a block disk with a file disk was saved without
    the block disk, silently."""
    devs = block_disks(domain)
    if not devs:
        return None
    return (
        f"{action} is not available yet for a VM with ZFS, iSCSI or LVM disks ({', '.join(devs)}): "
        "only disk files can be copied for now"
    )


def refuse_vm_with_block_disks(name, action):
    """block_disk_error by VM name, as a 422 answered at once (the backup or export itself runs in the background)."""
    from fastapi import HTTPException

    conn = open_conn(readonly=True)
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            return  # the background job reports a missing VM itself
        error = block_disk_error(domain, action)
        if error:
            raise HTTPException(status_code=422, detail=error)
    finally:
        conn.close()


def qemu_img_convert_with_progress(source, dest, task_id, base_pct, span_pct):
    """Copy with qemu-img convert -p, parse the real progress on stdout and report it
    in the task (base_pct/span_pct allow calling this several times, e.g. for
    several disks, without each one restarting from 0%)."""
    raise_if_cancelled(task_id)
    proc = subprocess.Popen(
        ["qemu-img", "convert", "-p", "-O", "qcow2", str(source), str(dest)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    # Cancelling the task stops the copy at once; the caller removes the partial files as for any failure.
    register_cancel(task_id, proc.terminate)
    # try/finally: proc.wait() ALWAYS happens, even if reading stdout or
    # update_task_progress() raises. Otherwise an exception raised from this loop
    # (e.g. a 'database is locked' from update_task_progress()) left the finished
    # qemu-img process never waited for, an orphan zombie. The process is reaped
    # either way, and the exception keeps propagating normally (handled by the
    # caller, see run_backup).
    try:
        last_pct = 0
        last_reported = -1
        last_write_time = 0.0
        for line in proc.stdout:
            m = _PROGRESS_RE.search(line)
            if m:
                last_pct = float(m.group(1))
                reported = int(base_pct + span_pct * last_pct / 100)
                # THROTTLE: qemu-img -p emits a progress line very often (even several times per
                # second on a fast disk), and every update_task_progress() is a SQLite WRITE. On
                # this database even a plain GET performs its own write (log_action() is called
                # everywhere, including for reads), so WAL mode does not protect against this
                # kind of writer-versus-writer contention (WAL solves reader-versus-writer, not
                # both directions). It only writes when the ROUNDED percentage has changed AND at
                # least 0.5 s has elapsed since the last write, which cuts the backup write volume
                # by one or two orders of magnitude without losing any granularity useful for a
                # progress bar (nobody can tell 47% from 48% displayed 10 times per second).
                now = time.monotonic()
                if reported != last_reported and now - last_write_time >= 0.5:
                    update_task_progress(task_id, reported)
                    last_reported = reported
                    last_write_time = now
        stderr = proc.stderr.read()
    finally:
        proc.wait()
        register_cancel(task_id)  # still cancellable between two disks
    raise_if_cancelled(task_id)
    if proc.returncode != 0:
        raise RuntimeError(f"qemu-img convert failed: {stderr.strip()[:400]}")


def backup_cold(domain, vm_name, dest_dir, task_id, disks=None):
    disks = disks if disks is not None else domain_disk_paths(domain)
    if not disks:
        raise RuntimeError("No disk found on this VM")
    dest_paths = []
    span = 90 / len(disks)
    for i, (dev, source) in enumerate(disks):
        dest = dest_dir / f"{dev}.qcow2"
        qemu_img_convert_with_progress(source, dest, task_id, base_pct=5 + i * span, span_pct=span)
        dest_paths.append(dest)
    return dest_paths


def backup_hot(conn, domain, vm_name, dest_dir, task_id, disks=None):
    """Transient external snapshot per disk -> copy of the frozen file -> blockCommit
    + pivot to merge back. The VM keeps running without interruption during the
    whole operation (only a very brief freeze when the snapshot itself is
    created, like any QEMU external snapshot). `disks`: an optional subset (e.g.
    a single disk for an export, see app/core/vm_export.py); the whole VM by
    default."""
    all_disks = domain_disk_paths(domain)
    if not all_disks:
        raise RuntimeError("No disk found on this VM")
    disks = disks if disks is not None else all_disks
    target_devs = {dev for dev, _ in disks}
    if checkpoints.names(domain):
        # A replicated VM (app/core/replication.py): libvirt refuses the external snapshot below while its checkpoint
        # exists, so the copy goes through libvirt's backup API instead, which leaves the replication chain intact.
        logger.info("Hot backup of %s through the backup API (it is replicated)", vm_name)
        update_task_progress(task_id, 10)
        paths = replication.copy_running(domain, disks, [dev for dev, _ in all_disks], dest_dir)
        update_task_progress(task_id, 85)
        return paths

    overlay_paths = {}
    disk_xml_parts = []
    # A disk that BELONGS to the VM but is absent from `disks` (a partial export, see
    # app/core/vm_export.py) must remain explicitly excluded (snapshot='no'):
    # otherwise libvirt still applies its default (internal) snapshot behaviour to
    # it, which would never be cleaned up since the merge loop below only walks
    # `disks`.
    for dev, _source in all_disks:
        if dev in target_devs:
            overlay = safe_child(IMAGES_DIR, f"{vm_name}.backup-{int(time.time())}.{dev}.qcow2")
            overlay_paths[dev] = overlay
            disk_xml_parts.append(f"<disk name='{dev}' snapshot='external'><source file='{overlay}'/></disk>")
        else:
            disk_xml_parts.append(f"<disk name='{dev}' snapshot='no'/>")
    snap_name = f"hyperlite-backup-{int(time.time())}"
    snap_xml = f"<domainsnapshot><name>{snap_name}</name><disks>{''.join(disk_xml_parts)}</disks></domainsnapshot>"

    # With Hyperlite Tools the guest file systems are frozen while the snapshot is taken (consistent copy).
    snap, quiesced = guest_agent.quiesced_snapshot(domain, snap_xml, libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_DISK_ONLY)
    logger.info("Hot backup of %s: snapshot %s", vm_name, "quiesced by the guest agent" if quiesced else "not quiesced")
    update_task_progress(task_id, 10)

    copy_failed = False
    try:
        dest_paths = []
        span = 70 / len(disks)
        for i, (dev, source) in enumerate(disks):
            dest = dest_dir / f"{dev}.qcow2"
            # We copy the FROZEN base (the original `source` file, no longer touched by the VM
            # while the overlay is active), not the overlay, which keeps growing with the VM's
            # activity.
            qemu_img_convert_with_progress(source, dest, task_id, base_pct=15 + i * span, span_pct=span)
            dest_paths.append(dest)
    except BaseException:
        copy_failed = True
        raise
    finally:
        # Merge the overlay back into the base for EVERY disk, even if the copy failed on
        # one of them: never leave the VM running indefinitely on a transient overlay (a
        # chain that grows without end, orphaned if Hyperlite restarts in the meantime).
        failures = _merge_overlays_back(domain, vm_name, disks, overlay_paths, snap)
        if failures and not copy_failed:
            raise RuntimeError(
                "The backup copy succeeded but merging the temporary overlay back failed ("
                + "; ".join(failures)
                + "): the VM still runs on it, and it was kept. Check the VM's disk chain before stopping it."
            )

    return dest_paths


# Committing the writes made during a backup back into the base: generous, the VM may have written a lot meanwhile.
MERGE_TIMEOUT_S = 3600


def _merge_overlays_back(domain, vm_name, disks, overlay_paths, snap):
    """blockCommit + pivot of each disk back to its base. An overlay is deleted ONLY once its disk pivoted back: while
    the pivot has not happened, the VM's disk chain still points to it and deleting it would lose the writes made
    during the backup (or leave a VM that no longer starts). Returns the failures, each already audited."""
    failures = []
    for dev, source in disks:
        try:
            domain.blockCommit(dev, str(source), None, 0, libvirt.VIR_DOMAIN_BLOCK_COMMIT_ACTIVE)
            deadline = time.monotonic() + MERGE_TIMEOUT_S
            while True:
                info = domain.blockJobInfo(dev, 0)
                if not info or (info.get("end", 0) and info.get("cur", 0) >= info["end"]):
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError(f"the merge did not finish within {MERGE_TIMEOUT_S} s")
                time.sleep(0.5)
            domain.blockJobAbort(dev, libvirt.VIR_DOMAIN_BLOCK_JOB_ABORT_PIVOT)
        except Exception as e:
            msg = describe_exception(e) if isinstance(e, libvirt.libvirtError) else str(e)
            failures.append(f"{dev}: {msg}")
            log_action("system", "backup_commit_warning", vm_name, "failed", f"{dev}: {msg}")
            logger.error("Merging the backup overlay of %s/%s failed, overlay kept: %s", vm_name, dev, msg)
            continue
        overlay = overlay_paths.get(dev)
        if overlay is not None:
            Path(overlay).unlink(missing_ok=True)
    if not failures:
        # Only when every disk is back on its base: the snapshot metadata is what shows an administrator which
        # overlay a disk still depends on.
        with contextlib.suppress(libvirt.libvirtError):
            snap.delete(libvirt.VIR_DOMAIN_SNAPSHOT_DELETE_METADATA_ONLY)
    return failures


def _write_vm_config(domain, dest_dir):
    """Record the hardware settings needed to rebuild the VM when restoring to a new location."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(domain.XMLDesc(0))
    memory_kib = int(root.findtext("memory") or 0)
    interface = root.find("./devices/interface/source")
    config = {
        "vcpu": int(root.findtext("vcpu") or 1),
        "memory_mb": max(memory_kib // 1024, 1),
        "network": interface.get("network") if interface is not None and interface.get("network") else "default",
        # A UEFI system disk does not boot with a BIOS: a VM restored to a new location keeps its firmware kind,
        # and gets its NVRAM and TPM state back from the backup (see backup_integrity.py).
        "firmware": firmware.of_domain(root),
        # Created with Hyperlite's cloud-init drive: a copy restored under a new name gets a new one (see
        # _reseed_iso).
        "cloud_init": any(
            (src.get("file") or "").endswith("-cloudinit.iso") for src in root.findall("./devices/disk/source")
        ),
        # Hyperlite's own data about the guest: a copy restored under a new name lost them (no OS shown, and the
        # SSH terminal did not know which account to open).
        "os_label": get_vm_os_label(root.findtext("name")),
        "ssh_user": get_vm_ssh_user(root.findtext("name")),
    }
    (dest_dir / "vm-config.json").write_text(json.dumps(config))


def _reseed_iso(config, source_vm, new_name):
    """The cloud-init drive of a VM restored under a new name, or None. Without it, the copy boots with the network
    configuration cloud-init wrote for the original's MAC address (netplan's `match: macaddress`), so it never gets
    an address, and with the original's host name and SSH host keys. A new instance id makes cloud-init configure
    it again, as for a clone (create_cloudinit_reseed_iso). Backups made before vm-config.json recorded it: guessed
    as for a clone, from the original's drive."""
    from app.core.vm_builder import create_cloudinit_reseed_iso

    used = config.get("cloud_init")
    if used is None:
        used = safe_child(IMAGES_DIR, f"{source_vm}-cloudinit.iso").exists()
    if not used:
        return None
    try:
        return create_cloudinit_reseed_iso(new_name)
    except (OSError, subprocess.CalledProcessError):
        logger.warning("No cloud-init drive for the restored VM %s", new_name, exc_info=True)
        return None


def _read_vm_config(src_dir):
    """Settings recorded at backup time; older backups fall back to the historical defaults."""
    config = {"vcpu": 1, "memory_mb": 1024, "network": "default", "firmware": "bios"}
    with contextlib.suppress(OSError, ValueError):
        config.update(json.loads((Path(src_dir) / "vm-config.json").read_text()))
    return config


def run_backup(vm_name, target_dir=None, job_id=None, username="system", claim=None, store=None):
    """Run a backup (choosing hot or cold according to the real state of the VM) and
    return the id of the `backups` row created. Synchronous: called from a
    thread by the endpoint (manual backup) or by the scheduler.

    _backup_lock: only one backup at a time on the WHOLE server (all VMs
    combined), see the comment above _backup_lock. A concurrent caller simply
    waits its turn instead of failing.

    `claim`: the vm_locks claim the endpoint took on the VM; without one the backup takes its own (VmBusy when
    another operation is running on the VM). Released when the backup ends.

    `store`: the name of a backup store (app/core/backup_stores.py) to back up into, instead of a directory."""
    if claim is None:
        claim = vm_locks.claim(vm_name, "a backup")
    with claim, _backup_lock:
        if store:
            return _run_store_backup(vm_name, store, job_id, username)
        return _run_backup_locked(vm_name, target_dir, job_id, username)


_allocated_bytes = disk_space.allocated_bytes


def check_free_space(target_root, disk_paths):
    """Refuse a backup that would not fit on its target (disk_space.ensure_room). A check only: it creates nothing.
    The default target is the system disk (data/backups)."""
    from app.core.backup_groups import validate_target  # late import: backup_groups imports this module

    needed = sum(_allocated_bytes(p) for p in disk_paths)
    target = os.path.normpath((validate_target(str(target_root)) if target_root else None) or DEFAULT_BACKUP_DIR)
    if not target.startswith("/"):  # validate_target already refuses it; spelled out for the path analysis
        raise RuntimeError(f"Invalid backup directory: {target_root}")
    disk_space.ensure_room(target, needed, "this backup")


def check_space_for(vm_name, target_dir=None):
    """Before a manual backup starts: refused at once (RuntimeError) when it would not fit, rather than reported as
    started and failing in its task. A check only: when the VM cannot be read, the backup's task reports why."""
    try:
        conn = open_conn(readonly=True)
    except libvirt.libvirtError:
        return
    try:
        domain = conn.lookupByName(vm_name)
        check_free_space(
            Path(paths.current(target_dir)) if target_dir else DEFAULT_BACKUP_DIR,
            [s for _d, s in domain_disk_paths(domain)],
        )
    except libvirt.libvirtError:
        return  # the backup itself says the VM is not found
    finally:
        conn.close()


# A directory backup's own files: what the daemon deletes in it (backup_files_only), nothing else.
BACKUP_FILES = {backup_integrity.MANIFEST, backup_integrity.NVRAM_FILE, backup_integrity.TPM_FILE, "vm-config.json"}
BACKUP_IMAGE_SUFFIXES = (".qcow2",)


def backup_target(target_dir):
    """The directory a backup is written under, as hyperlite-daemon accepts it (backup_groups.validate_target): the
    default directory, or an administrator's absolute directory outside the system's own. ValueError otherwise."""
    from app.core.backup_groups import GroupError, validate_target

    try:
        target = validate_target(str(target_dir)) if target_dir else None
    except GroupError as e:
        raise ValueError(str(e)) from None
    return Path(target) if target else DEFAULT_BACKUP_DIR


def backup_dir(path):
    """A directory backup's own directory, `<target>/<vm>/<time>` under a target backup_target accepts: what the
    daemon reads, verifies, restores from or deletes, whatever the records say (the API writes them). ValueError
    otherwise."""
    p = Path(str(path or ""))
    if not p.is_absolute() or ".." in p.parts or len(p.parts) < 4 or "\x00" in str(path):
        raise ValueError(f"Not a backup directory: {path}")
    backup_target(p.parent.parent)
    return p


def copy_vm(vm_name, target_root, task_id):
    """hyperlite-daemon's part of a directory backup (backup.run): the VM's disks copied cold or live into
    `<target_root>/<vm>/<time>`, its settings, its NVRAM and TPM state and the manifest beside them. Returns {"path",
    "mode", "size", "checksum"}; the partial directory is removed on any failure."""
    with libvirt_utils_local() as conn:
        try:
            domain = conn.lookupByName(vm_name)
        except libvirt.libvirtError:
            raise RuntimeError(f"VM '{vm_name}' not found") from None
        error = block_disk_error(domain, "A backup")
        if error:
            raise RuntimeError(error)
        mode = "live" if domain.isActive() else "cold"
        stamp = _now().strftime("%Y%m%dT%H%M%SZ")
        dest_dir = safe_child(safe_child(target_root, vm_name), stamp)
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            _write_vm_config(domain, dest_dir)
            backup_integrity.save_firmware_state(domain, dest_dir)
            firmware_kind = firmware.of_domain(ET.fromstring(domain.XMLDesc(0)))
            # A backup refused for want of space leaves nothing in its folder.
            check_free_space(target_root, [src for _dev, src in domain_disk_paths(domain)])
            if mode == "cold":
                dest_paths = backup_cold(domain, vm_name, dest_dir, task_id)
            else:
                dest_paths = backup_hot(conn, domain, vm_name, dest_dir, task_id)
            update_task_progress(task_id, 95)
            devs = [dev for dev, _source in domain_disk_paths(domain)]
            manifest = backup_integrity.write_manifest(
                dest_dir, vm_name, mode, firmware_kind, list(zip(devs, dest_paths, strict=False))
            )
        except BaseException:
            shutil.rmtree(dest_dir, ignore_errors=True)
            raise
    total_size = sum(f["size"] for f in manifest["files"])
    disk_sums = [f["sha256"] for f in manifest["files"] if f["role"] == "disk"]
    # Kept for the backups listed before manifests existed and for the API's single-disk field.
    checksum = disk_sums[0] if len(disk_sums) == 1 else None
    return {"path": str(dest_dir), "mode": mode, "size": total_size, "checksum": checksum}


def libvirt_utils_local():
    from app.core import libvirt_utils

    return libvirt_utils.local_conn()


def _run_backup_locked(vm_name, target_dir, job_id, username):
    """The API's part: the task and the record, hyperlite-daemon's copy (backup.run), the result recorded."""
    from app.core import privileged
    from app.daemon.registry import DaemonError

    conn = open_conn(readonly=True)
    try:
        try:
            domain = conn.lookupByName(vm_name)
        except libvirt.libvirtError:
            raise RuntimeError(f"VM '{vm_name}' not found") from None
        error = block_disk_error(domain, "A backup")
        if error:
            raise RuntimeError(error)
        mode = "live" if domain.isActive() else "cold"
    finally:
        conn.close()
    task_id = create_task("backup_vm", vm_name, node=None, username=username)
    backup_id = _store().create(vm_name, job_id, "", mode, _now().isoformat(), task_id)
    register_cancel(task_id, lambda: _cancel_in_daemon(task_id, username))
    try:
        try:
            done = privileged.run(
                "backup.run", user=username, timeout=JOB_TIMEOUT_S, vm=vm_name, target=target_dir, task_id=task_id
            )
        except DaemonError as e:
            raise RuntimeError(e.detail) from None
        _store().set_path(backup_id, done["path"])
        _store().mark_done(backup_id, done["size"], done["checksum"])
        finish_task(task_id, "succeeded")
        kind = "live" if done["mode"] == "live" else "offline"
        log_action(
            username, "backup_vm", vm_name, "success", f"{kind}, {done['size'] / 1024**3:.1f} GiB in {done['path']}"
        )
    except Exception as e:
        msg = str(e)
        _store().mark_failed(backup_id, msg)
        finish_task(task_id, "failed", msg)
        log_action(username, "backup_vm", vm_name, "failed", msg)
        raise
    _retention_after(vm_name, username, task_id)
    return backup_id


# What the API waits for a job hyperlite-daemon runs whole (a backup, a restore, a verification): a large disk copies
# for hours.
JOB_TIMEOUT_S = 24 * 3600


def _cancel_in_daemon(task_id, username):
    """Cancelling a backup's task stops its copy in the daemon (backup.cancel)."""
    from app.core import privileged
    from app.daemon.registry import DaemonError

    try:
        privileged.run("backup.cancel", user=username, task_id=task_id)
    except DaemonError as e:
        logger.warning("The copy of %s was not stopped: %s", task_id, e.detail)


def _retention_after(vm_name, username, task_id=None):
    """Retention is applied after each backup, in the same place for manual and scheduled backups, and on ALL the
    backups of this VM (not only those of the same job_id): a retention configured for a VM caps the total number of
    its backups. It does nothing when no backup_jobs row exists for this VM (no policy, no limit). An error in it is
    reported and the backup just made stays as it is (it used to be marked failed and deleted)."""
    try:
        job_row = _store().get_schedule(vm_name)
        if job_row:
            _apply_retention(vm_name, backup_retention.policy_of(job_row), task_id)
    except Exception as e:
        logger.exception("Retention of %s's backups failed", vm_name)
        log_action(username, "backup_retention", vm_name, "failed", str(e)[:300])


def _run_store_backup(vm_name, store_name, job_id, username):
    """A backup into a backup store (docs/design/backup-store.md): every disk type, running or stopped, only the
    chunks the store lacks written. Its task and its row exist from the start, so a store that cannot be reached is a
    failed task, audited and notified, as any failed backup. hyperlite-daemon writes the store (store.backup)."""
    from app.core import privileged
    from app.daemon.registry import DaemonError

    task_id = create_task("backup_vm", vm_name, node=None, username=username)
    conn = open_conn(readonly=True)
    try:
        try:
            mode = "live" if conn.lookupByName(vm_name).isActive() else "cold"
        except libvirt.libvirtError:
            finish_task(task_id, "failed", f"VM '{vm_name}' not found")
            raise RuntimeError(f"VM '{vm_name}' not found") from None
    finally:
        conn.close()
    backup_id = _store().create(vm_name, job_id, "", mode, _now().isoformat(), task_id, store=store_name)
    register_cancel(task_id, lambda: _cancel_in_daemon(task_id, username))
    try:
        try:
            done = privileged.run(
                "store.backup", user=username, timeout=JOB_TIMEOUT_S, vm=vm_name, store=store_name, task_id=task_id
            )
        except DaemonError as e:
            raise RuntimeError(e.detail) from None
        _store().set_path(backup_id, done["path"])
        _store().mark_done(backup_id, done["size"], None)
        finish_task(task_id, "succeeded")
        kind = "live" if done["mode"] == "live" else "offline"
        log_action(
            username,
            "backup_vm",
            vm_name,
            "success",
            f"{kind}, {done['size'] / 1024**3:.1f} GiB of disk into the store {store_name}",
        )
    except Exception as e:
        msg = str(e)
        _store().mark_failed(backup_id, msg)
        finish_task(task_id, "failed", msg)
        log_action(username, "backup_vm", vm_name, "failed", msg)
        raise
    _retention_after(vm_name, username, task_id)
    return backup_id


def copy_into_store(vm_name, store_name, task_id):
    """hyperlite-daemon's part of a backup into a store (store.backup): every disk read, only the chunks the store
    lacks written, the VM's settings among its files. {"path", "size", "mode"}."""
    from app.core import backup_store_vm, backup_stores, libvirt_utils

    with libvirt_utils.local_conn() as conn:
        try:
            domain = conn.lookupByName(vm_name)
        except libvirt.libvirtError:
            raise RuntimeError(f"VM '{vm_name}' not found") from None
        mode = "live" if domain.isActive() else "cold"
        try:
            store = backup_stores.open_store(store_name)
            with tempfile.TemporaryDirectory() as tmp:
                _write_vm_config(domain, Path(tmp))
                files = {"vm-config.json": (Path(tmp) / "vm-config.json").read_bytes()}
            directory = backup_store_vm.backup_vm(
                domain,
                store,
                mode,
                progress=lambda pct: update_task_progress(task_id, pct),
                files=files,
                manifest_extra=backup_stores.manifest_extra(store_name),
            )
        except backup_stores.StoreError as e:
            raise RuntimeError(e.message) from None
        except libvirt.libvirtError as e:
            raise RuntimeError(describe_exception(e)) from e
    manifest = json.loads((directory / "manifest.json").read_text())
    return {"path": str(directory), "size": sum(d["size"] for d in manifest["disks"].values()), "mode": mode}


def remove_backup_files(path, task_id=None):
    """Delete a backup's files through hyperlite-daemon (backup.delete): its own directory (and its VM's, once empty),
    or its index and manifest in a store, whose chunks go at the store's next garbage collection. RuntimeError when
    the daemon refused (a directory that is not a backup's, or that holds other files)."""
    from app.core import privileged
    from app.daemon.registry import DaemonError

    if not path:
        return
    try:
        privileged.run("backup.delete", path=str(path), **({"task_id": task_id} if task_id else {}))
    except DaemonError as e:
        raise RuntimeError(e.detail) from None


def checked_backup(path):
    """The backup at `path` as hyperlite-daemon reads it: (store, vm uuid, time) for a backup in a store, else the
    directory backup_dir accepts. ValueError otherwise."""
    from app.core import backup_store_vm

    found = backup_store_vm.store_backup_of(path) if path else None
    return found or backup_dir(path)


def _own_file(entry):
    return (
        entry.is_file()
        and not entry.is_symlink()
        and (entry.name in BACKUP_FILES or entry.name.endswith(BACKUP_IMAGE_SUFFIXES))
    )


def delete_backup_files(path):
    """hyperlite-daemon's part of remove_backup_files (backup.delete): a directory is deleted only when it is a
    backup's and holds nothing but a backup's files; anything else there is kept, and the deletion refused."""
    found = checked_backup(path)
    if not isinstance(found, Path):
        store, vm_uuid, stamp = found
        with contextlib.suppress(FileNotFoundError):
            store.delete_backup(vm_uuid, stamp)
        return
    if not found.is_dir() or found.is_symlink():
        return
    strange = sorted(e.name for e in found.iterdir() if not _own_file(e))
    if strange:
        raise ValueError(f"{found} holds files a backup does not ({', '.join(strange[:5])}): nothing was deleted there")
    shutil.rmtree(found)
    with contextlib.suppress(OSError):  # <target>/<vm>/<time>: the VM's directory goes with its last backup
        found.parent.rmdir()


def _backup_images(src_dir):
    """[(target dev, image Path)] of a backup's disks, in the VM's disk order. The manifest records the target of
    each image; a backup made before manifests names its images after their target (vda.qcow2)."""
    manifest = backup_integrity.read_manifest(src_dir)
    if manifest:
        return [
            (e["target"], Path(src_dir) / Path(str(e["name"])).name)
            for e in manifest.get("files") or []
            if e.get("role") == "disk" and e.get("target")
        ]
    return [(p.stem, p) for p in sorted(Path(src_dir).glob("*.qcow2"))]


def _disk_formats(domain):
    """{target dev: driver type ('qcow2', 'raw'...)} of a domain's file disks."""
    root = ET.fromstring(domain.XMLDesc(0))
    formats = {}
    for disk in root.findall(".//devices/disk"):
        target, driver = disk.find("target"), disk.find("driver")
        if disk.get("device") == "disk" and target is not None:
            formats[target.get("dev")] = (driver.get("type") if driver is not None else None) or "raw"
    return formats


def _convert(source, dest, fmt, task_id, base_pct, span_pct):
    """qemu-img convert to `fmt`: a backup image is always qcow2, and writing it as is into a raw disk would leave
    the VM with a disk it reads as garbage."""
    try:
        # A backup may come from another site's share: it names no file outside its VM's points.
        source_fmt = disk_images.check(source, within=Path(source).parent.parent)
    except disk_images.UnsafeImage as e:
        raise RuntimeError(str(e)) from None
    proc = subprocess.run(
        ["qemu-img", "convert", "-f", source_fmt, "-O", fmt, str(source), str(dest)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"qemu-img convert failed: {(proc.stderr or proc.stdout).strip()[:400]}")
    update_task_progress(task_id, int(base_pct + span_pct))


def _copy_owner_and_mode(reference, path):
    try:
        st = os.stat(reference)
    except FileNotFoundError:
        return
    os.chmod(path, st.st_mode & 0o7777)
    try:
        os.chown(path, st.st_uid, st.st_gid)
    except PermissionError:
        logger.warning("Could not give %s the owner of %s", path, reference)


def _store():
    # The backup repository's synchronous bridge: backups, restores and the scheduler run in threads.
    from app.repositories import registry

    return registry.backups().sync


def _check_before_restore(row, task_id, username):
    """Verify the backup NOW, whatever an earlier verification said: a restore replaces good data, so it must not
    start from a backup whose files changed or are damaged. The result is recorded like a manual verification."""
    status, problems = _verify_in_daemon(row, task_id, username, span=20)
    _store().set_verification(row["id"], status, _now().isoformat(), "; ".join(problems) or None)
    if status != backup_integrity.VERIFIED:
        raise RuntimeError("The backup failed its integrity check, nothing was restored: " + "; ".join(problems[:5]))


def _ensure_room_beside(existing, by_dev):
    """Each restored image is written next to the disk it replaces, the old disk kept until the swap: every file
    system holding a disk needs room for the images of its disks (disk_space.ensure_room)."""
    needed = {}  # file system -> [a directory on it, bytes]
    for dev, dest in existing:
        folder = Path(dest).parent
        entry = needed.setdefault(os.stat(folder).st_dev, [folder, 0])
        entry[1] += _allocated_bytes(by_dev[dev])
    for folder, size in needed.values():
        disk_space.ensure_room(folder, size, "this restore")


def _restore_overwrite(conn, domain, images, task_id):
    """Replace the disks of a stopped VM with the backup images, all or nothing.

    Every image is first written next to the disk it replaces, under a temporary name; only when all of them are
    complete are the disks swapped by renames (atomic on one file system), the previous disks being kept until the
    last swap succeeded. An interruption at any point leaves either the old disks or the new ones, never a
    half-written disk."""
    existing = domain_disk_paths(domain)
    by_dev = dict(images)
    missing = [dev for dev, _ in existing if dev not in by_dev]
    extra = [dev for dev in by_dev if dev not in {d for d, _ in existing}]
    if missing or extra:
        raise RuntimeError(
            f"The VM's disks ({', '.join(d for d, _ in existing) or 'none'}) no longer match the backup's "
            f"({', '.join(by_dev)}): nothing was restored. Restore to a new VM instead."
        )
    _ensure_room_beside(existing, by_dev)
    formats = _disk_formats(domain)
    token = uuid.uuid4().hex[:8]
    staged = []  # (dest, temp)
    try:
        span = 65 / len(existing)
        for i, (dev, dest) in enumerate(existing):
            temp = Path(dest).with_name(f".{Path(dest).name}.restore-{token}")
            staged.append((Path(dest), temp))
            _convert(by_dev[dev], temp, formats.get(dev, "qcow2"), task_id, 20 + i * span, span)
            _copy_owner_and_mode(dest, temp)
            with open(temp, "rb") as f:
                os.fsync(f.fileno())
    except BaseException:
        for _dest, temp in staged:
            temp.unlink(missing_ok=True)
        raise

    # The restored disks replace the ones the replication bitmap described. Dropped only now: a restore refused or
    # stopped before this point leaves the VM's replication as it was (it used to restart from a full copy).
    try:
        dropped = checkpoints.drop(domain)
    except BaseException:
        for _dest, temp in staged:
            temp.unlink(missing_ok=True)
        raise
    return _swap_staged(staged, task_id), dropped


def _swap_staged(staged, task_id):
    """Put each staged image (dest, temp) in place of its disk by renames, the previous disks kept until the last
    swap succeeded and put back if one fails."""
    swapped = []  # (dest, previous)
    try:
        for dest, temp in staged:
            previous = dest.with_name(temp.name.replace(".restore-", ".pre-restore-", 1))
            if dest.exists():
                os.replace(dest, previous)
                swapped.append((dest, previous))
            else:
                swapped.append((dest, None))
            os.replace(temp, dest)
    except BaseException:
        for dest, previous in reversed(swapped):
            if previous is not None:
                os.replace(previous, dest)
        for _dest, temp in staged:
            temp.unlink(missing_ok=True)
        raise
    for _dest, previous in swapped:
        if previous is not None:
            previous.unlink(missing_ok=True)
    update_task_progress(task_id, 90)
    return [str(dest) for dest, _ in staged]


def _restore_overwrite_from_store(domain, found, devs, task_id):
    """Replace the disks of a stopped VM with a backup in a store. A file disk is written next to itself under a
    temporary name and swapped in once every one is complete, as _restore_overwrite does; a block device (a zvol, an
    LVM volume, a LUN) cannot be swapped and is written in place, after every file disk was staged, as Proxmox
    overwrites a zvol."""
    from app.core import backup_store_vm

    store, vm_uuid, stamp = found
    disks = {d.dev: d for d in backup_store_vm.disks_of(domain)}
    if set(disks) != set(devs):
        raise RuntimeError(
            f"The VM's disks ({', '.join(disks) or 'none'}) no longer match the backup's ({', '.join(devs)}): nothing "
            "was restored. Restore to a new VM instead."
        )
    sizes = {dev: store.index_of(vm_uuid, stamp, dev).size for dev in devs}
    files = [disks[d] for d in devs if not disks[d].block]
    blocks = [disks[d] for d in devs if disks[d].block]
    needed = {}
    for disk in files:
        folder = Path(disk.source).parent
        needed.setdefault(os.stat(folder).st_dev, [folder, 0])[1] += sizes[disk.dev]
    for folder, size in needed.values():
        disk_space.ensure_room(folder, size, "this restore")
    token = uuid.uuid4().hex[:8]
    staged, span = [], 65 / len(devs)
    try:
        for i, disk in enumerate(files):
            dest = Path(disk.source)
            temp = dest.with_name(f".{dest.name}.restore-{token}")
            staged.append((dest, temp))
            backup_store_vm.restore_disk(store, vm_uuid, stamp, disk.dev, temp, disk.format)
            update_task_progress(task_id, int(20 + (i + 1) * span))
            _copy_owner_and_mode(dest, temp)
            with open(temp, "rb") as f:
                os.fsync(f.fileno())
        dropped = checkpoints.drop(domain)  # the disks no longer hold what the replication bitmap described
        for i, disk in enumerate(blocks):
            backup_store_vm.restore_disk(store, vm_uuid, stamp, disk.dev, disk.source)
            update_task_progress(task_id, int(20 + (len(files) + i + 1) * span))
    except BaseException:
        for _dest, temp in staged:
            temp.unlink(missing_ok=True)
        raise
    return [*_swap_staged(staged, task_id), *(d.source for d in blocks)], dropped


def restore_backup(backup_id, mode, new_name=None, username="system", claim=None, network=None):
    """See _restore_backup; the backup is marked as being read meanwhile, so it cannot be deleted under the restore."""
    with backup_use.reading(backup_id, "a restore"):
        return _restore_backup(backup_id, mode, new_name, username, claim, network)


def _restore_backup(backup_id, mode, new_name=None, username="system", claim=None, network=None):
    """mode='overwrite': replace the disks of the original VM (it must be stopped). mode='new': define a new VM from
    the backup, with a new UUID/MAC (the same logic as cloning). `network` (mode 'new' only) replaces the network
    recorded in the backup: a backup made on another site may name a network this node does not have.

    `claim`: the vm_locks claim the endpoint took on the VM, released here when the restore ends; without one, the
    restore takes its own."""
    row = _store().get(backup_id)
    if not row:
        raise RuntimeError("Backup not found")
    if row["status"] != "succeeded":
        raise RuntimeError("This backup is not in a restorable state (failed or in progress)")
    if mode not in ("overwrite", "new"):
        raise RuntimeError("invalid mode (expected 'overwrite' or 'new')")
    target_name = row["vm_name"] if mode == "overwrite" else new_name
    if claim is None:
        claim = vm_locks.claim(target_name or "", "a backup restore")

    with claim:
        task_id = create_task("restore_backup", target_name, node=None, username=username)
        try:
            ask = {
                "path": row["path"] or "",
                "store": bool(row.get("store")),
                "source_vm": row["vm_name"],
                "mode": mode,
                "vm": target_name or "",
                "network": network,
                "task_id": task_id,
            }
            _restore_in_daemon(username, check_only=True, **ask)  # the VM, the name, the network: before reading
            _check_before_restore(row, task_id, username)
            done = _restore_in_daemon(username, **ask)
            if done.get("checkpoints_dropped"):
                # The restored disks no longer hold what the replication bitmap described: its chain starts again.
                checkpoints.forget(target_name, done["checkpoints_dropped"])
            if mode == "new":
                # Older backups did not record them: taken from the original VM when it still has them.
                os_label = done.get("os_label") or get_vm_os_label(row["vm_name"])
                ssh_user = done.get("ssh_user") or get_vm_ssh_user(row["vm_name"])
                if os_label:
                    set_vm_os_label(new_name, os_label)
                if ssh_user:
                    set_vm_ssh_user(new_name, ssh_user)
            finish_task(task_id, "succeeded")
            restored = done.get("restored") or []
            what = "overwrite" if mode == "overwrite" else "new VM"
            detail = f"{what} from backup #{backup_id}" + (f", with {' and '.join(restored)}" if restored else "")
            log_action(username, "restore_backup", target_name, "success", detail)
            return {"vm": target_name, "mode": mode}
        except Exception as e:
            msg = str(e)
            finish_task(task_id, "failed", msg)
            log_action(username, "restore_backup", target_name or row["vm_name"], "failed", msg)
            raise


def _restore_in_daemon(username, **ask):
    from app.core import privileged
    from app.daemon.registry import DaemonError

    try:
        return privileged.run("backup.restore", user=username, timeout=JOB_TIMEOUT_S, **ask)
    except DaemonError as e:
        raise RuntimeError(e.detail) from None


def restore_files(path, store, source_vm, mode, vm, network, task_id, check_only=False):
    """hyperlite-daemon's part of a restore (backup.restore), on a backup checked_backup accepts: over the stopped VM
    `vm`, into the disks its own definition names, or as the new VM `vm` in the images directory, its definition
    built from checked fields. With `check_only`, only what is refused before the backup is read: the VM missing or
    running, the new name taken or invalid, the network missing. Returns {"restored", "os_label", "ssh_user"}."""
    from app.core import backup_store_vm, libvirt_utils
    from app.core.vm_builder import IMAGES_DIR as _IMAGES_DIR
    from app.core.vm_builder import build_domain_xml, validate_name

    found = checked_backup(path)
    found = None if isinstance(found, Path) else found
    if store and not found:
        raise RuntimeError("This backup's store cannot be read")
    src_dir = Path(path)
    new_disk_paths = []
    with libvirt_utils.local_conn() as conn, contextlib.ExitStack() as stack:
        try:
            if mode == "overwrite":
                try:
                    domain = conn.lookupByName(vm)
                except libvirt.libvirtError:
                    raise RuntimeError(f"Original VM '{vm}' not found: use the restore to a new location") from None
                if domain.isActive():
                    raise RuntimeError("Stop the VM before restoring over it")
                if check_only:
                    return {}
            else:
                err = validate_name(vm) if vm else "new_name is required for a restore to a new location"
                if err:
                    raise RuntimeError(err)
                try:
                    conn.lookupByName(vm)
                    raise RuntimeError(f"A VM '{vm}' already exists")
                except libvirt.libvirtError:
                    pass

            # A backup in a store (docs/design/backup-store.md): its disks are indexes of chunks, written by
            # backup_store_vm.restore_disk; its vm-config.json, NVRAM and TPM state are files of its directory,
            # decrypted into a temporary one when the store is encrypted (files_dir).
            files_dir = src_dir
            if found:
                manifest = json.loads((src_dir / "manifest.json").read_text())
                images = [(dev, None) for dev in manifest["disks"]]
                files_dir = stack.enter_context(backup_store_vm.files_of(found))
            else:
                images = _backup_images(src_dir)
            if not images:
                raise RuntimeError("No disk file found in this backup")

            if mode == "overwrite":
                if found:
                    written, dropped = _restore_overwrite_from_store(domain, found, [dev for dev, _ in images], task_id)
                else:
                    written, dropped = _restore_overwrite(conn, domain, images, task_id)
                refresh_pools_for_paths(conn, written)
                return {
                    "restored": backup_integrity.restore_firmware_state(files_dir, domain),
                    "checkpoints_dropped": dropped,
                }

            # Checked before any copy: a missing network would only fail at the first start.
            config = _read_vm_config(files_dir)
            if network:
                config["network"] = network
            try:
                conn.networkLookupByName(config["network"])
            except libvirt.libvirtError:
                raise RuntimeError(
                    f"The network '{config['network']}' recorded in the backup does not exist on this node: "
                    "choose another network"
                ) from None
            if check_only:
                return {}
            if found:
                store_, vm_uuid, stamp = found
                needed = sum(store_.index_of(vm_uuid, stamp, dev).size for dev, _ in images)
            else:
                needed = sum(_allocated_bytes(src) for _dev, src in images)
            disk_space.ensure_room(_IMAGES_DIR, needed, "this restore")

            span = 60 / len(images)
            for i, (_dev, src) in enumerate(images):
                suffix = "" if i == 0 else f"-{i + 1}"
                dest = safe_child(_IMAGES_DIR, f"{vm}{suffix}.qcow2")
                if dest.exists():
                    raise RuntimeError(f"A disk file '{dest.name}' already exists")
                new_disk_paths.append(dest)
                if found:
                    backup_store_vm.restore_disk(store_, vm_uuid, stamp, _dev, dest, "qcow2")
                    update_task_progress(task_id, int(20 + (i + 1) * span))
                else:
                    _convert(src, dest, "qcow2", task_id, 20 + i * span, span)

            seed = _reseed_iso(config, source_vm, vm)
            if seed:
                new_disk_paths.append(seed)
            xml = build_domain_xml(
                vm,
                int(config["vcpu"]),
                int(config["memory_mb"]),
                new_disk_paths[: len(images)],
                seed,
                config["network"],
                firmware=config["firmware"],
            )
            new_domain = conn.defineXML(xml)
            refresh_pools_for_paths(conn, new_disk_paths)
            return {
                "restored": backup_integrity.restore_firmware_state(files_dir, new_domain),
                "os_label": config.get("os_label"),
                "ssh_user": config.get("ssh_user"),
            }
        except Exception as e:
            if mode == "new" and not check_only:
                # Only the files this restore created: the name check above refused existing ones.
                for created in new_disk_paths:
                    Path(created).unlink(missing_ok=True)
            if isinstance(e, libvirt.libvirtError):
                raise RuntimeError(describe_exception(e)) from e
            raise


def verify_backup(backup_id, username="system", task_id=None):
    """See _verify_backup; the backup is marked as being read meanwhile."""
    with backup_use.reading(backup_id, "a verification"):
        return _verify_backup(backup_id, username, task_id)


def _verify_backup(backup_id, username="system", task_id=None):
    """Check one backup (checksums of every file, qemu-img check of the images) and record the result. Returns
    {"verification", "problems"}. A corrupted backup is audited and notified. hyperlite-daemon reads the backup
    (backup.verify)."""
    row = _store().get(backup_id)
    if not row:
        raise RuntimeError("Backup not found")
    if row["status"] != "succeeded":
        raise RuntimeError("Only a finished backup can be verified")
    own_task = task_id is None
    if own_task:
        task_id = create_task("verify_backup", row["vm_name"], username=username)
    try:
        status, problems = _verify_in_daemon(row, task_id, username)
    except Exception as e:
        if own_task:
            finish_task(task_id, "failed", str(e))
        raise
    _store().set_verification(backup_id, status, _now().isoformat(), "; ".join(problems) or None)
    if own_task:
        finish_task(task_id, "succeeded")
    if status == backup_integrity.CORRUPT:
        detail = f"backup #{backup_id}: {'; '.join(problems)}"[:1000]
        log_action(username, "verify_backup", row["vm_name"], "failed", detail)
        from app.core.notifications import notify

        notify("verify_backup", f"Corrupted backup of {row['vm_name']}", detail, result="failed")
    else:
        log_action(username, "verify_backup", row["vm_name"], "success", f"backup #{backup_id}")
    return {"verification": status, "problems": problems}


def _verify_in_daemon(row, task_id, username, span=100):
    """(status, problems) of backup_integrity.verify, run by hyperlite-daemon (backup.verify), its progress into the
    task up to `span` percent."""
    from app.core import privileged
    from app.daemon.registry import DaemonError

    try:
        found = privileged.run(
            "backup.verify",
            user=username,
            timeout=JOB_TIMEOUT_S,
            path=row["path"] or "",
            checksum=row["checksum_sha256"],
            task_id=task_id,
            span=span,
        )
    except DaemonError as e:
        raise RuntimeError(e.detail) from None
    return found["status"], found["problems"]


def verify_files(path, checksum, task_id, base=0, span=100):
    """hyperlite-daemon's part of a verification (backup.verify): backup_integrity.verify on a backup checked_backup
    accepts. {"status", "problems"}."""
    checked_backup(path)
    status, problems, _count = backup_integrity.verify(
        path, checksum, progress=lambda pct: update_task_progress(task_id, int(base + pct * span / 100))
    )
    return {"status": status, "problems": problems}


def _verify_due_backup():
    """The weekly verification: at most one backup per scheduler tick (they can be large), never while a backup
    runs, the one verified longest ago (or never) first."""
    limit = (_now() - timedelta(days=backup_integrity.VERIFY_EVERY_DAYS)).isoformat()
    backup_id = _store().next_to_verify(limit)
    if backup_id is None or not _backup_lock.acquire(blocking=False):
        return None
    try:
        return verify_backup(backup_id, username="scheduler")
    finally:
        _backup_lock.release()


def _apply_retention(vm_name, policy, task_id=None):
    """Per VM, not per job_id (see the comment in run_backup): the policy (app/core/backup_retention.py) applies
    to ALL the finished backups of the VM, whether they come from a schedule or from a manual trigger. `task_id`: the
    backup's own task, which lets its account delete what the retention removes (backup.delete's needs)."""
    rows = _store().finished_of(vm_name)

    def kept(selection):
        return backup_retention.kept(
            [(r["id"], r["created_at"]) for r in selection],
            policy["last"],
            policy["daily"],
            policy["weekly"],
            policy["monthly"],
        )

    # A backup found corrupted never takes the place of a good one: the policy picks among the good ones, so newer
    # corrupted copies cannot push the last good one out. Corrupted ones are kept only where the policy over all of
    # them would keep them (to be looked at), and removed beyond.
    corrupted = {r["id"] for r in rows if r["verification"] == "corrupted"}
    keep = kept([r for r in rows if r["id"] not in corrupted]) | (kept(rows) & corrupted)
    for row in rows:
        if row["id"] not in keep:
            if backup_use.holder(row["id"]):
                continue  # being restored or read: the next run of the schedule removes it
            try:
                remove_backup_files(row["path"], task_id=task_id)
            except RuntimeError as e:
                # Its files are not only a backup's: kept with its record, the others still go.
                logger.warning("Retention kept backup #%s of %s: %s", row["id"], vm_name, e)
                log_action("system", "backup_retention", vm_name, "failed", str(e)[:300])
                continue
            _store().delete(row["id"])


def _next_run(frequency, time, from_time=None):
    base = from_time or _now()
    hh, mm = (int(x) for x in time.split(":"))
    candidate = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if candidate <= base:
        candidate += timedelta(days=1)
    if frequency == "weekly":
        while candidate.weekday() != 0:  # execute le lundi
            candidate += timedelta(days=1)
    elif frequency == "monthly":
        candidate = candidate.replace(day=1)
        if candidate <= base:
            month = candidate.month % 12 + 1
            year = candidate.year + (1 if candidate.month == 12 else 0)
            candidate = candidate.replace(year=year, month=month)
    return candidate


def _hosted_vms():
    conn = open_conn(readonly=True)
    try:
        return {d.name() for d in conn.listAllDomains()}
    finally:
        conn.close()


def run_due_schedules(now):
    """The per-VM jobs whose time has come."""
    hosted = _hosted_vms() if cluster_lead.in_cluster() else None
    for job in _store().due_schedules(now.isoformat()):
        if hosted is not None and job["vm_name"] not in hosted:
            continue  # in a cluster, the node hosting the VM runs its job, and moves its next run for all
        try:
            # Retention is now applied INSIDE run_backup() itself (see its body), which also
            # covers the manual backups of this VM, not only those of the scheduler.
            run_backup(
                job["vm_name"], job["target_dir"], job_id=job["id"], username="scheduler", store=job.get("store")
            )
        except vm_locks.VmBusy:
            continue  # another operation runs on this VM: the job stays due and is retried next tick
        except Exception as e:
            log_action("scheduler", "backup_job_failed", job["vm_name"], "failed", str(e))
        next_run = _next_run(job["frequency"], job["time"], now)
        _store().record_schedule_run(job["id"], now.isoformat(), next_run.isoformat())


def _scheduler_loop():
    while True:
        try:
            if cluster_lead.paused("Scheduled backups"):
                time.sleep(SCHEDULER_INTERVAL_S)
                continue
            now = _now()
            run_due_schedules(now)
            from app.core import backup_groups

            backup_groups.run_due(now)
            _verify_due_backup()
            from app.core import backup_stores

            backup_stores.collect_due()
            from app.core import backup_store_sync

            backup_store_sync.run_due(now)
            db_health.succeeded("backup-scheduler")
        except Exception as e:
            logger.exception("Backup scheduler tick failed")
            db_health.failed("backup-scheduler", e)
        time.sleep(SCHEDULER_INTERVAL_S)


def start_backup_scheduler():
    thread = threading.Thread(target=_scheduler_loop, daemon=True)
    thread.start()
    return thread


def import_into_store(backup_id, store_name, username="system", task_id=None):
    """Copy a backup of today's format into a backup store (docs/design/backup-store.md, step 4): a new entry of the
    catalogue, the backup itself left as it is (removing it stays a choice of its own). Returns the new entry's id.
    hyperlite-daemon reads the backup and writes the store (store.import)."""
    from app.core import privileged
    from app.daemon.registry import DaemonError

    row = _store().get(backup_id)
    if not row:
        raise RuntimeError("Backup not found")
    if row.get("store"):
        raise RuntimeError("This backup is already in a backup store")
    if row["status"] != "succeeded":
        raise RuntimeError("Only a finished backup can be imported")
    own_task = task_id is None
    if own_task:
        task_id = create_task("import_backup_store", row["vm_name"], node=None, username=username)
    with backup_use.reading(backup_id, "an import into a backup store"):
        try:
            try:
                done = privileged.run(
                    "store.import",
                    user=username,
                    timeout=JOB_TIMEOUT_S,
                    path=row["path"] or "",
                    created_at=row["created_at"],
                    vm=row["vm_name"],
                    vm_uuid=_vm_uuid(row["vm_name"]),
                    store=store_name,
                    task_id=task_id,
                )
            except DaemonError as e:
                raise RuntimeError(e.detail) from None
            existing = _store().find_by_path(done["path"])
            new_id = existing["id"] if existing else _store().create(
                row["vm_name"], row["job_id"], done["path"], row["mode"], row["created_at"], task_id, store=store_name
            )  # fmt: skip
            if not existing:
                _store().mark_done(new_id, done["size"], None)
        except Exception as e:
            msg = getattr(e, "message", None) or str(e)
            if own_task:
                finish_task(task_id, "failed", msg)
            log_action(username, "import_backup_store", row["vm_name"], "failed", f"#{backup_id}: {msg}"[:300])
            raise
    if own_task:
        finish_task(task_id, "succeeded")
    log_action(
        username, "import_backup_store", row["vm_name"], "success", f"#{backup_id} into {store_name} as #{new_id}"
    )
    return new_id


def import_here(path, created_at, vm_name, vm_uuid, store_name, task_id):
    """hyperlite-daemon's part of import_into_store (store.import): a directory backup backup_dir accepts, copied
    into the store. {"path", "size"}."""
    from app.core import backup_store_vm, backup_stores

    backup_dir(path)
    try:
        store = backup_stores.open_store(store_name)
    except backup_stores.StoreError as e:
        raise RuntimeError(e.message) from None
    directory = backup_store_vm.import_directory_backup(
        path, created_at, vm_name, store, vm_uuid, progress=lambda pct: update_task_progress(task_id, pct)
    )
    manifest = json.loads((directory / "manifest.json").read_text())
    return {"path": str(directory), "size": sum(d["size"] for d in manifest["disks"].values())}


def _vm_uuid(vm_name):
    """The VM's UUID when it is defined here, else one derived from its name: a store keeps a VM's backups together
    by its UUID."""
    try:
        conn = open_conn(readonly=True)
        try:
            return conn.lookupByName(vm_name).UUIDString()
        finally:
            conn.close()
    except libvirt.libvirtError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"hyperlite-vm:{vm_name}"))
