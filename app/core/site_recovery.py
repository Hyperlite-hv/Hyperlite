"""Recovery of a lost site: restore here the VMs another Hyperlite backed up to a storage this node can read.

Two sites run two separate Hyperlite installations (a Corosync cluster needs a LAN, see
docs/design/control-plane-v2-migration.md section 15.2). Each site backs its VMs up to a storage of the other site,
an NFS share for instance. When a whole site is lost, the other one still runs its own VMs; this module lets it start
the lost site's VMs from those backups:

  1. `scan(directory)` lists the backups found there, laid out as Hyperlite writes them (<dir>/<vm>/<timestamp>/ with
     a manifest.json), whether or not this node's catalogue knows them;
  2. `register(path)` adds one of them to this node's catalogue (`backups.imported_from` names the host that made it),
     so the ordinary restore, with its integrity check first, applies to it;
  3. `recover(...)` restores a selection as new VMs, one after another, in one background task.

Nothing here ever deletes or overwrites: a restore creates a new VM and refuses a name already in use. Deciding that
a site is lost stays with an administrator: from the other site, a dead site and a cut link look the same, and
starting the same VMs on both sides would corrupt their data.
"""

import json
import logging
import os
import threading
from pathlib import Path

import libvirt

from app.core import backup_integrity, backup_store, paths
from app.core.backup_groups import GroupError, validate_target
from app.core.tasks import create_task, finish_task, update_task_progress

logger = logging.getLogger(__name__)

MAX_VMS = 2000
MAX_BACKUPS_PER_VM = 500
# Where an administrator mounts a share by hand; storage pools, the backup directory and the backup and replication
# jobs' directories are allowed too (_allowed_roots).
MOUNT_ROOTS = ("/mnt", "/media", "/srv")


class RecoveryError(Exception):
    pass


def _store():
    from app.repositories import registry

    return registry.backups().sync


def _allowed_roots():
    """The directories under which another site's backups may be read: the usual mount points, this node's storage
    pools (an NFS share of the other site is usually one), the backup directory and the backup and replication jobs'
    directories. A path from a client is read only under one of them, so the service, which runs as root, never reads
    a place an administrator did not set up for storage."""
    from app.core import backup_groups, replication
    from app.core.backups import DEFAULT_BACKUP_DIR
    from app.core.libvirt_utils import open_conn, pool_type_and_target_path

    roots = [*MOUNT_ROOTS, str(DEFAULT_BACKUP_DIR)]
    # The jobs' rows straight from their stores: list_jobs() would also ask libvirt for each job's VMs.
    roots += [paths.current(dict(row).get("target_dir")) for row in backup_groups._store().list_group_jobs()]
    roots += [paths.current(dict(row).get("target_dir")) for row in replication._store().list_jobs()]
    try:
        conn = open_conn(readonly=True)
        try:
            roots += [pool_type_and_target_path(pool)[1] for pool in conn.listAllStoragePools()]
        finally:
            conn.close()
    except libvirt.libvirtError:
        logger.warning("Storage pools unreadable: only the other storage directories are allowed", exc_info=True)
    return [os.path.realpath(root) for root in roots if root]


def _allowed(path):
    """`path` with every symbolic link resolved, when it lies under an allowed root; RecoveryError otherwise. A path
    that went through a link is refused too: the link could lead anywhere."""
    try:
        normalized = validate_target(path)
    except GroupError as e:
        raise RecoveryError(str(e)) from None
    if not normalized:
        raise RecoveryError("Choose the directory that holds the other site's backups")
    real = os.path.realpath(normalized)
    if real != normalized:
        raise RecoveryError(f"{normalized} goes through a symbolic link: give the directory's real path")
    for base in _allowed_roots():
        # The base itself, or below it: "/mnt/a" must not admit "/mnt/ab". One startswith on every accepted path,
        # so static analysis sees each one checked.
        if real.startswith(base) and real[len(base) : len(base) + 1] in ("", os.sep):
            return Path(real)
    raise RecoveryError(
        f"{normalized} is outside the storage this node knows: mount the share under /mnt, /media or /srv, or add it "
        "as a storage pool"
    )


def _directory(path):
    """The directory to scan: absolute, outside the system's own directories, without a symbolic link anywhere (a link
    would lead past that check, to /etc say), under an allowed root, an existing directory."""
    root = _allowed(path)
    if not root.is_dir():
        raise RecoveryError(f"{root} is not a directory this node can read")
    return root


def _summary(backup_dir, manifest, known):
    files = manifest.get("files") or []
    disks = [f for f in files if f.get("role") == "disk"]
    path = str(backup_dir)
    row = known.get(path)
    return {
        "path": path,
        "created_at": manifest.get("created_at"),
        "mode": manifest.get("mode") if manifest.get("mode") in ("live", "cold") else "cold",
        "firmware": manifest.get("firmware") or "bios",
        "source": manifest.get("source"),
        "disks": len(disks),
        "size_bytes": sum(int(f.get("size") or 0) for f in files),
        "backup_id": row["id"] if row else None,
    }


