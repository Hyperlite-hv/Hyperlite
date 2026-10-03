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
    checkpoints,
    cluster_lead,
    firmware,
    guest_agent,
    replication,
    vm_locks,
)
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import open_conn, refresh_pools_for_paths
from app.core.safe_paths import safe_child
from app.core.tasks import create_task, finish_task, raise_if_cancelled, register_cancel, update_task_progress
from app.core.vm_builder import IMAGES_DIR

logger = logging.getLogger(__name__)

DEFAULT_BACKUP_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "backups"
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
    """Target names (sda, vdb...) of a domain's disks that are block devices: ZFS zvols and iSCSI LUNs."""
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
        f"{action} is not available yet for a VM with ZFS or iSCSI disks ({', '.join(devs)}): "
        "only disk files can be copied for now"
    )


def refuse_vm_with_block_disks(name, action):
    """block_disk_error by VM name, as a 422 answered at once (the backup or export itself runs in the background)."""
    from fastapi import HTTPException

    conn = open_conn()
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
            log_action("system", "backup_commit_warning", vm_name, "echec", f"{dev}: {msg}")
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
    }
    (dest_dir / "vm-config.json").write_text(json.dumps(config))


def _read_vm_config(src_dir):
    """Settings recorded at backup time; older backups fall back to the historical defaults."""
    config = {"vcpu": 1, "memory_mb": 1024, "network": "default", "firmware": "bios"}
    with contextlib.suppress(OSError, ValueError):
        config.update(json.loads((Path(src_dir) / "vm-config.json").read_text()))
    return config


def run_backup(vm_name, target_dir=None, job_id=None, username="system", claim=None):
    """Run a backup (choosing hot or cold according to the real state of the VM) and
    return the id of the `backups` row created. Synchronous: called from a
    thread by the endpoint (manual backup) or by the scheduler.

    _backup_lock: only one backup at a time on the WHOLE server (all VMs
    combined), see the comment above _backup_lock. A concurrent caller simply
    waits its turn instead of failing.

    `claim`: the vm_locks claim the endpoint took on the VM; without one the backup takes its own (VmBusy when
    another operation is running on the VM). Released when the backup ends."""
    if claim is None:
        claim = vm_locks.claim(vm_name, "a backup")
    with claim, _backup_lock:
        return _run_backup_locked(vm_name, target_dir, job_id, username)


def _run_backup_locked(vm_name, target_dir, job_id, username):
    target_root = Path(target_dir) if target_dir else DEFAULT_BACKUP_DIR
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(vm_name)
        except libvirt.libvirtError:
            raise RuntimeError(f"VM '{vm_name}' not found") from None
        error = block_disk_error(domain, "A backup")
        if error:
            raise RuntimeError(error)

        mode = "chaud" if domain.isActive() else "froid"
        stamp = _now().strftime("%Y%m%dT%H%M%SZ")
        dest_dir = target_root / vm_name / stamp
        dest_dir.mkdir(parents=True, exist_ok=True)
        _write_vm_config(domain, dest_dir)
        backup_integrity.save_firmware_state(domain, dest_dir)
        firmware_kind = firmware.of_domain(ET.fromstring(domain.XMLDesc(0)))

        task_id = create_task("backup_vm", vm_name, node=None, username=username)
        backup_id = _store().create(vm_name, job_id, str(dest_dir), mode, _now().isoformat(), task_id)

        try:
            if mode == "froid":
                dest_paths = backup_cold(domain, vm_name, dest_dir, task_id)
            else:
                dest_paths = backup_hot(conn, domain, vm_name, dest_dir, task_id)

            update_task_progress(task_id, 95)
            devs = [dev for dev, _source in domain_disk_paths(domain)]
            manifest = backup_integrity.write_manifest(
                dest_dir, vm_name, mode, firmware_kind, list(zip(devs, dest_paths, strict=False))
            )
            total_size = sum(f["taille"] for f in manifest["fichiers"])
            disk_sums = [f["sha256"] for f in manifest["fichiers"] if f["role"] == "disque"]
            # Kept for the backups listed before manifests existed and for the API's single-disk field.
            checksum = disk_sums[0] if len(disk_sums) == 1 else None
            _store().mark_done(backup_id, total_size, checksum)
            finish_task(task_id, "termine")
            log_action(username, "backup_vm", vm_name, "succes", f"{mode}, {total_size} octets -> {dest_dir}")
            # Retention (retention_count, part of the schema) is applied HERE, in the same
            # place for manual and scheduled backups, and on ALL the backups of this VM (not
            # only those of the same job_id): a retention_count configured for a VM must cap
            # the total number of its backups, not just those coming from one specific job.
            # It does nothing when no backup_jobs row exists for this VM (no configured
            # policy means no imposed limit, so a 100% manual usage without scheduling is
            # unchanged).
            job_row = _store().get_schedule(vm_name)
            if job_row:
                _apply_retention(vm_name, backup_retention.policy_of(job_row))
        except Exception as e:
            msg = describe_exception(e) if isinstance(e, libvirt.libvirtError) else str(e)
            _store().mark_failed(backup_id, msg)
            finish_task(task_id, "echec", msg)
            log_action(username, "backup_vm", vm_name, "echec", msg)
            shutil.rmtree(dest_dir, ignore_errors=True)
            raise
        return backup_id
    finally:
        conn.close()


