"""The backups (docs/design/privilege-separation.md, section 13, the backups domain, part 1a): a directory backup, its
restore, verification and deletion, and a VM's export, each run whole by the daemon as vzdump runs as root in a task
of pvedaemon. An operation names the VM and the backup's directory, which the daemon checks itself
(app/core/backups.py: backup_target, backup_dir), never trusting the records the API writes. The API keeps the
routes, the schedules, the retention decisions, the records, the locks and the audit log."""

from typing import Literal

from pydantic import Field

from app.daemon.registry import Args, DaemonError, operation

VM_NAME = 64
PATH = 4096
TASK_ID = 64


def _job(fn, *args, **kwargs):
    """A whole job's failure, its sentence for the task; a directory the daemon does not accept, refused."""
    import libvirt

    from app.core.error_messages import describe_exception
    from app.core.tasks import TaskCancelled

    try:
        return fn(*args, **kwargs)
    except ValueError as e:
        raise DaemonError("refused", str(e)) from None
    except TaskCancelled as e:
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
