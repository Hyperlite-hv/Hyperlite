"""Replication to another site: an incremental copy of each VM every few minutes (docs/design/replication.md, section 5).

A copy ("point") is a directory `<target>/<vm>/<timestamp>/`, laid out like a backup (disks, vm-config.json, NVRAM
and TPM state, manifest.json), so the other site restores it with site recovery (app/core/site_recovery.py). A chain
starts with a full point; each following point holds only the clusters written since the previous one, in qcow2 files
whose backing file is the previous point's disk, **by a relative path**, so the chain still reads when the other site
mounts the share elsewhere. Reading the newest point's disk through its chain gives the disk at that moment.

How a point is made:
  - VM running: libvirt's backup API in push mode. The first point of a chain creates a checkpoint (a dirty bitmap in
    each qcow2 disk); the next ones copy what changed since it and move the checkpoint forward. The guest's file
    systems are frozen while the copy starts, when the guest agent runs.
  - VM stopped: nothing to do when its disk files were not modified since the last point was made with the VM
    stopped (any QEMU run rewrites them, and Hyperlite's own disk operations drop the chain, see
    app/core/checkpoints.py). Otherwise it is started
    **paused**, so the guest never runs, just for the copy, then stopped again: libvirt's backup API needs a running
    QEMU, and the bitmaps kept tracking the writes while it was stopped. Proxmox backs stopped VMs up the same way.
    A VM with a passed-through device is never started that way (it would take the device): it gets a full cold
    copy with qemu-img instead.
A chain is restarted after MAX_POINTS points (one day at the default interval), so a damaged point never costs more
than a day, and only the KEEP_CHAINS most recent chains are kept. A point is never deleted while a later one depends
on it.

Only VMs whose disks are all qcow2 files with persistent bitmaps (qcow2 version 3) can be replicated; the others are
reported with the reason.
"""

import json
import logging
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import libvirt

from app.core import backup_groups, backup_integrity, checkpoints, firmware, guest_agent, object_meta, vm_locks
from app.core.safe_paths import safe_child

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL = 15
MIN_INTERVAL, MAX_INTERVAL = 5, 1440
MAX_POINTS = 96  # a new full copy every day at the default interval
KEEP_CHAINS = 2
MAX_PARALLEL = 2  # VMs copied at once: the link between the sites is not known in advance
JOB_TIMEOUT_S = 6 * 3600

_running_jobs = set()
_running_lock = threading.Lock()


class ReplicationError(Exception):
    pass


def _store():
    from app.repositories import registry

    return registry.replication().sync


def _now():
    return datetime.now(UTC)


# --- What can be replicated ---------------------------------------------------------------------------------------


