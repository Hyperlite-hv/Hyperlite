"""The storage's root work (docs/design/privilege-separation.md, section 13, the storage domain). Part 1: the ZFS pools
and volumes the storage routes make and remove (app/routers/storage.py), implemented in app/core/zfs_storage.py. Parts
2a and 2b: the libvirt pools of this node the storage routes make, rename and remove, and their volumes, implemented in
app/core/storage_pools.py, which writes a pool's or a volume's definition from checked fields. Each operation takes
names and fields, checked again here; the routes keep who may ask, the audit log and their answers. What VMs do with
their disks (zvols, snapshots, resizes) moves with the VM domain."""

import functools
from typing import Literal

from pydantic import Field

from app.core import iscsi, zfs_storage
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
    from app.core import libvirt_utils

    with libvirt_utils.local_conn() as conn:
        return libvirt_utils.get_disk_paths_in_use(conn)


@operation("storage.zfs_volume_delete", VolumeArgs)
@_zfs
def zfs_volume_delete(args, ctx):
    """Refused while a VM's definition uses the volume, whatever the API checked."""
    _name(args.pool)
    _name(args.name)
    if zfs_storage.device_path(args.pool, args.name) in _disk_paths_in_use():
        raise DaemonError("in_use", f"Volume '{args.name}' is used by a VM, deletion refused")
    zfs_storage.delete_zvol(args.pool, args.name)


# --- Part 2a: the libvirt pools of this node (directory, NFS, SMB, iSCSI, LVM) ---

LIBVIRT_NAME = 64  # vm_builder.validate_name's own limit is 63
FIELD = 512  # loose bounds: the modules' own checks give the user their sentence (storage_pools.check_spec)


def _pools(fn):
    """storage_pools' refusals and failures keep their kind; a libvirt failure is "failed" with libvirt's reason."""

    @functools.wraps(fn)
    def wrapper(args, ctx):
        import libvirt

        from app.core import storage_pools
        from app.core.error_messages import describe_exception

        try:
            return fn(args, ctx)
        except storage_pools.PoolError as e:
            raise DaemonError(e.kind, e.message) from e
        except libvirt.libvirtError as e:
            raise DaemonError("failed", describe_exception(e)) from e

    return wrapper


def _pool_name(value):
    from app.core.vm_builder import validate_name

    error = validate_name(value, "storage pool")
    if error:
        raise DaemonError("refused", error)


class LibvirtPoolCreateArgs(Args):
    """routers.storage.PoolCreate's fields for a libvirt pool, without the nodes it is made on."""

    name: str = Field(min_length=1, max_length=LIBVIRT_NAME)
    type: Literal["dir", "netfs", "smb", "iscsi", "lvm"]
    path: str | None = Field(None, max_length=FIELD)
    nfs_host: str | None = Field(None, max_length=FIELD)
    nfs_export_path: str | None = Field(None, max_length=FIELD)
    nfs_version: Literal["3", "4", "4.0", "4.1", "4.2"] = "4.2"
    iscsi_host: str | None = Field(None, max_length=FIELD)
    iscsi_port: int = Field(iscsi.DEFAULT_PORT, ge=1, le=65535)
    iscsi_target: str | None = Field(None, max_length=FIELD)
    chap_user: str | None = Field(None, max_length=FIELD)
    chap_password: str | None = Field(None, max_length=FIELD)
    smb_host: str | None = Field(None, max_length=FIELD)
    smb_share: str | None = Field(None, max_length=FIELD)
    smb_user: str | None = Field(None, max_length=FIELD)
    smb_password: str | None = Field(None, max_length=FIELD)
    smb_domain: str | None = Field(None, max_length=FIELD)
    smb_version: str = Field("default", max_length=16)
    lvm_vg: str | None = Field(None, max_length=FIELD)
    lvm_device: str | None = Field(None, max_length=FIELD)
    lvm_thin_pool: str | None = Field(None, max_length=FIELD)
    # A node registered over SSH only, or a shared pool's other node, reached with the cluster's key (the cluster
    # domain, part 2); absent: this node.
    node: str | None = Field(None, min_length=1, max_length=63)


@operation("storage.pool_create", LibvirtPoolCreateArgs)
@_pools
def pool_create(args, ctx):
    """Every field checked again (storage_pools.check_spec), then the pool defined, built, started and autostarted,
    with its SMB credentials, its CHAP secret and its LVM thin pool; nothing left half made. Returns {"warning"}."""
    from app.core import libvirt_utils, storage_pools

    _pool_name(args.name)
    with libvirt_utils.node_conn(args.node) as conn:
        return storage_pools.create(conn, args, args.node)


class LibvirtPoolArgs(Args):
    pool: str = Field(min_length=1, max_length=LIBVIRT_NAME)
    node: str | None = Field(None, min_length=1, max_length=63)


