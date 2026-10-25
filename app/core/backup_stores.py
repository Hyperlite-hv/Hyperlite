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
from app.core.safe_paths import safe_child

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
        self.message, self.status = message, status


def _repo():
    from app.repositories import registry

    return registry.backups().sync


# A daemon's refusal as the StoreError the routes answer (privileged.STATUS), and back (app/daemon/ops/backup.py).
_STATUS_OF_KIND = {"not_found": 404, "in_use": 409, "unreachable": 502, "unavailable": 503, "failed": 500}
KIND_OF_STATUS = {404: "not_found", 409: "in_use", 502: "unreachable", 503: "unavailable"}


def _daemon(op, timeout=None, **args):
    """A store's operation run by hyperlite-daemon, which alone reads a store's key and a remote's token
    (docs/design/privilege-separation.md, the backups domain, part 2): its result, or StoreError."""
    from app.core import daemon_client, privileged
    from app.daemon.registry import DaemonError

    try:
        return privileged.run(op, timeout=timeout or daemon_client.TIMEOUT_S, **args)
    except DaemonError as e:
        raise StoreError(e.detail, _STATUS_OF_KIND.get(e.kind, 422)) from None


def check(name):
    """The store `name` exists and this node reaches it, with its key when it is encrypted; StoreError otherwise."""
    _daemon("store.check", name=name)


def _now():
    return datetime.now(UTC).isoformat(timespec="seconds")


def _pool_dir(pool_name):
    """The directory of this node's pool `pool_name`, or StoreError when it is not here, not running, or holds no files."""
    from app.core.storage_pools import pool_path, pool_type

    conn = open_conn(readonly=True)
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
    return safe_child(_pool_dir(row["pool"]), _check_directory(row["directory"]))


def get(name):
    row = _repo().get_store(name)
    if not row:
        raise StoreError(f"No backup store '{name}'", 404)
    return row


def _key_of(row):
    """The key of an encrypted store, from the cluster's secrets; None for an unencrypted one."""
    if not row["encrypted"]:
        return None
    from app.core import secrets_crypto

    sealed = _repo().get_store_key(row["name"])
    if not sealed and row.get("copy_of"):
        raise StoreError(
            f"The copy '{row['name']}' keeps no key on this site: unlock it with its source's key to restore from it",
            409,
        )
    if not sealed:
        raise StoreError(f"The key of the backup store '{row['name']}' is missing from this cluster's secrets", 409)
    try:
        return backup_store.key_from_code(secrets_crypto.decrypt(sealed))
    except (secrets_crypto.SecretUnreadable, backup_store.StoreError):
        raise StoreError(f"The key of the backup store '{row['name']}' cannot be read on this node", 409) from None


def open_store(name, raw=False):
    """The store `name` as this node reaches it: a Store, with its key when it is encrypted, or StoreError (its pool
    is not here, or the directory found is not that store). raw: without its key, to copy its chunks as they are."""
    row = get(name)
    path = path_of(row)
    try:
        store = backup_store.Store(path, raw=True) if raw else backup_store.Store(path, _key_of(row))
    except backup_store.StoreError as e:
        raise StoreError(f"Backup store '{name}' cannot be read on this node: {e.message}", 409) from None
    if store.id != row["store_id"]:
        raise StoreError(
            f"{path} holds another backup store than '{name}': the pool is not the same share on this node", 409
        )
    return store


def open_root(root):
    """The store at `root` (a backup's <store>/vm/<uuid>/<time> three levels up), with its key: the store of this
    cluster whose id it holds. A store no row names is opened without a key (an unencrypted one only)."""
    try:
        store_id = json.loads((Path(root) / backup_store.CONFIG).read_text())["id"]
    except (OSError, ValueError, KeyError) as e:
        raise StoreError(f"{root} is not a backup store: {e}", 409) from None
    row = next((r for r in _repo().list_stores() if r["store_id"] == store_id), None)
    try:
        return backup_store.Store(root, _key_of(row) if row else None)
    except backup_store.StoreError as e:
        raise StoreError(e.message, 409) from None


