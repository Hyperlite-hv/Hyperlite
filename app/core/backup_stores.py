"""The backup stores of the cluster (docs/design/backup-store.md): each lives in a directory of a storage pool, as a
Proxmox Backup Server datastore is added in Datacenter > Storage. The row is replicated (app/repositories/cfs/tables.py),
so every node knows the store; a node that mounts the pool reaches it at the same place, and the id written in the
store itself (.store.json) is checked against the row's, so a share mounted elsewhere is never taken for it.

Only a pool of files can hold a store (a directory, NFS, SMB): ZFS, LVM and iSCSI pools hold disks, not files.
"""

import contextlib
import json
import logging
import os
import re
import shutil
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import libvirt

from app.core import backup_store
from app.core.libvirt_utils import open_conn

logger = logging.getLogger(__name__)

NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$")
DEFAULT_DIRECTORY = "hyperlite-store"
FILE_POOLS = ("dir", "netfs", "fs")
GC_EVERY_DAYS = 7
_GC_LOCK_TTL_S = 600


class StoreError(Exception):
    """A store that cannot be made, found or used here, said to the person who asked."""

    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def _repo():
    from app.repositories import registry

    return registry.backups().sync


def _now():
    return datetime.now(UTC).isoformat(timespec="seconds")


def _pool_dir(pool_name):
    """The directory of this node's pool `pool_name`, or StoreError when it is not here, not running, or holds no files."""
    from app.core.storage_pools import pool_path, pool_type

    conn = open_conn()
    try:
        try:
            pool = conn.storagePoolLookupByName(pool_name)
        except libvirt.libvirtError:
            raise StoreError(f"The storage pool '{pool_name}' is not on this node", 404) from None
        kind = pool_type(pool)
        if kind not in FILE_POOLS:
            raise StoreError(
                f"The storage pool '{pool_name}' ({kind}) holds disks, not files: a store needs a directory, NFS or "
                "SMB pool"
            )
        if not pool.isActive():
            raise StoreError(f"The storage pool '{pool_name}' is not running on this node", 409)
        path = pool_path(pool)
    finally:
        conn.close()
    if not path or not os.path.isabs(path):
        raise StoreError(f"The storage pool '{pool_name}' has no directory", 409)
    return Path(path)


def _check_directory(directory):
    if not NAME.match(directory or ""):
        raise StoreError("Invalid directory: letters, digits, '.', '-' and '_', inside the pool")
    return directory


def path_of(row):
    return _pool_dir(row["pool"]) / row["directory"]


def get(name):
    row = _repo().get_store(name)
    if not row:
        raise StoreError(f"No backup store '{name}'", 404)
    return row


def open_store(name):
    """The store `name` as this node reaches it: a Store, or StoreError (its pool is not here, or the directory found
    is not that store)."""
    row = get(name)
    path = path_of(row)
    try:
        store = backup_store.Store(path)
    except backup_store.StoreError as e:
        raise StoreError(f"Backup store '{name}' cannot be read on this node: {e}", 409) from None
    if store.id != row["store_id"]:
        raise StoreError(
            f"{path} holds another backup store than '{name}': the pool is not the same share on this node", 409
        )
    return store


def create(name, pool, directory=None, username="system"):
    """Make the store `name` in `directory` of `pool` (created when missing), or take over the store found there (a
    store made by another cluster, or before a reinstallation). Returns its row."""
    if not NAME.match(name or ""):
        raise StoreError("Invalid name: letters, digits, '.', '-' and '_', 63 characters at most")
    if _repo().get_store(name):
        raise StoreError(f"A backup store '{name}' already exists", 409)
    directory = _check_directory(directory or DEFAULT_DIRECTORY)
    path = _pool_dir(pool) / directory
    if (path / backup_store.CONFIG).exists():
        try:
            store = backup_store.Store(path)
        except backup_store.StoreError as e:
            raise StoreError(str(e)) from None
        if any(r["store_id"] == store.id for r in _repo().list_stores()):
            raise StoreError(f"{path} is already a store of this cluster", 409)
    else:
        try:
            store = backup_store.create(path)
        except backup_store.StoreError as e:
            raise StoreError(str(e)) from None
    _repo().save_store(name, pool, directory, store.id, 1 if store.encrypted else 0, username, _now())
    return get(name)


def users_of(name):
    """The jobs that back up into the store: a store they need is never removed under them."""
    repo = _repo()
    vms = [s["vm_name"] for s in repo.list_schedules() if s.get("store") == name]
    groups = [g["name"] for g in repo.list_group_jobs() if g.get("store") == name]
    return vms, groups


