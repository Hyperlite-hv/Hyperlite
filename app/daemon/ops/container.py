"""The containers' root work (docs/design/privilege-separation.md, section 13, the containers domain). Part 1: an
existing container started, stopped, its settings changed, renamed, deleted (app/core/container_ops.py), asked by its
name. Its filesystem is found in its definition and accepted in Hyperlite's container directories only. The routes
keep who may ask, the container's lock, its records and their answers."""

import functools

from pydantic import Field

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


@operation("container.start", ContainerArgs)
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


@operation("container.stop", StopArgs)
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