def create(
    name, pool, directory=None, username="system", encrypted=False, master_public_key=None, key=None, copy_of=None
):
    """Make the store `name` in `directory` of `pool` (created when missing), or take over the store found there (a
    store made by another cluster, or before a reinstallation; an encrypted one with its `key`, as its code).

    encrypted: a new store's chunks and files are encrypted with a new key, kept in the cluster's secrets.
    master_public_key: the public half of a master recovery key (PEM): each backup then carries the store's key
    encrypted with it. copy_of: a new store made as the copy of that store of this cluster (backup_store_sync): its
    chunk size and its key, so chunks are copied as they are. Returns (row, the key's code for a new encrypted store,
    to be shown once, else None). hyperlite-daemon makes the store (store.make); its records are written here."""
    if not NAME.match(name or ""):
        raise StoreError("Invalid name: letters, digits, '.', '-' and '_', 63 characters at most")
    if _repo().get_store(name):
        raise StoreError(f"A backup store '{name}' already exists", 409)
    directory = _check_directory(directory or DEFAULT_DIRECTORY)
    made = _daemon(
        "store.make",
        name=name,
        pool=pool,
        directory=directory,
        encrypted=bool(encrypted),
        master_public_key=master_public_key,
        key=key,
        copy_of=copy_of,
    )
    if made["sealed_key"]:
        _repo().save_store_key(name, made["sealed_key"], _now())
    _repo().save_store(
        name, pool, directory, made["store_id"], 1 if made["encrypted"] else 0, username, _now(), made["master"]
    )
    if copy_of:
        if not made["alike"]:
            _repo().delete_store(name)
            raise StoreError(f"{made['path']} holds a store whose chunks are not named as those of '{copy_of}'")
        _repo().set_store_copy_of(name, copy_of)
    return get(name), made["shown"]


def make(name, pool, directory, encrypted=False, master_public_key=None, key=None, copy_of=None):
    """hyperlite-daemon's part of create() (store.make): the store made, or taken over, in the pool's directory, its
    key sealed with the cluster's secrets. {"path", "store_id", "encrypted", "sealed_key", "shown", "master",
    "alike"}: the records are the API's to write."""
    from app.core import backup_remotes

    remote = backup_remotes.split_source(copy_of)
    source = get(copy_of) if copy_of and not remote else None
    remote_config = None
    if remote:  # another cluster's store: its configuration read now, its key never here
        try:
            reader = backup_remotes.RemoteStore(*remote)
        except backup_remotes.RemoteError as e:
            raise StoreError(e.message, e.status) from None
        remote_config = reader.config
        reader.close()
    directory = _check_directory(directory)
    master = None
    if master_public_key:
        try:
            master = backup_store.check_public_key(master_public_key).decode()
        except backup_store.StoreError as e:
            raise StoreError(e.message) from None
    path = safe_child(_pool_dir(pool), directory)
    shown = None
    try:
        if (path / backup_store.CONFIG).exists():
            existing = json.loads((path / backup_store.CONFIG).read_text())
            secret = backup_store.key_from_code(key) if key else None
            if existing.get("encrypted") and secret is None:
                raise StoreError(f"{path} holds an encrypted store: give its key to take it over")
            store = backup_store.Store(path, secret)
            if any(r["store_id"] == store.id for r in _repo().list_stores()):
                raise StoreError(f"{path} is already a store of this cluster", 409)
        elif remote_config is not None:
            secret = None
            store = backup_store.create(path, like=remote_config)
        elif source is not None:
            secret = _key_of(source)
            store = backup_store.create(
                path, encrypted_with=secret, chunk_size=open_store(copy_of, raw=True).chunk_size
            )
        else:
            secret = backup_store.new_key() if encrypted else None
            store = backup_store.create(path, encrypted_with=secret)
            shown = backup_store.key_code(secret) if secret else None
    except backup_store.StoreError as e:
        raise StoreError(e.message) from None
    except (OSError, ValueError) as e:
        raise StoreError(f"{path} cannot be read as a store: {e}") from None
    if master and not store.encrypted:
        raise StoreError("A master recovery key is for an encrypted store")
    sealed = None
    if store.encrypted and secret is not None:
        from app.core import secrets_crypto

        sealed = secrets_crypto.encrypt(backup_store.key_code(secret))
    alike = True
    if source is not None:
        alike = store.same_chunks_as(open_store(copy_of, raw=True))
    elif remote_config is not None:
        alike = _alike(store.config, remote_config)
    return {
        "path": str(path),
        "store_id": store.id,
        "encrypted": bool(store.encrypted),
        "sealed_key": sealed,
        "shown": shown,
        "master": master,
        "alike": alike,
    }