def remove(name):
    """Forget the store; its directory and backups stay on the pool, to be taken over again or deleted by hand (as
    Proxmox leaves a removed storage's data)."""
    get(name)
    vms, groups = users_of(name)
    if vms or groups:
        named = ", ".join([*(f"the schedule of {v}" for v in vms), *(f"the grouped job {g}" for g in groups)])
        raise StoreError(f"Backup store '{name}' is used by {named}", 409)
    _repo().delete_store(name)


def describe(row):
    """A store's row with what this node sees of it: reachable, space, backups."""
    out = {k: row[k] for k in ("name", "pool", "directory", "store_id", "created_by", "created_at")}
    out["encrypted"] = bool(row["encrypted"])
    try:
        store = open_store(row["name"])
    except StoreError as e:
        return {**out, "available": False, "problem": str(e)}
    usage = shutil.disk_usage(store.root)
    return {
        **out,
        "available": True,
        "path": str(store.root),
        "total_bytes": usage.total,
        "free_bytes": usage.free,
        "backups": len(store.backups()),
        "last_gc": last_gc(store),
    }


LAST_GC = ".last-gc"  # in the store itself: every node that shares it sees when it was last collected, and by whom


def last_gc(store):
    try:
        return json.loads((store.root / LAST_GC).read_text())
    except (OSError, ValueError):
        return None


@contextlib.contextmanager
def _cluster_lock(lock_name):
    """One garbage collection of a store at a time in the whole cluster: hyperlite-cfs's lock, renewed while it runs
    (a large store takes hours). Outside a cluster the store's own lock file is enough."""
    from app.core import cluster_lead, self_node
    from app.core.cfs_client import Locked

    if not cluster_lead.in_cluster():
        yield
        return
    client = cluster_lead._shadow()._get_client()
    owner = self_node.name()
    try:
        client.lock(lock_name, owner, _GC_LOCK_TTL_S)
    except Locked:
        raise StoreError("Another node is collecting this store's garbage", 409) from None
    stop = threading.Event()

    def renew():
        while not stop.wait(_GC_LOCK_TTL_S / 3):
            try:
                client.lock(lock_name, owner, _GC_LOCK_TTL_S)
            except Exception as e:  # the collection goes on; the next renewal tries again
                logger.warning("Lock %s not renewed: %s", lock_name, e)

    thread = threading.Thread(target=renew, daemon=True, name="store-gc-lock")
    thread.start()
    try:
        yield
    finally:
        stop.set()
        with contextlib.suppress(Exception):
            client.unlock(lock_name, owner)


def collect_garbage(name):
    """Free the chunks no backup of the store uses any more. Returns what it did."""
    from app.core import self_node

    store = open_store(name)
    with _cluster_lock(f"backup-store-gc-{store.id}"):
        try:
            result = store.collect_garbage()
        except BlockingIOError:
            raise StoreError("This store's garbage is already being collected on this node", 409) from None
        except backup_store.StoreError as e:
            raise StoreError(str(e), 409) from None
    record = {"at": _now(), "node": self_node.name(), **result}
    backup_store._write_atomic(store.root / LAST_GC, json.dumps(record).encode())
    return result


_collecting = set()
_collecting_lock = threading.Lock()


def collect_due():
    """The scheduler's weekly collection: each store reachable here, not collected for GC_EVERY_DAYS, in a thread of
    its own (a large store takes hours; backups go on meanwhile). In a cluster, the first node to take the store's
    lock collects it; the others find it done."""
    limit = (datetime.now(UTC) - timedelta(days=GC_EVERY_DAYS)).isoformat()
    for row in _repo().list_stores():
        name = row["name"]
        with _collecting_lock:
            if name in _collecting:
                continue
        try:
            store = open_store(name)
        except StoreError:
            continue  # its pool is not on this node
        done = last_gc(store)
        if done and done.get("at", "") > limit:
            continue
        with _collecting_lock:
            _collecting.add(name)

        def run(name=name):
            from app.core.audit import log_action

            try:
                result = collect_garbage(name)
                freed = result["freed_bytes"] / 1024**3
                log_action(
                    "scheduler", "backup_store_gc", name, "success", f"{result['removed']} chunks, {freed:.1f} GiB"
                )
            except StoreError as e:
                if e.status != 409:  # another node collects it: nothing to say
                    log_action("scheduler", "backup_store_gc", name, "failed", str(e))
            except Exception as e:
                logger.exception("Garbage collection of the backup store %s failed", name)
                log_action("scheduler", "backup_store_gc", name, "failed", str(e)[:300])
            finally:
                with _collecting_lock:
                    _collecting.discard(name)

        threading.Thread(target=run, daemon=True, name=f"store-gc-{name}").start()