def scan(directory):
    """[{"vm", "exists_here", "backups": [newest first]}] for every VM with at least one complete backup (a
    manifest listing at least one disk) under `directory`. Symbolic links are not followed: a link planted on a
    shared storage must not make this node read elsewhere."""
    from app.core.vm_builder import validate_name

    root = _directory(directory)
    known = {r["path"]: r for r in _store().list_all(limit=100000)}
    if (root / backup_store.CONFIG).is_file():
        vms = _scan_store(root, known)
        return _mark_existing(vms)
    vms = []
    try:
        vm_dirs = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError as e:
        raise RecoveryError(f"{root} cannot be read: {e.strerror}") from None
    for vm_entry in vm_dirs:
        if len(vms) >= MAX_VMS:
            break
        if not vm_entry.is_dir(follow_symlinks=False) or validate_name(vm_entry.name):
            continue
        backups = []
        try:
            stamps = sorted(os.scandir(vm_entry.path), key=lambda e: e.name, reverse=True)
        except OSError:
            logger.warning("Cannot read %s", vm_entry.path, exc_info=True)
            continue
        for stamp in stamps[:MAX_BACKUPS_PER_VM]:
            if not stamp.is_dir(follow_symlinks=False):
                continue
            manifest = backup_integrity.read_manifest(stamp.path)
            if not isinstance(manifest, dict) or manifest.get("vm") != vm_entry.name:
                continue
            if not any(f.get("role") == "disk" for f in manifest.get("files") or []):
                continue
            backups.append(_summary(Path(stamp.path), manifest, known))
        if backups:
            backups.sort(key=lambda b: b["created_at"] or "", reverse=True)
            vms.append({"vm": vm_entry.name, "backups": backups})

    return _mark_existing(vms)


def _mark_existing(vms):
    from app.core.libvirt_utils import open_conn

    conn = open_conn(readonly=True)
    try:
        local = {d.name() for d in conn.listAllDomains()}
    finally:
        conn.close()
    for vm in vms:
        vm["exists_here"] = vm["vm"] in local
    return vms


def _store_row(root):
    """The backup store of this cluster at `root`, and the store itself (with its key, an encrypted one's): a backup
    is restored from a store this cluster knows, never from one it was not given."""
    from app.core import backup_stores

    try:
        store_id = json.loads((Path(root) / backup_store.CONFIG).read_text())["id"]
    except (OSError, ValueError, KeyError):
        raise RecoveryError(f"{root} is not a backup store this node can read") from None
    row = next((r for r in _store().list_stores() if r["store_id"] == store_id), None)
    if row is None:
        raise RecoveryError(
            f"{root} is a backup store this cluster does not know: add it in Backups > Backup stores (it is taken "
            "over, with its key when it is encrypted), then scan it again"
        )
    try:
        # Its backups' manifests, read as they are: the restore opens the store with its key, in hyperlite-daemon.
        return row, backup_stores.open_store(row["name"], raw=True)
    except backup_stores.StoreError as e:
        raise RecoveryError(e.message) from None


def _store_summary(backup_dir, manifest, known, row):
    path = str(backup_dir)
    known_row = known.get(path)
    return {
        "path": path,
        "created_at": manifest.get("time"),
        "mode": manifest.get("mode") if manifest.get("mode") in ("live", "cold") else "cold",
        "firmware": None,  # in the backup's files, encrypted in an encrypted store: read by the restore
        "source": f"{row['name']}" + (f" (copy of {row['copy_of']})" if row.get("copy_of") else ""),
        "disks": len(manifest.get("disks") or {}),
        "size_bytes": sum(int(d.get("size") or 0) for d in (manifest.get("disks") or {}).values()),
        "backup_id": known_row["id"] if known_row else None,
    }


def _scan_store(root, known):
    """scan() of a backup store: its backups by VM name, newest first."""
    from app.core.vm_builder import validate_name

    row, store = _store_row(root)
    by_vm = {}
    for vm_uuid, stamp, manifest in store.backups():
        vm = manifest.get("vm") or ""
        if validate_name(vm) or not manifest.get("disks"):
            continue
        by_vm.setdefault(vm, []).append(_store_summary(store.backup_dir(vm_uuid, stamp), manifest, known, row))
    vms = []
    for vm in sorted(by_vm)[:MAX_VMS]:
        backups = sorted(by_vm[vm], key=lambda b: b["created_at"] or "", reverse=True)[:MAX_BACKUPS_PER_VM]
        vms.append({"vm": vm, "backups": backups})
    return vms