def _alike(config, other):
    return (
        int(config["chunk_size"]) == int(other["chunk_size"])
        and bool(config.get("encrypted")) == bool(other.get("encrypted"))
        and config.get("key_fingerprint") == other.get("key_fingerprint")
    )


def unlock(name, code, username="system"):
    """Give an encrypted copy its key, the day its backups are to be restored here: the copy kept none until then
    (Antho, 2026-10-24). The key's fingerprint must be the store's. hyperlite-daemon checks and seals it
    (store.unlock); the sealed key is recorded here."""
    row = get(name)
    if not row["encrypted"]:
        raise StoreError(f"The store '{name}' is not encrypted: it needs no key")
    _repo().save_store_key(name, _daemon("store.unlock", name=name, code=code or "")["sealed_key"], _now())
    return get(name)


def seal_key(name, code):
    """hyperlite-daemon's part of unlock(): the key checked against the store, sealed with the cluster's secrets."""
    from app.core import secrets_crypto

    row = get(name)
    try:
        key = backup_store.key_from_code(code or "")
        backup_store.Store(path_of(row), key)
    except backup_store.StoreError as e:
        raise StoreError(e.message) from None
    return secrets_crypto.encrypt(backup_store.key_code(key))


def manifest_extra(name):
    """What a backup into the store `name` adds to its manifest: its key sealed with the master recovery key."""
    row = get(name)
    if not row.get("master_public_key"):
        return {}
    store = open_store(name)
    return {"recovery_key": store.sealed_key(row["master_public_key"].encode())}


def users_of(name):
    """What backs up into the store, or copies from or into it, in words: a store they need is never removed under
    them."""
    repo = _repo()
    users = [f"the schedule of {s['vm_name']}" for s in repo.list_schedules() if s.get("store") == name]
    users += [f"the grouped job {g['name']}" for g in repo.list_group_jobs() if g.get("store") == name]
    users += [f"the copy job {s['name']}" for s in repo.list_syncs() if name in (s["source"], s["target"])]
    users += [f"the store {r['name']}, its copy" for r in repo.list_stores() if r.get("copy_of") == name]
    return users


def remove(name):
    """Forget the store; its directory and backups stay on the pool, to be taken over again or deleted by hand (as
    Proxmox leaves a removed storage's data)."""
    get(name)
    users = users_of(name)
    if users:
        raise StoreError(f"Backup store '{name}' is used by {', '.join(users)}", 409)
    _repo().delete_store(name)


def describe(row):
    """A store's row with what this node sees of it: reachable, space, backups, read by hyperlite-daemon
    (store.describe: an encrypted store opens with its key)."""
    return _daemon("store.describe", name=row["name"])


def describe_here(row):
    """describe()'s work, hyperlite-daemon's."""
    out = {k: row[k] for k in ("name", "pool", "directory", "store_id", "created_by", "created_at")}
    out["encrypted"] = bool(row["encrypted"])
    out["master_key"] = bool(row.get("master_public_key"))
    out["copy_of"] = row.get("copy_of")
    # An encrypted copy of another cluster's store keeps no key until the day it is restored from (unlock).
    out["locked"] = bool(row["encrypted"]) and not _repo().get_store_key(row["name"])
    try:
        store = open_store(row["name"])
    except StoreError as e:
        return {**out, "available": False, "problem": e.message}
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


# A garbage collection reads every index of the store: generous, a large store takes hours.
GC_TIMEOUT_S = 24 * 3600


def collect_garbage(name):
    """Free the chunks no backup of the store uses any more, by hyperlite-daemon (store.gc). Returns what it did."""
    return _daemon("store.gc", timeout=GC_TIMEOUT_S, name=name)


def collect_garbage_here(name):
    """collect_garbage()'s work, hyperlite-daemon's."""
    from app.core import self_node

    store = open_store(name)
    with _cluster_lock(f"backup-store-gc-{store.id}"):
        try:
            result = store.collect_garbage()
        except BlockingIOError:
            raise StoreError("This store's garbage is already being collected on this node", 409) from None
        except backup_store.StoreError as e:
            raise StoreError(e.message, 409) from None
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
            store = open_store(name, raw=True)  # when it was last collected: no key needed
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
