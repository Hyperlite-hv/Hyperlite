"""A migration between clusters' root work (docs/design/multi-cluster.md, section 9.3; app/core/site_transfer.py).
At the source: a stopped VM's specification, and a pipe its disk comes out of. At the target: an incoming VM's
volumes, a pipe its disk goes into, its definition and its cancellation. An operation names a VM, a disk, a pool, an
incoming; never a path or XML: the VM's disks come from its definition, an incoming's volumes from the daemon's own
record. A prefix of its own (`transfer.`), apart from vm.migrate's within a cluster.

The privileges, for hyperlite-daemon's checks (#511, step 4): the source's operations need vm.migrate on the VM (an
administrator's, as POST /vms/{name}/migrate), the target's vm.allocate and the pools' space (an administrator's)."""

import functools
from typing import Annotated

from pydantic import Field

from app.daemon.registry import Args, DaemonError, Handover, operation

VM_NAME = 64
DEV = Field(min_length=3, max_length=4, pattern=r"^(sd|vd|hd)[a-z]{1,2}$")
INCOMING = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
NAME = Annotated[str, Field(min_length=1, max_length=128)]


def _transfer(fn):
    """site_transfer's refusals with their kinds, libvirt's failures as "failed"."""

    @functools.wraps(fn)
    def wrapper(args, ctx):
        import libvirt

        from app.core import libvirt_utils, site_transfer
        from app.core.error_messages import describe_exception

        try:
            with libvirt_utils.local_conn() as conn:
                return fn(args, conn)
        except site_transfer.TransferError as e:
            raise DaemonError(e.kind, e.detail) from e
        except libvirt.libvirtError as e:
            raise DaemonError("failed", describe_exception(e)) from e

    return wrapper


class VmArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)


class ExportArgs(VmArgs):
    dev: str = DEV


@operation("transfer.describe", VmArgs)
@_transfer
def describe(args, conn):
    """The stopped VM as it would go: {"spec", "left", "allocated"}; refused when it runs, has a snapshot or a device of
    this host."""
    from app.core import site_transfer

    return site_transfer.describe(conn, args.vm)


@operation("transfer.export", ExportArgs)
@_transfer
def export(args, conn):
    """{"size"} and the read end of a pipe the stopped VM's disk comes out of, as frames."""
    from app.core import site_transfer

    fd, size = site_transfer.export(conn, args.vm, args.dev)
    return Handover({"size": size}, [fd])


class PrepareArgs(Args):
    spec: dict  # checked field by field by site_spec.check before anything is made
    pools: list[NAME] = Field(min_length=1, max_length=16)
    allocated: list[Annotated[int, Field(ge=0, le=64 * 1024**4)]] = Field(min_length=1, max_length=16)


@operation("transfer.prepare", PrepareArgs)
@_transfer
def prepare(args, conn):
    """An incoming VM: {"incoming", "disks"}, a new volume for each disk in the pool chosen for it."""
    from app.core import site_transfer

    return site_transfer.prepare(conn, args.spec, args.pools, args.allocated)


class ImportArgs(Args):
    incoming: str = INCOMING
    dev: str = DEV


@operation("transfer.import", ImportArgs)
@_transfer
def import_disk(args, conn):
    """The write end of a pipe the incoming VM's disk goes into, as frames."""
    from app.core import site_transfer

    return Handover({}, [site_transfer.import_disk(args.incoming, args.dev)])


class DefineArgs(Args):
    incoming: str = INCOMING
    networks: list[NAME] = Field(default_factory=list, max_length=16)
    start: bool = False


@operation("transfer.define", DefineArgs)
@_transfer
def define(args, conn):
    """The incoming VM defined once its disks were written, from its checked specification: {"name", "started"}."""
    from app.core import site_transfer

    return site_transfer.define(conn, args.incoming, args.networks, args.start)


class IncomingArgs(Args):
    incoming: str = INCOMING


@operation("transfer.abort", IncomingArgs)
@_transfer
def abort(args, conn):
    """The incoming VM cancelled: its exports stopped, its volumes removed."""
    from app.core import site_transfer

    site_transfer.abort(conn, args.incoming)
