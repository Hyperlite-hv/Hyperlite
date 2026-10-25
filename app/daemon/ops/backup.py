"""The backups (docs/design/privilege-separation.md, section 13, the backups domain, part 1a): a directory backup, its
restore, verification and deletion, and a VM's export, each run whole by the daemon as vzdump runs as root in a task
of pvedaemon. An operation names the VM and the backup's directory, which the daemon checks itself
(app/core/backups.py: backup_target, backup_dir), never trusting the records the API writes. The API keeps the
routes, the schedules, the retention decisions, the records, the locks and the audit log."""

from typing import Literal

from pydantic import Field

from app.daemon.registry import Args, DaemonError, Handover, operation

VM_NAME = 64
PATH = 4096
TASK_ID = 64


def _job(fn, *args, **kwargs):
    """A whole job's failure, its sentence for the task; a directory the daemon does not accept, refused."""
    import libvirt

    from app.core.backup_store_vm import BackupFailed
    from app.core.error_messages import describe_exception
    from app.core.tasks import TaskCancelled

    try:
        return fn(*args, **kwargs)
    except ValueError as e:
        raise DaemonError("refused", str(e)) from None
    except (TaskCancelled, BackupFailed) as e:
        raise DaemonError("failed", str(e)) from None
    except libvirt.libvirtError as e:
        raise DaemonError("failed", describe_exception(e)) from e
    except (RuntimeError, OSError) as e:
        raise DaemonError("failed", str(e)) from e


def _owned(task_id):
    """The task's cancellation, registered in this process while the job runs (backup.cancel)."""
    from contextlib import contextmanager

    from app.core import tasks

    @contextmanager
    def scope():
        tasks.register_cancel(task_id)
        try:
            yield
        finally:
            tasks.unregister_cancel(task_id)

    return scope()


class RunArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    target: str | None = Field(default=None, max_length=PATH)
    task_id: str = Field(min_length=1, max_length=TASK_ID)


# One call that waits: a backup copies for as long as its disks take.
@operation("backup.run", RunArgs)
def run(args, ctx):
    """A directory backup of a VM of this node, cold or live, under a target backups.backup_target accepts: {"path",
    "mode", "size", "checksum"}, the API records them."""
    from app.core import backups

    target = _job(backups.backup_target, args.target)
    with _owned(args.task_id):
        return _job(backups.copy_vm, args.vm, target, args.task_id)


class VerifyArgs(Args):
    path: str = Field(min_length=1, max_length=PATH)
    checksum: str | None = Field(default=None, max_length=128)
    task_id: str = Field(min_length=1, max_length=TASK_ID)
    span: int = Field(100, ge=1, le=100)


@operation("backup.verify", VerifyArgs)
def verify(args, ctx):
    """Every file's checksum and a qemu-img check of each image, of a backup backups.checked_backup accepts:
    {"status", "problems"}, the API records them."""
    from app.core import backups

    return _job(backups.verify_files, args.path, args.checksum, args.task_id, span=args.span)


class RestoreArgs(Args):
    path: str = Field(min_length=1, max_length=PATH)
    store: bool = False
    source_vm: str = Field(min_length=1, max_length=VM_NAME)
    mode: Literal["overwrite", "new"]
    vm: str = Field(max_length=VM_NAME)
    network: str | None = Field(default=None, min_length=1, max_length=64)
    task_id: str = Field(min_length=1, max_length=TASK_ID)
    check_only: bool = False


@operation("backup.restore", RestoreArgs)
def restore(args, ctx):
    """A backup restored over the stopped VM `vm` or as the new VM `vm` (backups.restore_files); `check_only`
    answers its refusals before anything is read. {"restored", "os_label", "ssh_user", "checkpoints_dropped"}."""
    from app.core import backups

    with _owned(args.task_id):
        return _job(
            backups.restore_files,
            args.path,
            args.store,
            args.source_vm,
            args.mode,
            args.vm,
            args.network,
            args.task_id,
            check_only=args.check_only,
        )


class DeleteArgs(Args):
    path: str = Field(min_length=1, max_length=PATH)


@operation("backup.delete", DeleteArgs)
def delete(args, ctx):
    """A backup's files: its directory when it holds a backup's files only (backups.delete_backup_files), or its
    index in a store."""
    from app.core import backups

    _job(backups.delete_backup_files, args.path)


class CancelArgs(Args):
    task_id: str = Field(min_length=1, max_length=TASK_ID)


@operation("backup.cancel", CancelArgs)
def cancel(args, ctx):
    """Stop the copy a backup's, an export's or a restore's task is running here: {"cancelled"}."""
    from app.core import tasks

    return {"cancelled": tasks.abort_here(args.task_id, ctx.user or "?")}


class ExportArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    task_id: str = Field(min_length=1, max_length=TASK_ID)


@operation("vm.export", ExportArgs)
def export(args, ctx):
    """A VM's system disk copied, cold or live, into the exports directory: {"name", "size_bytes"}."""
    from app.core import vm_export

    with _owned(args.task_id):
        return _job(vm_export.export_copy, args.vm, args.task_id)


class ExportNameArgs(Args):
    name: str = Field(min_length=1, max_length=255)


@operation("vm.export_delete", ExportNameArgs)
def export_delete(args, ctx):
    """An export of the exports directory deleted: {"deleted"}, False when there is none by that name."""
    from app.core import vm_export

    return {"deleted": _job(vm_export.delete_export, args.name)}


# --- Part 1b: the replication's points ---


class PointArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    target: str = Field(max_length=PATH)
    # The chain's state as the API recorded it: names make_point checks, never paths.
    previous_target: str | None = Field(default=None, max_length=PATH)
    previous: str | None = Field(default=None, max_length=32)
    checkpoint: str | None = Field(default=None, max_length=64)
    chain: str | None = Field(default=None, max_length=32)
    points: int = Field(0, ge=0, le=100000)
    disks_mtime: float | None = None


# One call that waits: a full point copies the whole VM.
@operation("replication.point", PointArgs)
def replication_point(args, ctx):
    """The next replication point of a VM of this node under the other site's directory (replication.make_point):
    {"kind", and the chain's new state}, the API records it."""
    from app.core import replication

    try:
        return _job(
            replication.make_point,
            args.vm,
            args.target,
            args.previous_target,
            args.previous,
            args.checkpoint,
            args.chain,
            args.points,
            args.disks_mtime,
        )
    except replication.ReplicationError as e:
        raise DaemonError("failed", str(e)) from None


# --- Part 2: the backup stores ---

STORE_NAME = 63


def _store_job(fn, *args, **kwargs):
    """A store's refusal (StoreError, SyncError, RemoteError: a message and a status) as the daemon's kind."""
    from app.core import backup_remotes, backup_store_sync, backup_stores

    try:
        return _job(fn, *args, **kwargs)
    except (backup_stores.StoreError, backup_store_sync.SyncError, backup_remotes.RemoteError) as e:
        raise DaemonError(backup_stores.KIND_OF_STATUS.get(e.status, "refused"), e.message) from None


class StoreArgs(Args):
    name: str = Field(min_length=1, max_length=STORE_NAME)


@operation("store.check", StoreArgs)
def store_check(args, ctx):
    """The store exists and this node reaches it, with its key when it is encrypted."""
    from app.core import backup_stores

    _store_job(backup_stores.open_store, args.name)


@operation("store.describe", StoreArgs)
def store_describe(args, ctx):
    """What this node sees of a store: reachable, space, backups, its last garbage collection."""
    from app.core import backup_stores

    return _store_job(lambda: backup_stores.describe_here(backup_stores.get(args.name)))


@operation("store.gc", StoreArgs)
def store_gc(args, ctx):
    """The chunks no backup of the store uses any more, freed: what it did."""
    from app.core import backup_stores

    return _store_job(backup_stores.collect_garbage_here, args.name)


class MakeArgs(Args):
    name: str = Field(min_length=1, max_length=STORE_NAME)
    pool: str = Field(min_length=1, max_length=64)
    directory: str = Field(min_length=1, max_length=63)
    encrypted: bool = False
    master_public_key: str | None = Field(default=None, max_length=8192)
    key: str | None = Field(default=None, max_length=512)
    copy_of: str | None = Field(default=None, max_length=2 * STORE_NAME + 1)


@operation("store.make", MakeArgs)
def store_make(args, ctx):
    """A store made, or taken over, in a directory of a pool of this node, its key sealed with the cluster's
    secrets (backup_stores.make): the API writes the records."""
    from app.core import backup_stores

    return _store_job(
        backup_stores.make,
        args.name,
        args.pool,
        args.directory,
        encrypted=args.encrypted,
        master_public_key=args.master_public_key,
        key=args.key,
        copy_of=args.copy_of,
    )


class UnlockArgs(StoreArgs):
    code: str = Field(max_length=512)


@operation("store.unlock", UnlockArgs)
def store_unlock(args, ctx):
    """An encrypted copy's key, checked against the store and sealed: {"sealed_key"}, the API records it."""
    from app.core import backup_stores

    return {"sealed_key": _store_job(backup_stores.seal_key, args.name, args.code)}


class StoreBackupArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    store: str = Field(min_length=1, max_length=STORE_NAME)
    task_id: str = Field(min_length=1, max_length=TASK_ID)