def _qcow2_compat(path):
    proc = subprocess.run(["qemu-img", "info", "-U", "--output=json", path], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise ReplicationError(f"Cannot read {Path(path).name}: {proc.stderr.strip()[:200]}")
    info = json.loads(proc.stdout)
    return ((info.get("format-specific") or {}).get("data") or {}).get("compat")


def disks_of(domain):
    """[(target dev, image path)] of the VM's disks, or ReplicationError with what prevents replicating it."""
    root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    disks = []
    for disk in root.findall("./devices/disk"):
        if disk.get("device") != "disk":
            continue
        target, driver, source = disk.find("target"), disk.find("driver"), disk.find("source")
        dev = target.get("dev") if target is not None else "?"
        if disk.get("type") != "file" or source is None or not source.get("file"):
            raise ReplicationError(f"Disk {dev} is not a file (ZFS, iSCSI or a device): it cannot be replicated yet")
        if driver is None or driver.get("type") != "qcow2":
            raise ReplicationError(f"Disk {dev} is not qcow2: replication needs qcow2 disks")
        disks.append((dev, source.get("file")))
    if not disks:
        raise ReplicationError("The VM has no disk")
    for dev, path in disks:
        if _qcow2_compat(path) != "1.1":
            raise ReplicationError(
                f"Disk {dev} is an old qcow2 (version 2) without dirty bitmaps: convert it with "
                f"'qemu-img amend -o compat=1.1 {path}' while the VM is stopped"
            )
    return disks


# --- One point ----------------------------------------------------------------------------------------------------


def _stamp(vm_dir):
    stamp = _now().strftime("%Y%m%dT%H%M%SZ")
    while (vm_dir / stamp).exists():
        time.sleep(1)
        stamp = _now().strftime("%Y%m%dT%H%M%SZ")
    return stamp


def _backup_xml(disks, point_dir, incremental_from, skipped=()):
    root = ET.Element("domainbackup")
    if incremental_from:
        ET.SubElement(root, "incremental").text = incremental_from
    container = ET.SubElement(root, "disks")
    for dev, _path in disks:
        disk = ET.SubElement(container, "disk", name=dev, backup="yes", type="file")
        ET.SubElement(disk, "target", file=str(point_dir / f"{dev}.qcow2"))
        ET.SubElement(disk, "driver", type="qcow2")
    for dev in skipped:
        ET.SubElement(container, "disk", name=dev, backup="no")
    return ET.tostring(root, encoding="unicode")


def _checkpoint_xml(name):
    root = ET.Element("domaincheckpoint")
    ET.SubElement(root, "name").text = name
    return ET.tostring(root, encoding="unicode")


def _wait(domain):
    deadline = time.monotonic() + JOB_TIMEOUT_S
    while domain.jobInfo()[0] != libvirt.VIR_DOMAIN_JOB_NONE:
        if time.monotonic() > deadline:
            domain.abortJob()
            raise ReplicationError("The copy took too long and was stopped")
        time.sleep(0.5)
    stats = domain.jobStats(libvirt.VIR_DOMAIN_JOB_STATS_COMPLETED)
    if stats.get("type") != libvirt.VIR_DOMAIN_JOB_COMPLETED:
        raise ReplicationError(f"The copy failed: {stats.get('errmsg') or 'no reason given by libvirt'}")


def _run_backup_job(domain, disks, point_dir, checkpoint, incremental_from, skipped=()):
    flags = libvirt.VIR_DOMAIN_BACKUP_BEGIN_REUSE_EXTERNAL if incremental_from else 0
    frozen = False
    if guest_agent.state(domain) == guest_agent.CONNECTED and guest_agent.bound(domain):
        try:
            domain.fsFreeze()
            frozen = True
        except libvirt.libvirtError:
            logger.info("The guest agent of %s did not freeze its file systems", domain.name(), exc_info=True)
    try:
        domain.backupBegin(
            _backup_xml(disks, point_dir, incremental_from, skipped),
            _checkpoint_xml(checkpoint) if checkpoint else None,
            flags,
        )
    finally:
        # The copy reads the disks as they were when the job began: the guest only needs to be frozen that long.
        if frozen:
            try:
                domain.fsThaw()
            except libvirt.libvirtError:
                logger.warning("Could not thaw the file systems of %s", domain.name(), exc_info=True)
    _wait(domain)


def copy_running(domain, disks, all_devs, dest_dir):
    """A full copy of a running VM's `disks` into `dest_dir/<dev>.qcow2` with libvirt's backup API, without a
    checkpoint. Used by the hot backup of a replicated VM (app/core/backups.py): its usual transient external
    snapshot is a block operation libvirt refuses while the replication checkpoint exists, and dropping that
    checkpoint would make the next replication a full copy."""
    wanted = {dev for dev, _path in disks}
    _run_backup_job(domain, disks, Path(dest_dir), None, None, skipped=[d for d in all_devs if d not in wanted])
    return [Path(dest_dir) / f"{dev}.qcow2" for dev, _path in disks]


def _create_incremental_targets(disks, point_dir, previous):
    for dev, _path in disks:
        target = point_dir / f"{dev}.qcow2"
        backing = f"../{previous}/{dev}.qcow2"
        proc = subprocess.run(
            ["qemu-img", "create", "-q", "-f", "qcow2", "-b", backing, "-F", "qcow2", str(target)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=point_dir,
        )
        if proc.returncode != 0:
            raise ReplicationError(f"Cannot prepare the copy of {dev}: {proc.stderr.strip()[:200]}")


def _cold_copy(disks, point_dir):
    for dev, path in disks:
        proc = subprocess.run(
            ["qemu-img", "convert", "-O", "qcow2", path, str(point_dir / f"{dev}.qcow2")],
            capture_output=True,
            text=True,
            timeout=JOB_TIMEOUT_S,
        )
        if proc.returncode != 0:
            raise ReplicationError(f"Cannot copy {dev}: {proc.stderr.strip()[:200]}")


def _disks_mtime(disks):
    return max(Path(path).stat().st_mtime for _dev, path in disks)


def _has_hostdev(domain):
    return ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)).find("./devices/hostdev") is not None


