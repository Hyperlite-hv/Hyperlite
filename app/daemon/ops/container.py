"""The containers' root work (docs/design/privilege-separation.md, section 13, the containers domain). Part 1: an
existing container started, stopped, its settings changed, renamed, deleted (app/core/container_ops.py), asked by its
name. Its filesystem is found in its definition and accepted in Hyperlite's container directories only. The routes
keep who may ask, the container's lock, its records and their answers."""

import functools

from pydantic import Field, field_validator

from app.daemon.needs import on_container
from app.daemon.registry import Args, DaemonError, operation

NAME = 64


def _container(fn):
    """container_ops' refusals and failures keep their kind; libvirt's own failure is "failed" with its reason."""

    @functools.wraps(fn)
    def wrapper(args, ctx):
        import libvirt

        from app.core import container_ops
        from app.core.error_messages import describe_exception

        try:
            return fn(args, ctx)
        except container_ops.ContainerError as e:
            raise DaemonError(e.kind, e.detail) from e
        except libvirt.libvirtError as e:
            raise DaemonError("failed", describe_exception(e)) from e

    return wrapper


class ContainerArgs(Args):
    container: str = Field(min_length=1, max_length=NAME)


@operation("container.start", ContainerArgs, needs=on_container("container.power"))
@_container
def start(args, ctx):
    from app.core import container_ops, libvirt_utils

    with libvirt_utils.local_lxc_conn() as conn:
        container_ops.start(conn, args.container)


class StopArgs(ContainerArgs):
    force: bool = False
    # An application image's process gets SIGTERM; a system container's init shuts its services down.
    application: bool = False
    # The run asked to stop, for the stop by force after the grace period: a new run is left alone.
    run_id: int | None = Field(default=None, ge=0)


@operation("container.stop", StopArgs, needs=on_container("container.power"))
@_container
def stop(args, ctx):
    from app.core import container_ops, libvirt_utils

    with libvirt_utils.local_lxc_conn() as conn:
        return container_ops.stop(conn, args.container, args.force, args.application, args.run_id)


class UpdateArgs(ContainerArgs):
    vcpu: int | None = Field(default=None, ge=1, le=1024)
    memory_mb: int | None = Field(default=None, ge=128, le=16 * 1024 * 1024)
    autostart: bool | None = None
    dns: list[str] | None = Field(default=None, max_length=8)


@operation("container.update", UpdateArgs)
@_container
def update(args, ctx):
    """Its processors and memory, its start at boot, the DNS servers written into its own /etc/resolv.conf."""
    from app.core import container_ops, libvirt_utils

    with libvirt_utils.local_conn() as qemu, libvirt_utils.local_lxc_conn() as conn:
        return container_ops.update(qemu, conn, args.container, args.vcpu, args.memory_mb, args.autostart, args.dns)


class DeleteArgs(ContainerArgs):
    application: bool = False  # its fixed address is released


@operation("container.delete", DeleteArgs)
@_container
def delete(args, ctx):
    from app.core import container_ops, libvirt_utils

    with libvirt_utils.local_conn() as qemu, libvirt_utils.local_lxc_conn() as conn:
        return container_ops.delete(qemu, conn, args.container, args.application)


class RenameArgs(ContainerArgs):
    new_name: str = Field(min_length=1, max_length=NAME)


@operation("container.rename", RenameArgs)
@_container
def rename(args, ctx):
    from app.core import container_ops, libvirt_utils

    with libvirt_utils.local_conn() as qemu, libvirt_utils.local_lxc_conn() as conn:
        return container_ops.rename(qemu, conn, args.container, args.new_name)


# --- Part 2: creation and clone (app/core/container_create.py) ---


class CreateArgs(Args):
    # The API's own model of POST /containers, checked again here; its unknown fields are refused.
    spec: dict
    check_only: bool = False

    @field_validator("spec")
    @classmethod
    def _as_sent(cls, spec):
        from app.core.container_create import ContainerCreate

        return ContainerCreate.model_validate(spec, extra="forbid")


# One call that waits: an image is pulled and unpacked, a filesystem copied and configured.
@operation("container.create", CreateArgs)
@_container
def create(args, ctx):
    from app.core import container_create, libvirt_utils

    with libvirt_utils.local_conn() as qemu, libvirt_utils.local_lxc_conn() as conn:
        return container_create.create(qemu, conn, args.spec, check_only=args.check_only)


