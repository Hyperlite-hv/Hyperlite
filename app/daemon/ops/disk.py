"""The disks' root work (docs/design/privilege-separation.md, section 13, the disks domain). Part 1: this node's
libraries, the ISOs and the disks to import (app/core/disk_library.py, app/core/ova.py). Part 2: a VM's disk grown or
moved to another pool (app/core/disk_resize.py, app/core/disk_move.py), asked by the VM's name and the disk's target.
An upload is taken from the spool by its name, never a path; a library's files are named, checked again here. The
routes keep who may ask, the reads, the audit log, the tasks and their answers."""

import functools
import logging
import threading
from typing import Literal

from pydantic import Field

from app.core import disk_library
from app.daemon.needs import on_vm
from app.daemon.registry import Args, DaemonError, operation

logger = logging.getLogger(__name__)

FILE_NAME = 256


def _library(fn):
    """disk_library's refusals and failures keep their kind; an OVA it cannot read is a refusal with its reason."""

    @functools.wraps(fn)
    def wrapper(args, ctx):
        from app.core import ova

        try:
            return fn(args, ctx)
        except disk_library.LibraryError as e:
            raise DaemonError(e.kind, e.message) from e
        except ova.OvaError as e:
            raise DaemonError("refused", str(e)) from e

    return wrapper


def _refresh_pools(path):
    """A file written outside libvirt is listed by the pool that holds it once refreshed: best-effort, the file is
    stored whatever libvirt says."""
    import libvirt

    from app.core import libvirt_utils

    try:
        with libvirt_utils.local_conn() as conn:
            libvirt_utils.refresh_pools_for_paths(conn, [path])
    except libvirt.libvirtError as e:
        logger.warning("The pool holding %s was not refreshed: %s", path, e)


class StoreArgs(Args):
    library: Literal["iso", "import"]
    spool_name: str = Field(max_length=64)
    name: str = Field(min_length=1, max_length=FILE_NAME)
    replace: bool = False


@operation("disk.library_store", StoreArgs)
@_library
def library_store(args, ctx):
    """An upload moved from the spool into its library (disk_library.store): a regular file of its own only, root's,
    readable by QEMU. Returns its size in bytes."""
    size = disk_library.store(args.library, args.spool_name, args.name, replace=args.replace)
    if args.library == "iso":
        _refresh_pools(disk_library.ISOS / args.name)
    return {"size": size}


class RenameArgs(Args):
    name: str = Field(min_length=1, max_length=FILE_NAME)
    new_name: str = Field(min_length=1, max_length=FILE_NAME)


@operation("disk.library_rename", RenameArgs)
@_library
def library_rename(args, ctx):
    """An ISO renamed, refused while a VM has it in a CD-ROM drive, whatever the API checked."""
    from app.core import libvirt_utils

    with libvirt_utils.local_conn() as conn:
        target = disk_library.rename_iso(conn, args.name, args.new_name)
    _refresh_pools(target)


class DeleteArgs(Args):
    library: Literal["iso", "import"]
    name: str = Field(min_length=1, max_length=FILE_NAME)


@operation("disk.library_delete", DeleteArgs)
@_library
def library_delete(args, ctx):
    """A library's file deleted; an ISO a VM has in a CD-ROM drive is kept, whatever the API checked."""
    from app.core import libvirt_utils

    with libvirt_utils.local_conn() as conn:
        disk_library.delete(conn, args.library, args.name)


class OvaArgs(Args):
    ova: str = Field(min_length=1, max_length=FILE_NAME)
    stem: str = Field(min_length=1, max_length=128)


@operation("disk.ova_extract", OvaArgs)
@_library
def ova_extract(args, ctx):
    """An OVA's disks copied out among the disks to import, each checked against its manifest (ova.extract_disks).
    Returns their file names."""
    from app.core import ova
    from app.core.safe_paths import safe_child

    disk_library.check_name("import", args.ova)
    if not args.ova.lower().endswith(".ova"):
        raise DaemonError("refused", "Not an OVA file")
    path = safe_child(disk_library.IMPORTS, args.ova)
    if not path.is_file():
        raise DaemonError("not_found", f"File '{args.ova}' not found")
    try:
        return {"disks": ova.extract_disks(path, disk_library.IMPORTS, args.stem)}
    except OSError as e:
        from app.core.error_messages import describe_exception

        raise DaemonError("failed", f"The disks could not be extracted: {describe_exception(e)}") from e