def _finish_point(domain, vm_name, point_dir, disks, kind, chain, parent, running):
    from app.core.backups import _write_vm_config

    _write_vm_config(domain, point_dir)
    backup_integrity.save_firmware_state(domain, point_dir)
    images = [(dev, point_dir / f"{dev}.qcow2") for dev, _path in disks]
    backup_integrity.write_manifest(
        point_dir,
        vm_name,
        "chaud" if running else "froid",
        firmware.of_domain(ET.fromstring(domain.XMLDesc(0))),
        images,
        replication={"type": kind, "chaine": chain, "parent": parent},
    )


def _prune(vm_dir, current_chain):
    """Keep the KEEP_CHAINS newest chains of replication points; never touch an ordinary backup in the same place."""
    chains = {}
    for point in vm_dir.iterdir():
        if point.is_symlink() or not point.is_dir():
            continue
        info = (backup_integrity.read_manifest(point) or {}).get("replication")
        if info and info.get("chaine"):
            chains.setdefault(info["chaine"], []).append(point)
    keep = sorted(set(chains) | {current_chain}, reverse=True)[:KEEP_CHAINS]
    for chain, points in chains.items():
        if chain not in keep:
            for point in points:
                shutil.rmtree(point, ignore_errors=True)


def replicate(vm_name, target_dir, username="scheduler"):
    """Make the next point of `vm_name` under `target_dir`. Returns "complet", "incremental" or "inchange"."""
    claim = vm_locks.claim(vm_name, "a replication")
    with claim:
        try:
            result = _replicate(vm_name, target_dir)
        except Exception as e:
            message = str(e) or type(e).__name__
            _store().save_state(vm_name, statut="echec", erreur=message[:500], maj_le=_now().isoformat())
            raise
    return result