# One call that waits: a first backup into a store reads every disk.
@operation("store.backup", StoreBackupArgs)
def store_backup(args, ctx):
    """A VM of this node backed up into a store, only the chunks it lacks written: {"path", "size", "mode"}."""
    from app.core import backups

    with _owned(args.task_id):
        return _store_job(backups.copy_into_store, args.vm, args.store, args.task_id)


class ImportArgs(Args):
    path: str = Field(min_length=1, max_length=PATH)
    created_at: str = Field(min_length=1, max_length=64)
    vm: str = Field(min_length=1, max_length=VM_NAME)
    # A store keeps a VM's backups under its UUID, a directory's name: a UUID, never a path.
    vm_uuid: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    store: str = Field(min_length=1, max_length=STORE_NAME)
    task_id: str = Field(min_length=1, max_length=TASK_ID)


@operation("store.import", ImportArgs)
def store_import(args, ctx):
    """A directory backup (a directory backups.backup_dir accepts) copied into a store: {"path", "size"}."""
    from app.core import backups

    return _store_job(backups.import_here, args.path, args.created_at, args.vm, args.vm_uuid, args.store, args.task_id)


class SyncArgs(Args):
    job_id: int = Field(ge=1)


# One call that waits: a copy job copies whole stores.
@operation("store.sync", SyncArgs)
def store_sync(args, ctx):
    """A copy job run (backup_store_sync.run_job), the job read from the records by its id: what it did."""
    from app.core import backup_store_sync

    return _store_job(backup_store_sync.run_job_by_id, args.job_id)


class RemoteAddArgs(Args):
    address: str = Field(min_length=1, max_length=254)
    port: int = Field(ge=1, le=65535)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    token: str = Field(min_length=1, max_length=4096)


@operation("remote.add", RemoteAddArgs)
def remote_add(args, ctx):
    """Another cluster checked (its certificate, its token accepted) and its token sealed with its address and
    certificate: {"sealed"}, the API records it, and the daemon only ever sends the token there."""
    from app.core import backup_remotes

    return {"sealed": _store_job(backup_remotes.check_and_seal, args.address, args.port, args.fingerprint, args.token)}


class RemoteArgs(Args):
    name: str = Field(min_length=1, max_length=STORE_NAME)


@operation("remote.stores", RemoteArgs)
def remote_stores(args, ctx):
    """The stores another cluster's token may read: {"stores"}."""
    from app.core import backup_remotes

    return {"stores": _store_job(backup_remotes.stores_here, args.name)}


# --- Part 3: the file restore ---


def _restore_job(fn, *args):
    from app.core import file_restore

    try:
        return fn(*args)
    except file_restore.RestoreError as e:
        raise DaemonError("refused", str(e)) from None


class RestoreOpenArgs(Args):
    backup_id: int = Field(ge=1)
    path: str = Field(min_length=1, max_length=PATH)
    username: str = Field(min_length=1, max_length=64)


@operation("restore.open", RestoreOpenArgs)
def restore_open(args, ctx):
    """A browsing session on a directory backup (file_restore.open_here): libguestfs opens its images read-only.
    {"session", "filesystems"}."""
    from app.core import file_restore

    session = _restore_job(file_restore.open_here, args.backup_id, args.path, args.username)
    return {"session": session.id, "filesystems": session.filesystems}


class SessionArgs(Args):
    session: str = Field(min_length=1, max_length=64)
    username: str = Field(min_length=1, max_length=64)


class PathArgs(SessionArgs):
    device: str = Field(min_length=1, max_length=256)
    path: str = Field(min_length=1, max_length=4096)


@operation("restore.ls", PathArgs)
def restore_ls(args, ctx):
    """A folder of the backup's file system: {"entries"}."""
    from app.core import file_restore

    session = _restore_job(file_restore.session_here, args.session, args.username)
    return _restore_job(session.request, {"op": "ls", "device": args.device, "path": args.path})


@operation("restore.export", PathArgs)
def restore_export(args, ctx):
    """A file of the backup, or a folder as a .tar.gz, handed to the API as an open descriptor: {"name"}."""
    from app.core import file_restore

    session = _restore_job(file_restore.session_here, args.session, args.username)
    fd, name = _restore_job(file_restore.export_here, session, args.device, args.path)
    return Handover({"name": name}, [fd])


@operation("restore.close", SessionArgs)
def restore_close(args, ctx):
    from app.core import file_restore

    _restore_job(file_restore.close_here, args.session, args.username)


class BusyArgs(Args):
    backup_id: int = Field(ge=1)


@operation("restore.busy", BusyArgs)
def restore_busy(args, ctx):
    """Whether a browsing session reads this backup: {"open"}."""
    from app.core import file_restore

    return {"open": file_restore.is_open_here(args.backup_id)}