class LibvirtPoolRenameArgs(LibvirtPoolArgs):
    new_name: str = Field(min_length=1, max_length=LIBVIRT_NAME)


def _not_default(*names):
    if "default" in names:
        raise DaemonError("refused", "The 'default' pool keeps its name and stays: Hyperlite relies on it")


@operation("storage.pool_rename", LibvirtPoolRenameArgs)
@_pools
def pool_rename(args, ctx):
    """Refused while a running VM uses the pool (storage_pools.rename); the 'default' pool keeps its name."""
    from app.core import libvirt_utils, storage_pools

    _pool_name(args.new_name)
    _not_default(args.pool, args.new_name)
    with libvirt_utils.node_conn(args.node) as conn:
        storage_pools.rename(conn, args.pool, args.new_name)


class LibvirtPoolDeleteArgs(LibvirtPoolArgs):
    detach: bool = False


@operation("storage.pool_delete", LibvirtPoolDeleteArgs)
@_pools
def pool_delete(args, ctx):
    """Never the user's files: refused while the pool holds any, or a VM uses it, whatever the API checked
    (storage_pools.delete). The 'default' pool stays."""
    from app.core import libvirt_utils, storage_pools

    _not_default(args.pool)
    with libvirt_utils.node_conn(args.node) as conn:
        storage_pools.delete(conn, args.pool, detach=args.detach, node=args.node)


@operation("storage.pool_check", LibvirtPoolArgs)
@_pools
def pool_check(args, ctx):
    """Whether QEMU can own files on the share an NFS or SMB pool mounted: a file written there and removed."""
    from app.core import libvirt_utils, storage_pools

    with libvirt_utils.local_conn() as conn:
        return storage_pools.check_permissions(conn, args.pool)


class NodeOnlyArgs(Args):
    node: str | None = Field(None, min_length=1, max_length=63)


@operation("storage.ensure_default_pool", NodeOnlyArgs)
@_pools
def ensure_default_pool(args, ctx):
    """The 'default' pool Hyperlite relies on (libvirt_utils.ensure_default_pool), defined, started and autostarted,
    here or on a node registered over SSH only."""
    from app.core import libvirt_utils

    with libvirt_utils.node_conn(args.node) as conn:
        libvirt_utils.ensure_default_pool(conn)


# --- Part 2b: the volumes of this node's libvirt pools ---


class LibvirtVolumeArgs(LibvirtPoolArgs):
    name: str = Field(min_length=1, max_length=128)


class LibvirtVolumeCreateArgs(LibvirtVolumeArgs):
    size_gb: int = Field(ge=1, le=MAX_GB)


@operation("storage.volume_create", LibvirtVolumeCreateArgs)
@_pools
def volume_create(args, ctx):
    """A qcow2 volume, its definition written here from its checked name and size (storage_pools.volume_create)."""
    from app.core import libvirt_utils, storage_pools

    with libvirt_utils.local_conn() as conn:
        return storage_pools.volume_create(conn, args.pool, args.name, args.size_gb)


@operation("storage.volume_delete", LibvirtVolumeArgs)
@_pools
def volume_delete(args, ctx):
    """Refused while a VM's definition uses the volume, whatever the API checked."""
    from app.core import libvirt_utils, storage_pools

    with libvirt_utils.local_conn() as conn:
        storage_pools.volume_delete(conn, args.pool, args.name)


# --- Part 2c: what the storage routes read that needs root or a connection that may write ---


@operation("storage.pool_refresh", LibvirtPoolArgs)
@_pools
def pool_refresh(args, ctx):
    """libvirt's list of the pool's volumes read again (files written outside libvirt, LUNs that came or went): a
    refresh is refused on libvirt's read-only connection, the one the API will keep."""
    from app.core import libvirt_utils, storage_pools

    with libvirt_utils.node_conn(args.node) as conn:
        storage_pools.refresh(conn, args.pool)


class VolumeGroupArgs(Args):
    vg: str = Field(min_length=1, max_length=128)
    node: str | None = Field(None, min_length=1, max_length=63)  # read there over SSH with the cluster's key


@operation("storage.lvm_volumes", VolumeGroupArgs)
def lvm_volumes(args, ctx):
    """Every volume of the group, thin ones and thin pools included (lvm_storage.logical_volumes): LVM reads the
    disks, which only root may."""
    from app.core import lvm_storage

    try:
        lvm_storage.check_name(args.vg)
    except lvm_storage.LvmError as e:
        raise DaemonError("refused", str(e)) from e
    if args.node:
        from app.core import cluster

        if cluster.get_node(args.node) is None:
            raise DaemonError("not_found", f"Node '{args.node}' not found")
    try:
        return lvm_storage.logical_volumes(args.vg, args.node)
    except lvm_storage.LvmError as e:
        raise DaemonError("failed", str(e)) from e