# --- Part 2: a VM's disk, grown or moved to another pool, asked by the VM's name and the disk's target ---

TARGET = r"^[a-z]{2,4}[0-9]{0,2}$"  # the routes' own rule (app/routers/vms/_shared.py, TARGET_DEV_RE)
VM_NAME = 64
TASK_ID = 64
MAX_GB = 1 << 20

# The copies running in this process, by task: what disk.cancel stops.
_moves = {}
_moves_lock = threading.Lock()


def _kind(status):
    """disk_resize's and disk_move's statuses, as the daemon's kinds (the routes give back the same statuses)."""
    return {404: "not_found", 409: "in_use", 422: "refused"}.get(status, "failed")


def _domain(conn, vm):
    import libvirt

    try:
        return conn.lookupByName(vm)
    except libvirt.libvirtError:
        raise DaemonError("not_found", f"VM '{vm}' not found") from None


class ResizeArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    target: str = Field(pattern=TARGET)
    size_gb: int = Field(ge=1, le=MAX_GB)


@operation("disk.resize", ResizeArgs, needs=on_vm("vm.resize"))
def resize(args, ctx):
    """The disk grown (disk_resize.grow: only growing, never an iSCSI LUN, not under a block job), its replication
    checkpoints dropped once every check passed. The API forgets the replication chain when `checkpoints_dropped`."""
    from app.core import checkpoints, disk_resize, libvirt_utils

    dropped = []
    with libvirt_utils.local_conn() as conn:
        domain = _domain(conn, args.vm)
        try:
            old_bytes, new_bytes, live = disk_resize.grow(
                conn, domain, args.target, args.size_gb, before_change=lambda: dropped.append(checkpoints.drop(domain))
            )
        except disk_resize.ResizeError as e:
            raise DaemonError(_kind(e.status), e.message) from e
    return {"old_bytes": old_bytes, "new_bytes": new_bytes, "live": live, "checkpoints_dropped": sum(dropped)}


class MoveArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    target: str = Field(pattern=TARGET)
    pool: str = Field(min_length=1, max_length=64)
    delete_source: bool = False
    task_id: str = Field(min_length=1, max_length=TASK_ID)


@operation("disk.move", MoveArgs)
def move(args, ctx):
    """The disk copied into another directory or NFS pool and the VM switched to it, live or stopped (disk_move):
    checked again here, its replication checkpoints dropped, its progress written into the task. One call that waits;
    disk.cancel stops it. Returns where it went and `checkpoints_dropped`."""
    from app.core import checkpoints, disk_move, libvirt_utils
    from app.core.tasks import task_log, update_task_progress

    stop = threading.Event()
    with _moves_lock:
        if args.task_id in _moves:
            raise DaemonError("in_use", "This task already moves a disk")
        _moves[args.task_id] = stop
    try:
        with libvirt_utils.local_conn() as conn:
            domain = _domain(conn, args.vm)
            try:
                how = disk_move.plan(conn, domain, args.target, args.pool)
                dropped = checkpoints.drop(domain)
                task_log(
                    args.task_id, f"Copying {args.target} to {args.pool} ({'live' if how['live'] else 'VM stopped'})"
                )
                result = disk_move.move(
                    domain,
                    args.target,
                    how,
                    args.delete_source,
                    progress=lambda pct: update_task_progress(args.task_id, pct),
                    stop=stop,
                )
            except disk_move.MoveError as e:
                raise DaemonError(_kind(e.status), e.message) from e
    finally:
        with _moves_lock:
            _moves.pop(args.task_id, None)
    return {**result, "live": how["live"], "checkpoints_dropped": dropped}


class CancelArgs(Args):
    task_id: str = Field(min_length=1, max_length=TASK_ID)


@operation("disk.cancel", CancelArgs)
def cancel(args, ctx):
    """Stop the copy of this task, if one runs: the partial copy is removed, the VM stays on its source disk."""
    with _moves_lock:
        stop = _moves.get(args.task_id)
    if stop is not None:
        stop.set()
    return {"cancelled": stop is not None}
