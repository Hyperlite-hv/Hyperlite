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
import hashlib
import re
import shutil
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import libvirt

from app.core.audit import log_action
from app.core.database import get_conn
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import open_conn
from app.core.tasks import create_task, finish_task, update_task_progress
from app.core.vm_builder import IMAGES_DIR

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
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


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


def qemu_img_convert_with_progress(source, dest, task_id, base_pct, span_pct):
    """Copy with qemu-img convert -p, parse the real progress on stdout and report it
    in the task (base_pct/span_pct allow calling this several times, e.g. for
    several disks, without each one restarting from 0%)."""
    proc = subprocess.Popen(
        ["qemu-img", "convert", "-p", "-O", "qcow2", str(source), str(dest)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
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

    overlay_paths = {}
    disk_xml_parts = []
    # A disk that BELONGS to the VM but is absent from `disks` (a partial export, see
    # app/core/vm_export.py) must remain explicitly excluded (snapshot='no'):
    # otherwise libvirt still applies its default (internal) snapshot behaviour to
    # it, which would never be cleaned up since the merge loop below only walks
    # `disks`.
    for dev, _source in all_disks:
        if dev in target_devs:
            overlay = IMAGES_DIR / f"{vm_name}.backup-{int(time.time())}.{dev}.qcow2"
            overlay_paths[dev] = overlay
            disk_xml_parts.append(f"<disk name='{dev}' snapshot='external'><source file='{overlay}'/></disk>")
        else:
            disk_xml_parts.append(f"<disk name='{dev}' snapshot='no'/>")
    snap_name = f"hyperlite-backup-{int(time.time())}"
    snap_xml = f"<domainsnapshot><name>{snap_name}</name><disks>{''.join(disk_xml_parts)}</disks></domainsnapshot>"

    snap = domain.snapshotCreateXML(snap_xml, libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_DISK_ONLY)
    update_task_progress(task_id, 10)

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
    finally:
        # Merge the overlay back into the base for EVERY disk, even if the copy failed on
        # one of them: never leave the VM running indefinitely on a transient overlay (a
        # chain that grows without end, orphaned if Hyperlite restarts in the meantime).
        for dev, source in disks:
            try:
                domain.blockCommit(dev, str(source), None, 0, libvirt.VIR_DOMAIN_BLOCK_COMMIT_ACTIVE)
                for _ in range(60):
                    info = domain.blockJobInfo(dev, 0)
                    if not info or (info.get("end", 0) and info.get("cur", 0) >= info["end"]):
                        break
                    time.sleep(0.5)
                domain.blockJobAbort(dev, libvirt.VIR_DOMAIN_BLOCK_JOB_ABORT_PIVOT)
            except libvirt.libvirtError as e:
                log_action("system", "backup_commit_warning", vm_name, "echec", f"{dev}: {describe_exception(e)}")
        with contextlib.suppress(libvirt.libvirtError):
            snap.delete(libvirt.VIR_DOMAIN_SNAPSHOT_DELETE_METADATA_ONLY)
        for overlay in overlay_paths.values():
            Path(overlay).unlink(missing_ok=True)

    return dest_paths


def run_backup(vm_name, target_dir=None, job_id=None, username="system"):
    """Run a backup (choosing hot or cold according to the real state of the VM) and
    return the id of the `backups` row created. Synchronous: called from a
    thread by the endpoint (manual backup) or by the scheduler.

    _backup_lock: only one backup at a time on the WHOLE server (all VMs
    combined), see the comment above _backup_lock. A concurrent caller simply
    waits its turn instead of failing."""
    with _backup_lock:
        return _run_backup_locked(vm_name, target_dir, job_id, username)


def _run_backup_locked(vm_name, target_dir, job_id, username):
    target_root = Path(target_dir) if target_dir else DEFAULT_BACKUP_DIR
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(vm_name)
        except libvirt.libvirtError:
            raise RuntimeError(f"VM '{vm_name}' not found") from None

        mode = "chaud" if domain.isActive() else "froid"
        stamp = _now().strftime("%Y%m%dT%H%M%SZ")
        dest_dir = target_root / vm_name / stamp
        dest_dir.mkdir(parents=True, exist_ok=True)

        task_id = create_task("backup_vm", vm_name, node=conn.getHostname(), username=username)
        with get_conn() as db:
            cur = db.execute(
                "INSERT INTO backups (vm_name, job_id, chemin, mode, cree_le, statut, task_id) "
                "VALUES (?, ?, ?, ?, ?, 'en_cours', ?)",
                (vm_name, job_id, str(dest_dir), mode, _now().isoformat(), task_id),
            )
            backup_id = cur.lastrowid
            db.commit()

        try:
            if mode == "froid":
                dest_paths = backup_cold(domain, vm_name, dest_dir, task_id)
            else:
                dest_paths = backup_hot(conn, domain, vm_name, dest_dir, task_id)

            update_task_progress(task_id, 95)
            total_size = sum(p.stat().st_size for p in dest_paths)
            checksum = _sha256_of(dest_paths[0]) if len(dest_paths) == 1 else None
            with get_conn() as db:
                db.execute(
                    "UPDATE backups SET statut = 'termine', taille_octets = ?, checksum_sha256 = ? WHERE id = ?",
                    (total_size, checksum, backup_id),
                )
                db.commit()
            finish_task(task_id, "termine")
            log_action(username, "backup_vm", vm_name, "succes", f"{mode}, {total_size} octets -> {dest_dir}")
            # Retention (retention_count, part of the schema) is applied HERE, in the same
            # place for manual and scheduled backups, and on ALL the backups of this VM (not
            # only those of the same job_id): a retention_count configured for a VM must cap
            # the total number of its backups, not just those coming from one specific job.
            # It does nothing when no backup_jobs row exists for this VM (no configured
            # policy means no imposed limit, so a 100% manual usage without scheduling is
            # unchanged).
            with get_conn() as db:
                job_row = db.execute("SELECT retention_count FROM backup_jobs WHERE vm_name = ?", (vm_name,)).fetchone()
            if job_row:
                _apply_retention(vm_name, job_row["retention_count"])
        except Exception as e:
            msg = describe_exception(e) if isinstance(e, libvirt.libvirtError) else str(e)
            with get_conn() as db:
                db.execute("UPDATE backups SET statut = 'echec', erreur = ? WHERE id = ?", (msg, backup_id))
                db.commit()
            finish_task(task_id, "echec", msg)
            log_action(username, "backup_vm", vm_name, "echec", msg)
            shutil.rmtree(dest_dir, ignore_errors=True)
            raise
        return backup_id
    finally:
        conn.close()


def restore_backup(backup_id, mode, new_name=None, username="system"):
    """mode='overwrite': overwrite the disks of the original VM (it must be
    stopped). mode='new': define a new VM from the backup, with a new UUID/MAC
    (the same logic as cloning)."""
    with get_conn() as db:
        row = db.execute("SELECT * FROM backups WHERE id = ?", (backup_id,)).fetchone()
    if not row:
        raise RuntimeError("Backup not found")
    if row["statut"] != "termine":
        raise RuntimeError("This backup is not in a restorable state (failed or in progress)")

    src_dir = Path(row["chemin"])
    disk_files = sorted(src_dir.glob("*.qcow2"))
    if not disk_files:
        raise RuntimeError("No disk file found in this backup")

    conn = open_conn()
    task_id = create_task("restore_backup", row["vm_name"], node=conn.getHostname(), username=username)
    try:
        if mode == "overwrite":
            target_name = row["vm_name"]
            try:
                domain = conn.lookupByName(target_name)
            except libvirt.libvirtError:
                raise RuntimeError(
                    f"Original VM '{target_name}' not found: use the restore to a new location"
                ) from None
            if domain.isActive():
                raise RuntimeError("Stop the VM before restoring over it")
            existing_disks = domain_disk_paths(domain)
            for i, (_dev, dest_path) in enumerate(existing_disks):
                src = disk_files[min(i, len(disk_files) - 1)]
                update_task_progress(task_id, int(10 + 80 * i / max(len(existing_disks), 1)))
                shutil.copyfile(src, dest_path)
            finish_task(task_id, "termine")
            log_action(username, "restore_backup", target_name, "succes", f"overwrite from backup #{backup_id}")
            return {"vm": target_name, "mode": "overwrite"}

        elif mode == "new":
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

            new_disk_paths = []
            for i, src in enumerate(disk_files):
                suffix = "" if i == 0 else f"-{i + 1}"
                dest = _IMAGES_DIR / f"{new_name}{suffix}.qcow2"
                update_task_progress(task_id, int(10 + 70 * i / len(disk_files)))
                shutil.copyfile(src, dest)
                new_disk_paths.append(dest)

            xml = build_domain_xml(new_name, 1, 1024, new_disk_paths, None, "default")
            conn.defineXML(xml)
            finish_task(task_id, "termine")
            log_action(username, "restore_backup", new_name, "succes", f"new VM from backup #{backup_id}")
            return {"vm": new_name, "mode": "new"}
        else:
            raise RuntimeError("invalid mode (expected 'overwrite' or 'new')")
    except Exception as e:
        finish_task(task_id, "echec", str(e))
        raise
    finally:
        conn.close()


def _apply_retention(vm_name, retention_count):
    """Per VM, not per job_id (see the comment in run_backup): a retention_count
    configured for a VM caps the TOTAL number of its finished backups, whether
    they come from a scheduled job or from a manual trigger."""
    with get_conn() as db:
        rows = db.execute(
            "SELECT id, chemin FROM backups WHERE vm_name = ? AND statut = 'termine' ORDER BY cree_le DESC",
            (vm_name,),
        ).fetchall()
        for row in rows[retention_count:]:
            shutil.rmtree(row["chemin"], ignore_errors=True)
            db.execute("DELETE FROM backups WHERE id = ?", (row["id"],))
        db.commit()


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


def _scheduler_loop():
    while True:
        try:
            now = _now()
            with get_conn() as db:
                due = db.execute(
                    "SELECT * FROM backup_jobs WHERE actif = 1 AND prochaine_execution <= ?", (now.isoformat(),)
                ).fetchall()
            for job in due:
                try:
                    # Retention is now applied INSIDE run_backup() itself (see its body), which also
                    # covers the manual backups of this VM, not only those of the scheduler.
                    run_backup(job["vm_name"], job["cible_dir"], job_id=job["id"], username="scheduler")
                except Exception as e:
                    log_action("scheduler", "backup_job_echec", job["vm_name"], "echec", str(e))
                next_run = _next_run(job["frequence"], job["heure"], now)
                with get_conn() as db:
                    db.execute(
                        "UPDATE backup_jobs SET derniere_execution = ?, prochaine_execution = ? WHERE id = ?",
                        (now.isoformat(), next_run.isoformat(), job["id"]),
                    )
                    db.commit()
        except Exception as e:
            print(f"[backups] scheduler tick failed: {e!r}", flush=True)
        time.sleep(SCHEDULER_INTERVAL_S)


def start_backup_scheduler():
    thread = threading.Thread(target=_scheduler_loop, daemon=True)
    thread.start()
    return thread
