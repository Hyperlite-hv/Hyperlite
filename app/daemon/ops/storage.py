"""The storage's root work (docs/design/privilege-separation.md, section 13, the storage domain), part 1: the ZFS pools
and volumes the storage routes make and remove (app/routers/storage.py). The implementation stays in
app/core/zfs_storage.py. Each operation takes a pool's or a volume's name, checked again here; the routes keep who may
ask, the audit log and their answers. What VMs do with zvols (their disks, snapshots, resizes) moves with the VM
domain."""

import functools

from pydantic import Field

from app.core import zfs_storage
from app.daemon.registry import Args, DaemonError, NoArgs, operation

NAME = 63  # zfs_storage.NAME_RE's own limit
MAX_GB = 1 << 20  # a bound for the request; the routes check the host's own limits (app/core/vm_limits.py)


def _zfs(fn):
    """zfs_storage's refusals: "not_found" for what does not exist, "refused" with its message otherwise."""

    @functools.wraps(fn)
    def wrapper(args, ctx):
        try:
            return fn(args, ctx)
        except zfs_storage.ZfsNotFoundError as e:
            raise DaemonError("not_found", e.message) from e
        except zfs_storage.ZfsError as e:
            raise DaemonError("refused", e.message) from e

    return wrapper


def _name(value):
    error = zfs_storage.validate_zfs_name(value)
    if error:
        raise DaemonError("refused", error)


@operation("storage.zfs_load_module", NoArgs)
def zfs_load_module(args, ctx):
    """The kernel module, loaded once on an administrator's action (creating a pool), never on a poll."""
    return {"loaded": zfs_storage.load_module()}


class PoolArgs(Args):
    name: str = Field(min_length=1, max_length=NAME)


class PoolCreateArgs(PoolArgs):
    size_gb: int = Field(ge=1, le=MAX_GB)


@operation("storage.zfs_pool_create", PoolCreateArgs)
@_zfs
def zfs_pool_create(args, ctx):
    _name(args.name)
    return zfs_storage.create_pool(args.name, args.size_gb)


@operation("storage.zfs_pool_delete", PoolArgs)
@_zfs
def zfs_pool_delete(args, ctx):
    """Refused while the pool holds volumes (zfs_storage.delete_pool)."""
    _name(args.name)
    zfs_storage.delete_pool(args.name)


class VolumeArgs(Args):
    pool: str = Field(min_length=1, max_length=NAME)
    name: str = Field(min_length=1, max_length=NAME)


class VolumeCreateArgs(VolumeArgs):
    size_gb: int = Field(ge=1, le=MAX_GB)


@operation("storage.zfs_volume_create", VolumeCreateArgs)
@_zfs
def zfs_volume_create(args, ctx):
    _name(args.pool)
    _name(args.name)
    if not zfs_storage.pool_exists(args.pool):
        raise DaemonError("not_found", f"ZFS pool '{args.pool}' not found")
    return {"path": zfs_storage.create_zvol(args.pool, args.name, args.size_gb)}


def _disk_paths_in_use():
    import libvirt

    from app.core.libvirt_utils import LIBVIRT_URI, get_disk_paths_in_use

    conn = libvirt.open(LIBVIRT_URI)
    try:
        return get_disk_paths_in_use(conn)
    finally:
        conn.close()


@operation("storage.zfs_volume_delete", VolumeArgs)
@_zfs
def zfs_volume_delete(args, ctx):
    """Refused while a VM's definition uses the volume, whatever the API checked."""
    _name(args.pool)
    _name(args.name)
    if zfs_storage.device_path(args.pool, args.name) in _disk_paths_in_use():
        raise DaemonError("in_use", f"Volume '{args.name}' is used by a VM, deletion refused")
    zfs_storage.delete_zvol(args.pool, args.name)
