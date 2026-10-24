"""The disks' root work (docs/design/privilege-separation.md, section 13, the disks domain). Part 1: this node's
libraries, the ISOs and the disks to import (app/core/disk_library.py, app/core/ova.py). An upload is taken from the
spool by its name, never a path; a library's files are named, checked again here. The routes keep who may ask, the
reads, the audit log, the tasks and their answers."""

import functools
import logging
from typing import Literal

from pydantic import Field

from app.core import disk_library
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