def _replicate(vm_name, target_dir):
    from app.core.libvirt_utils import open_conn

    try:
        root = Path(backup_groups.validate_target(target_dir) or "")
    except backup_groups.GroupError as e:
        raise ReplicationError(str(e)) from None
    if not root.is_absolute():
        raise ReplicationError("Choose the directory of the other site's storage")
    vm_dir = safe_child(root, vm_name)
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(vm_name)
        except libvirt.libvirtError:
            raise ReplicationError(f"VM '{vm_name}' not found") from None
        disks = disks_of(domain)
        state = _store().state(vm_name) or {}
        running = domain.isActive()
        same_target = state.get("cible_dir") == str(root)
        previous = state.get("dernier_point") if same_target else None
        previous_ok = bool(previous) and all((vm_dir / previous / f"{dev}.qcow2").is_file() for dev, _p in disks)

        last_checkpoint = state.get("checkpoint")
        chain_alive = previous_ok and last_checkpoint in checkpoints.names(domain)
        if not running and previous_ok and state.get("mtime_disques") == _disks_mtime(disks):
            _store().save_state(vm_name, statut="ok", erreur=None, maj_le=_now().isoformat())
            return "inchange"
        paused = not running and not _has_hostdev(domain)

        vm_dir.mkdir(parents=True, exist_ok=True)
        stamp = _stamp(vm_dir)
        point_dir = vm_dir / stamp
        point_dir.mkdir()
        new_checkpoint = f"{checkpoints.PREFIX}{stamp}"
        incremental = (running or paused) and chain_alive and int(state.get("points") or 0) < MAX_POINTS
        try:
            if paused:
                domain.createWithFlags(libvirt.VIR_DOMAIN_START_PAUSED)
            if incremental:
                _create_incremental_targets(disks, point_dir, previous)
                _run_backup_job(domain, disks, point_dir, new_checkpoint, last_checkpoint)
                kind, chain, parent, points = "incremental", state["chaine"], previous, int(state["points"]) + 1
            else:
                # A new chain: the old checkpoints would only accumulate bitmaps in the disks.
                checkpoints.release(domain, vm_name)
                if running or paused:
                    _run_backup_job(domain, disks, point_dir, new_checkpoint, None)
                else:
                    _cold_copy(disks, point_dir)
                    new_checkpoint = None
                kind, chain, parent, points = "complet", stamp, None, 1
            if incremental and last_checkpoint:
                # Only the newest checkpoint is needed; keeping the old ones would grow every disk with bitmaps.
                try:
                    domain.checkpointLookupByName(last_checkpoint).delete(0)
                except libvirt.libvirtError:
                    logger.warning("Could not drop the previous checkpoint of %s", vm_name, exc_info=True)
            _finish_point(domain, vm_name, point_dir, disks, kind, chain, parent, running)
        except Exception:
            shutil.rmtree(point_dir, ignore_errors=True)
            # A checkpoint created by a failed job marks blocks nobody copied: drop it, the previous one still holds.
            if (running or paused) and new_checkpoint and domain.isActive():
                try:
                    domain.checkpointLookupByName(new_checkpoint).delete(0)
                except libvirt.libvirtError:
                    logger.debug("No checkpoint %s to drop", new_checkpoint, exc_info=True)
            raise
        finally:
            if paused and domain.isActive():
                # Never a guest boot: it was started paused, for the copy only.
                domain.destroy()
        _store().save_state(
            vm_name,
            cible_dir=str(root),
            chaine=chain,
            dernier_point=stamp,
            checkpoint=new_checkpoint,
            points=points,
            dernier_ok_le=_now().isoformat(),
            mtime_disques=None if domain.isActive() else _disks_mtime(disks),
            statut="ok",
            erreur=None,
            maj_le=_now().isoformat(),
        )
        if kind == "complet":
            _prune(vm_dir, chain)
        return kind
    finally:
        conn.close()


def forget(vm_name):
    """The VM is renamed or removed: its replication state goes; its points stay on the other site's storage."""
    _store().delete_state(vm_name)


# --- Jobs ---------------------------------------------------------------------------------------------------------


def validate(payload):
    name = (payload.get("nom") or "").strip()
    if not backup_groups.NAME_RE.match(name):
        raise ReplicationError("Invalid name: letters, digits, spaces, dots, dashes, 64 characters at most")
    selection = payload.get("selection")
    value = (payload.get("valeur") or "").strip() or None
    if selection not in ("toutes", "etiquette", "pool"):
        raise ReplicationError("selection must be toutes, etiquette or pool")
    if selection == "etiquette":
        tags = object_meta.normalize_tags([value or ""])
        if not tags:
            raise ReplicationError("Choose the tag whose VMs are replicated")
        value = tags[0]
    elif selection == "pool":
        from app.core import permissions

        if not value or not value.isdigit() or int(value) not in {p["id"] for p in permissions.list_pools()}:
            raise ReplicationError("Choose an existing pool")
    else:
        value = None
    try:
        target = backup_groups.validate_target(payload.get("cible_dir"))
    except backup_groups.GroupError as e:
        raise ReplicationError(str(e)) from None
    if not target:
        raise ReplicationError("Choose the directory of the other site's storage")
    interval = payload.get("intervalle_minutes", DEFAULT_INTERVAL)
    if not isinstance(interval, int) or not MIN_INTERVAL <= interval <= MAX_INTERVAL:
        raise ReplicationError(f"The interval must be {MIN_INTERVAL} to {MAX_INTERVAL} minutes")
    return {
        "nom": name,
        "selection": selection,
        "valeur": value,
        "exclues": sorted({str(v) for v in payload.get("exclues") or [] if v}),
        "cible_dir": target,
        "intervalle_minutes": interval,
        "actif": 1 if payload.get("actif", True) else 0,
    }


