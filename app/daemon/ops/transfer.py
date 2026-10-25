"""A migration between clusters' root work (docs/design/multi-cluster.md, section 9.3; app/core/site_transfer.py).
At the source: a stopped VM's specification, and a pipe its disk comes out of. At the target: an incoming VM's
volumes, a pipe its disk goes into, its definition and its cancellation. An operation names a VM, a disk, a pool, an
incoming; never a path or XML: the VM's disks come from its definition, an incoming's volumes from the daemon's own
record. A prefix of its own (`transfer.`), apart from vm.migrate's within a cluster.

The privileges, for hyperlite-daemon's checks (#511, step 4): the source's operations need vm.migrate on the VM (an
administrator's, as POST /vms/{name}/migrate), the target's vm.allocate and the pools' space (an administrator's)."""

import functools
from typing import Annotated, Literal

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


class DescribeArgs(VmArgs):
    live: bool = False


@operation("transfer.describe", DescribeArgs)
@_transfer
def describe(args, conn):
    """The VM as it would go: {"spec", "left", "allocated", "cpu"}; refused when it has a snapshot or a device of this
    host, or runs (stopped, for `live`)."""
    from app.core import site_transfer

    return site_transfer.describe(conn, args.vm, args.live)


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
    """{"size"} and the write end of a pipe the incoming VM's disk goes into, as frames."""
    from app.core import site_transfer

    fd, size = site_transfer.import_disk(args.incoming, args.dev)
    return Handover({"size": size}, [fd])


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


# ---- A live migration (docs/design/multi-cluster.md, section 9.2; app/core/site_live.py) ----


class CpuArgs(Args):
    cpu_xml: str = Field(min_length=1, max_length=64 * 1024)


@operation("transfer.cpu_check", CpuArgs)
@_transfer
def cpu_check(args, conn):
    """Whether this host can run a VM with this <cpu>: {"compatible", "detail"}."""
    from app.core import site_live

    return site_live.cpu_check(conn, args.cpu_xml)


class LivePrepareArgs(IncomingArgs):
    networks: list[NAME] = Field(default_factory=list, max_length=16)


@operation("transfer.live_prepare", LivePrepareArgs)
@_transfer
def live_prepare(args, conn):
    """The prepared incoming VM made ready to arrive running: {"sockets", "volumes"}."""
    from app.core import site_live

    return site_live.prepare(conn, args.incoming, args.networks)


@operation("transfer.live_expect", IncomingArgs)
@_transfer
def live_expect(args, conn):
    """What the relay accepts for this incoming VM (site_relay.check_destination_xml)."""
    from app.core import site_live

    return site_live.expected(conn, args.incoming)


class ChannelArgs(IncomingArgs):
    kind: Literal["libvirt", "memory", "disks"]


@operation("transfer.live_connect", ChannelArgs)
@_transfer
def live_connect(args, conn):
    """A socket connected to this host's end of a channel: its libvirt (the API puts the relay between), or the
    target QEMU's migration stream or NBD server."""
    from app.core import site_live

    sock = site_live.connect(args.incoming, args.kind)
    return Handover({}, [sock.detach()])


@operation("transfer.live_define", IncomingArgs)
@_transfer
def live_define(args, conn):
    """The VM that arrived running defined here, from what it runs: {"name"}."""
    from app.core import site_live

    return site_live.define(conn, args.incoming)


@operation("transfer.live_listen", IncomingArgs)
def live_listen(args, ctx):
    """The source's three listening sockets (libvirt, memory, disks), for the API to accept on."""
    from app.core import site_live, site_transfer

    try:
        return Handover({"kinds": list(site_live.KINDS)}, site_live.listen(args.incoming))
    except site_transfer.TransferError as e:
        raise DaemonError(e.kind, e.detail) from e


class LiveRunArgs(IncomingArgs):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    volumes: list[Annotated[str, Field(min_length=1, max_length=4096)]] = Field(min_length=1, max_length=16)
    networks: list[NAME] = Field(default_factory=list, max_length=16)
    limit_mib: int | None = Field(None, ge=1, le=100000)
    task_id: str | None = Field(None, max_length=64)


@operation("transfer.live_run", LiveRunArgs)
@_transfer
def live_run(args, conn):
    """libvirt's live migration of the running VM through this migration's sockets, its progress in the task: one
    call that waits; transfer.live_abort stops it (the VM keeps running here). The volumes are the target's paths,
    which only its relay judges."""
    from app.core import site_live
    from app.core.tasks import update_task_progress

    progress = (lambda p: update_task_progress(args.task_id, p)) if args.task_id else None
    return site_live.run(conn, args.vm, args.incoming, args.volumes, args.networks, args.limit_mib, progress)


@operation("transfer.live_abort", IncomingArgs)
def live_abort(args, ctx):
    from app.core import site_live, site_transfer

    try:
        site_live.abort(args.incoming)
    except site_transfer.TransferError as e:
        raise DaemonError(e.kind, e.detail) from e


@operation("transfer.live_close", IncomingArgs)
def live_close(args, ctx):
    """The migration's sockets removed, at either end."""
    from app.core import site_live

    site_live.close(args.incoming)