class CloneArgs(ContainerArgs):
    new_name: str = Field(min_length=1, max_length=NAME)
    # An application container's process, as the API recorded it at its creation: the clone runs the same.
    app: dict | None = None

    @field_validator("app")
    @classmethod
    def _process(cls, app):
        from app.core.container_create import AppSpec

        return None if app is None else AppSpec.model_validate(app, extra="forbid").model_dump()


@operation("container.clone", CloneArgs)
@_container
def clone(args, ctx):
    from app.core import container_create, libvirt_utils

    with libvirt_utils.local_conn() as qemu, libvirt_utils.local_lxc_conn() as conn:
        return container_create.clone(qemu, conn, args.container, args.new_name, args.app)


# --- Part 3: snapshots and the console log ---


class SnapshotArgs(ContainerArgs):
    snapshot: str = Field(min_length=1, max_length=NAME)


class SnapshotCreateArgs(SnapshotArgs):
    description: str | None = Field(default=None, max_length=4096)
    check_only: bool = False


class SnapshotRestoreArgs(SnapshotArgs):
    check_only: bool = False


@operation("container.snapshot_list", ContainerArgs, needs=on_container("container.view"))
@_container
def snapshot_list(args, ctx):
    """The snapshots are root's (0700): listed here."""
    from app.core import container_ops, libvirt_utils

    with libvirt_utils.local_conn() as qemu, libvirt_utils.local_lxc_conn() as conn:
        return {"snapshots": container_ops.snapshots(qemu, conn, args.container)}


# One call that waits: the container's files are copied.
@operation("container.snapshot_create", SnapshotCreateArgs)
@_container
def snapshot_create(args, ctx):
    from app.core import container_ops, libvirt_utils

    with libvirt_utils.local_conn() as qemu, libvirt_utils.local_lxc_conn() as conn:
        return container_ops.snapshot_create(
            qemu, conn, args.container, args.snapshot, args.description, check_only=args.check_only
        )


@operation("container.snapshot_restore", SnapshotRestoreArgs)
@_container
def snapshot_restore(args, ctx):
    from app.core import container_ops, libvirt_utils

    with libvirt_utils.local_conn() as qemu, libvirt_utils.local_lxc_conn() as conn:
        container_ops.snapshot_restore(qemu, conn, args.container, args.snapshot, check_only=args.check_only)


@operation("container.snapshot_delete", SnapshotArgs)
@_container
def snapshot_delete(args, ctx):
    from app.core import container_ops, libvirt_utils

    with libvirt_utils.local_conn() as qemu, libvirt_utils.local_lxc_conn() as conn:
        container_ops.snapshot_delete(qemu, conn, args.container, args.snapshot)


class LogArgs(ContainerArgs):
    lines: int = Field(default=300, ge=1, le=5000)


@operation("container.log", LogArgs, needs=on_container("container.console"))
@_container
def log(args, ctx):
    from app.core import container_ops, libvirt_utils

    with libvirt_utils.local_conn() as qemu, libvirt_utils.local_lxc_conn() as conn:
        return container_ops.log(qemu, conn, args.container, args.lines)


# --- The containers' archives (the backups domain, part 4) ---


@operation("container.archive", ContainerArgs)
@_container
def archive(args, ctx):
    """A stopped container's filesystem archived into the containers' backup directory: {"path", "size"}."""
    from app.core import container_ops, libvirt_utils

    with libvirt_utils.local_conn() as qemu, libvirt_utils.local_lxc_conn() as conn:
        return container_ops.archive(qemu, conn, args.container)


class ArchiveArgs(Args):
    path: str = Field(min_length=1, max_length=4096)


@operation("container.archive_delete", ArchiveArgs)
@_container
def archive_delete(args, ctx):
    """An archive of the containers' backup directory removed: {"deleted"}."""
    from app.core import container_ops

    return {"deleted": container_ops.archive_delete(args.path)}


class ArchiveRestoreArgs(ArchiveArgs):
    container: str = Field(min_length=1, max_length=NAME)
    original: str = Field(min_length=1, max_length=NAME)


@operation("container.archive_restore", ArchiveRestoreArgs)
@_container
def archive_restore(args, ctx):
    """An archive unpacked and defined as the container `container`: {"mac", "ip", "network"}."""
    from app.core import container_ops, libvirt_utils

    container_ops.archive_path(args.path)  # before anything is opened
    with libvirt_utils.local_lxc_conn() as conn:
        return container_ops.archive_restore(conn, args.path, args.container, args.original)