def save_job(payload, job_id=None):
    values = validate(payload)
    next_run = _now().isoformat()  # a new or changed job runs at the next scheduler tick
    try:
        if job_id is None:
            job_id = _store().create_job(values, next_run)
        elif not _store().update_job(job_id, values, next_run):
            return None
    except Exception as e:
        if "UNIQUE" in str(e):
            raise ReplicationError(f"A replication job is already named {values['nom']}") from None
        raise
    return job_with_vms(_store().get_job(job_id))


def job_with_vms(job):
    if job:
        job = dict(job)
        job["vms"] = backup_groups.resolve(job)
        job["en_cours"] = job["id"] in _running_jobs
    return job


def list_jobs():
    return [job_with_vms(j) for j in _store().list_jobs()]


def delete_job(job_id):
    return _store().delete_job(job_id)


def run_job(job, username="scheduler"):
    """Replicate every VM of the job, MAX_PARALLEL at a time. Returns {vm: result or error}."""
    with _running_lock:
        if job["id"] in _running_jobs:
            return {}
        _running_jobs.add(job["id"])
    results = {}
    try:

        def one(vm):
            try:
                return vm, replicate(vm, job["cible_dir"], username)
            except vm_locks.VmBusy as e:
                return vm, f"skipped: {e}"
            except Exception as e:
                logger.warning("Replication of %s failed", vm, exc_info=True)
                return vm, f"failed: {e}"

        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
            for vm, result in pool.map(one, backup_groups.resolve(job)):
                results[vm] = result
    finally:
        with _running_lock:
            _running_jobs.discard(job["id"])
    return results


def _run_and_record(job, username):
    from app.core.audit import log_action

    results = run_job(job, username)
    failed = {vm: r for vm, r in results.items() if r not in ("complet", "incremental", "inchange")}
    log_action(
        username,
        "replication",
        job["nom"],
        "echec" if failed else "succes",
        "; ".join(f"{vm}: {r}" for vm, r in failed.items())[:500] or f"{len(results)} VM(s)",
    )


def start_job(job, username):
    """Run a job now, in the background."""
    threading.Thread(target=_run_and_record, args=(job, username), daemon=True, name="replication").start()


def run_due(now):
    """Called by the backup scheduler: starts the jobs whose time has come. Each runs in its own thread so a long
    full copy never delays the backups."""
    from app.core import cluster_lead

    own = cluster_lead.in_cluster()  # in a cluster, each node replicates its VMs on its own schedule
    for job in _store().due_jobs(now.isoformat()):
        _store().record_run(
            job["id"],
            now.isoformat(),
            (now + timedelta(minutes=job["intervalle_minutes"])).isoformat(),
            shared_next=job["prochaine_partagee"] if own else None,
        )
        if job["id"] not in _running_jobs:
            start_job(job, "scheduler")


def status(now=None):
    """Each replicated VM: its last good copy, its age and whether it is late (no good copy within twice the
    shortest interval of the jobs covering it)."""
    now = now or _now()
    intervals = {}
    for job in _store().list_jobs():
        if not job["actif"]:
            continue
        for vm in backup_groups.resolve(job):
            intervals[vm] = min(intervals.get(vm, MAX_INTERVAL), job["intervalle_minutes"])
    rows = []
    for state in _store().states():
        last = state.get("dernier_ok_le")
        age = (now - datetime.fromisoformat(last)).total_seconds() if last else None
        interval = intervals.get(state["vm_name"])
        state = dict(state)
        state["age_s"] = int(age) if age is not None else None
        state["intervalle_minutes"] = interval
        state["en_retard"] = bool(interval) and (age is None or age > 2 * interval * 60)
        rows.append(state)
    return rows


TICK_S = 30


def _loop():
    while True:
        try:
            run_due(_now())
        except Exception:
            logger.exception("Replication scheduler tick failed")
        time.sleep(TICK_S)


def start_replication_scheduler():
    """Its own thread, not the backup scheduler's: a long nightly backup must not delay a 15-minute replication."""
    thread = threading.Thread(target=_loop, daemon=True, name="replication")
    thread.start()
    return thread