def _backup_dir(path):
    """A backup directory given by a client: absolute, no '..', no symbolic link anywhere, under an allowed root,
    holding a manifest of a backup with at least one disk."""
    backup_dir = _allowed(path)
    if len(backup_dir.parents) >= 3 and (backup_dir.parents[2] / backup_store.CONFIG).is_file():
        try:
            manifest = json.loads((backup_dir / "manifest.json").read_text())
        except (OSError, ValueError):
            manifest = None
        if not isinstance(manifest, dict) or not manifest.get("disks"):
            raise RecoveryError(f"No Hyperlite backup in {backup_dir}")
        return backup_dir, manifest
    manifest = backup_integrity.read_manifest(str(backup_dir))
    if not isinstance(manifest, dict) or not any(f.get("role") == "disk" for f in manifest.get("files") or []):
        raise RecoveryError(f"No Hyperlite backup in {backup_dir}")
    return backup_dir, manifest


def register(path, username="system"):
    """The catalogue id of the backup at `path`, added to this node's catalogue when it is not there yet."""
    from app.core.audit import log_action
    from app.core.vm_builder import validate_name

    backup_dir, manifest = _backup_dir(path)
    existing = _store().find_by_path(str(backup_dir))
    if existing:
        return existing["id"]
    vm = manifest.get("vm") or ""
    if validate_name(vm):
        raise RecoveryError("The backup's manifest names an invalid VM")
    if "disks" in manifest and isinstance(manifest["disks"], dict):  # a backup in a store
        row, _store_obj = _store_row(backup_dir.parents[2])
        summary = _store_summary(backup_dir, manifest, {}, row)
        backup_id = _store().register_imported(
            vm, str(backup_dir), summary["mode"], summary["created_at"] or "", summary["size_bytes"], None,
            summary["source"][:255], store=row["name"],
        )  # fmt: skip
        log_action(username, "import_backup", vm, "success", f"#{backup_id} from the store {row['name']}")
        return backup_id
    summary = _summary(backup_dir, manifest, {})
    disks = [f for f in manifest.get("files") or [] if f.get("role") == "disk"]
    checksum = disks[0].get("sha256") if len(disks) == 1 else None
    backup_id = _store().register_imported(
        vm,
        str(backup_dir),
        summary["mode"],
        summary["created_at"] or "",
        summary["size_bytes"],
        checksum,
        str(manifest.get("source") or "")[:255] or None,
    )
    log_action(username, "import_backup", vm, "success", f"#{backup_id} from {backup_dir}")
    return backup_id


def plan(items, network):
    """Check a recovery request before anything starts: valid paths and names, no name twice, no name already used
    here, an existing network. Returns [(backup path, new name)]."""
    from app.core.libvirt_utils import open_conn
    from app.core.vm_builder import validate_name

    if not items:
        raise RecoveryError("Choose at least one VM to restore")
    if len(items) > MAX_VMS:
        raise RecoveryError("Too many VMs in one recovery")
    pairs, names = [], set()
    for item in items:
        backup_dir, _manifest = _backup_dir(item.get("path") or "")
        name = (item.get("name") or "").strip()
        error = validate_name(name)
        if error:
            raise RecoveryError(f"{name or '(empty)'}: {error}")
        if name in names:
            raise RecoveryError(f"The name {name} is used twice")
        names.add(name)
        pairs.append((str(backup_dir), name))
    conn = open_conn(readonly=True)
    try:
        taken = sorted(names & {d.name() for d in conn.listAllDomains()})
        if network:
            try:
                conn.networkLookupByName(network)
            except Exception:
                raise RecoveryError(f"The network '{network}' does not exist on this node") from None
    finally:
        conn.close()
    if taken:
        raise RecoveryError(f"A VM already has these names here: {', '.join(taken)}")
    return pairs


def _run(task_id, pairs, network, username):
    from app.core.audit import log_action
    from app.core.backups import restore_backup

    done, failed = [], []
    for i, (path, name) in enumerate(pairs):
        try:
            backup_id = register(path, username)
            restore_backup(backup_id, "new", new_name=name, username=username, network=network)
            done.append(name)
        except Exception as e:
            # One VM that cannot be restored must not stop the others: the summary names it with its reason.
            logger.warning("Site recovery of %s failed", name, exc_info=True)
            failed.append(f"{name}: {e}")
        update_task_progress(task_id, int((i + 1) * 100 / len(pairs)))
    summary = f"{len(done)}/{len(pairs)} VM(s) restored"
    if failed:
        summary += "; failed: " + "; ".join(failed)
    finish_task(task_id, "succeeded" if not failed else "failed", None if not failed else summary)
    log_action(
        username, "site_recovery", ", ".join(n for _, n in pairs)[:200], "success" if not failed else "failed", summary
    )


def recover(items, network, username):
    """Start the restore of `items` ([{"path", "name"}]) as new VMs, one after another. Returns the task id."""
    from app.core.audit import log_action

    pairs = plan(items, network)
    task_id = create_task("site_recovery", f"{len(pairs)} VM(s)", username=username)
    log_action(username, "site_recovery_requested", f"{len(pairs)} VM(s)", "success")
    threading.Thread(target=_run, args=(task_id, pairs, network, username), daemon=True).start()
    return task_id