def _backup_images(src_dir):
    """[(target dev, image Path)] of a backup's disks, in the VM's disk order. The manifest records the target of
    each image; a backup made before manifests names its images after their target (vda.qcow2)."""
    manifest = backup_integrity.read_manifest(src_dir)
    if manifest:
        return [
            (e["cible"], Path(src_dir) / Path(str(e["nom"])).name)
            for e in manifest.get("fichiers") or []
            if e.get("role") == "disque" and e.get("cible")
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
    proc = subprocess.run(
        ["qemu-img", "convert", "-O", fmt, str(source), str(dest)], capture_output=True, text=True, check=False
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
    status, problems, _count = backup_integrity.verify(
        row["chemin"], row["checksum_sha256"], progress=lambda pct: update_task_progress(task_id, int(pct * 0.2))
    )
    _store().set_verification(row["id"], status, _now().isoformat(), "; ".join(problems) or None)
    if status != backup_integrity.VERIFIED:
        raise RuntimeError("The backup failed its integrity check, nothing was restored: " + "; ".join(problems[:5]))


def _restore_overwrite(conn, domain, images, task_id):
    """Replace the disks of a stopped VM with the backup images, all or nothing.

    Every image is first written next to the disk it replaces, under a temporary name; only when all of them are
    complete are the disks swapped by renames (atomic on one file system), the previous disks being kept until the
    last swap succeeded. An interruption at any point leaves either the old disks or the new ones, never a
    half-written disk."""
    existing = domain_disk_paths(domain)
    by_dev = dict(images)
    # The restored disks replace the ones the replication bitmap described.
    checkpoints.release(domain)
    missing = [dev for dev, _ in existing if dev not in by_dev]
    extra = [dev for dev in by_dev if dev not in {d for d, _ in existing}]
    if missing or extra:
        raise RuntimeError(
            f"The VM's disks ({', '.join(d for d, _ in existing) or 'none'}) no longer match the backup's "
            f"({', '.join(by_dev)}): nothing was restored. Restore to a new VM instead."
        )
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

    swapped = []  # (dest, previous)
    try:
        for dest, temp in staged:
            previous = dest.with_name(f".{dest.name}.pre-restore-{token}")
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


def restore_backup(backup_id, mode, new_name=None, username="system", claim=None, network=None):
    """mode='overwrite': replace the disks of the original VM (it must be stopped). mode='new': define a new VM from
    the backup, with a new UUID/MAC (the same logic as cloning). `network` (mode 'new' only) replaces the network
    recorded in the backup: a backup made on another site may name a network this node does not have.

    `claim`: the vm_locks claim the endpoint took on the VM, released here when the restore ends; without one, the
    restore takes its own."""
    row = _store().get(backup_id)
    if not row:
        raise RuntimeError("Backup not found")
    if row["statut"] != "termine":
        raise RuntimeError("This backup is not in a restorable state (failed or in progress)")
    if mode not in ("overwrite", "new"):
        raise RuntimeError("invalid mode (expected 'overwrite' or 'new')")
    target_name = row["vm_name"] if mode == "overwrite" else new_name
    if claim is None:
        claim = vm_locks.claim(target_name or "", "a backup restore")

    with claim:
        src_dir = Path(row["chemin"])
        conn = open_conn()
        task_id = create_task("restore_backup", row["vm_name"], node=None, username=username)
        new_disk_paths = []
        try:
            images = _backup_images(src_dir)
            if not images:
                raise RuntimeError("No disk file found in this backup")
            if mode == "overwrite":
                try:
                    domain = conn.lookupByName(target_name)
                except libvirt.libvirtError:
                    raise RuntimeError(
                        f"Original VM '{target_name}' not found: use the restore to a new location"
                    ) from None
                if domain.isActive():
                    raise RuntimeError("Stop the VM before restoring over it")
                _check_before_restore(row, task_id, username)
                written = _restore_overwrite(conn, domain, images, task_id)
                refresh_pools_for_paths(conn, written)
                restored = backup_integrity.restore_firmware_state(src_dir, domain)
                finish_task(task_id, "termine")
                detail = f"overwrite from backup #{backup_id}" + (
                    f", with {' and '.join(restored)}" if restored else ""
                )
                log_action(username, "restore_backup", target_name, "succes", detail)
                return {"vm": target_name, "mode": "overwrite"}

            from app.core.vm_builder import IMAGES_DIR as _IMAGES_DIR
            from app.core.vm_builder import build_domain_xml, validate_name

            if not new_name:
                raise RuntimeError("new_name is required for a restore to a new location")
            err = validate_name(new_name)
            if err:
                raise RuntimeError(err)
            try:
                conn.lookupByName(new_name)
                raise RuntimeError(f"A VM '{new_name}' already exists")
            except libvirt.libvirtError:
                pass
            # Checked before any copy: a missing network would only fail at the first start.
            config = _read_vm_config(src_dir)
            if network:
                config["network"] = network
            try:
                conn.networkLookupByName(config["network"])
            except libvirt.libvirtError:
                raise RuntimeError(
                    f"The network '{config['network']}' recorded in the backup does not exist on this node: "
                    "choose another network"
                ) from None
            _check_before_restore(row, task_id, username)

            span = 60 / len(images)
            for i, (_dev, src) in enumerate(images):
                suffix = "" if i == 0 else f"-{i + 1}"
                dest = safe_child(_IMAGES_DIR, f"{new_name}{suffix}.qcow2")
                if dest.exists():
                    raise RuntimeError(f"A disk file '{dest.name}' already exists")
                new_disk_paths.append(dest)
                _convert(src, dest, "qcow2", task_id, 20 + i * span, span)

            xml = build_domain_xml(
                new_name,
                config["vcpu"],
                config["memory_mb"],
                new_disk_paths,
                None,
                config["network"],
                firmware=config["firmware"],
            )
            new_domain = conn.defineXML(xml)
            refresh_pools_for_paths(conn, new_disk_paths)
            restored = backup_integrity.restore_firmware_state(src_dir, new_domain)
            finish_task(task_id, "termine")
            detail = f"new VM from backup #{backup_id}" + (f", with {' and '.join(restored)}" if restored else "")
            log_action(username, "restore_backup", new_name, "succes", detail)
            return {"vm": new_name, "mode": "new"}
        except Exception as e:
            msg = describe_exception(e) if isinstance(e, libvirt.libvirtError) else str(e)
            if mode == "new":
                # Only the files this restore created: the name check above refused existing ones.
                for path in new_disk_paths:
                    Path(path).unlink(missing_ok=True)
            finish_task(task_id, "echec", msg)
            log_action(username, "restore_backup", target_name or row["vm_name"], "echec", msg)
            raise
        finally:
            conn.close()


def verify_backup(backup_id, username="system", task_id=None):
    """Check one backup (checksums of every file, qemu-img check of the images) and record the result. Returns
    {"verification", "problemes"}. A corrupted backup is audited and notified."""
    row = _store().get(backup_id)
    if not row:
        raise RuntimeError("Backup not found")
    if row["statut"] != "termine":
        raise RuntimeError("Only a finished backup can be verified")
    own_task = task_id is None
    if own_task:
        task_id = create_task("verify_backup", row["vm_name"], username=username)
    try:
        status, problems, _count = backup_integrity.verify(
            row["chemin"], row["checksum_sha256"], progress=lambda pct: update_task_progress(task_id, pct)
        )
    except Exception as e:
        if own_task:
            finish_task(task_id, "echec", str(e))
        raise
    _store().set_verification(backup_id, status, _now().isoformat(), "; ".join(problems) or None)
    if own_task:
        finish_task(task_id, "termine")
    if status == backup_integrity.CORRUPT:
        detail = f"backup #{backup_id}: {'; '.join(problems)}"[:1000]
        log_action(username, "verify_backup", row["vm_name"], "echec", detail)
        from app.core.notifications import notify

        notify("verify_backup", f"Corrupted backup of {row['vm_name']}", detail, result="echec")
    else:
        log_action(username, "verify_backup", row["vm_name"], "succes", f"backup #{backup_id}")
    return {"verification": status, "problemes": problems}


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


def _apply_retention(vm_name, policy):
    """Per VM, not per job_id (see the comment in run_backup): the policy (app/core/backup_retention.py) applies
    to ALL the finished backups of the VM, whether they come from a schedule or from a manual trigger."""
    rows = _store().finished_of(vm_name)
    keep = backup_retention.kept(
        [(r["id"], r["cree_le"]) for r in rows],
        policy["last"],
        policy["daily"],
        policy["weekly"],
        policy["monthly"],
    )
    for row in rows:
        if row["id"] not in keep:
            shutil.rmtree(row["chemin"], ignore_errors=True)
            _store().delete(row["id"])


def _next_run(frequence, heure, from_time=None):
    base = from_time or _now()
    hh, mm = (int(x) for x in heure.split(":"))
    candidate = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if candidate <= base:
        candidate += timedelta(days=1)
    if frequence == "hebdomadaire":
        while candidate.weekday() != 0:  # execute le lundi
            candidate += timedelta(days=1)
    elif frequence == "mensuel":
        candidate = candidate.replace(day=1)
        if candidate <= base:
            month = candidate.month % 12 + 1
            year = candidate.year + (1 if candidate.month == 12 else 0)
            candidate = candidate.replace(year=year, month=month)
    return candidate


def _hosted_vms():
    conn = open_conn()
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
            run_backup(job["vm_name"], job["cible_dir"], job_id=job["id"], username="scheduler")
        except vm_locks.VmBusy:
            continue  # another operation runs on this VM: the job stays due and is retried next tick
        except Exception as e:
            log_action("scheduler", "backup_job_echec", job["vm_name"], "echec", str(e))
        next_run = _next_run(job["frequence"], job["heure"], now)
        _store().record_schedule_run(job["id"], now.isoformat(), next_run.isoformat())


def _scheduler_loop():
    while True:
        try:
            now = _now()
            run_due_schedules(now)
            from app.core import backup_groups

            backup_groups.run_due(now)
            _verify_due_backup()
        except Exception:
            logger.exception("Backup scheduler tick failed")
        time.sleep(SCHEDULER_INTERVAL_S)


def start_backup_scheduler():
    thread = threading.Thread(target=_scheduler_loop, daemon=True)
    thread.start()
    return thread
