import logging
import shutil
import xml.etree.ElementTree as ET
from typing import Literal

import libvirt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import (
    cluster,
    cluster_view,
    iscsi,
    lvm_storage,
    privileged,
    renaming,
    shared_pools,
    smb_storage,
    storage_pools,
    zfs_storage,
)
from app.core import permissions as perm
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import get_disk_paths_in_use, open_conn
from app.core.listing import item, listing
from app.core.security import get_current_user, require_role
from app.core.vm_builder import validate_name
from app.core.vm_limits import validate_vm_resources
from app.daemon.registry import DaemonError

logger = logging.getLogger(__name__)

# ?node= on every route: the member that holds the object does the work (app/core/cluster_view.py, on_node).
router = APIRouter(prefix="/storage", tags=["storage"], dependencies=[Depends(cluster_view.on_node)])


# This table did not match libvirt's real enumeration (virStoragePoolState,
# checked through libvirt.VIR_STORAGE_POOL_*: only 5 values, 0-4, not 6). Every
# pool, including an already active "default", was displayed as
# "building". It had no visible effect while no screen displayed this
# "state" field.
POOL_STATE_NAMES = {
    0: "inactive",  # VIR_STORAGE_POOL_INACTIVE
    1: "building",  # VIR_STORAGE_POOL_BUILDING
    2: "active",  # VIR_STORAGE_POOL_RUNNING
    3: "degraded",  # VIR_STORAGE_POOL_DEGRADED
    4: "inaccessible",  # VIR_STORAGE_POOL_INACCESSIBLE
}


def _node(node):
    """The node a request names, None for this host: `?node=local` is this host too."""
    return None if not node or node == "local" else node


def _pool_work(node, op, user, action, target, refused=422, **args):
    """A pool's root work, by hyperlite-daemon (`op` with `args`), on this node or on another (`node`: registered over
    SSH only, or a shared pool's member), whose libvirt the daemon reaches with the cluster's key. The failure is in
    the audit log."""
    return privileged.ask(op, user, action, target, refused=refused, node=node, **args)


def _pool_type(pool):
    return storage_pools.pool_type(pool)


def _pool_path(pool):
    return storage_pools.pool_path(pool)


_FS_NS = "{http://libvirt.org/schemas/storagepool/fs/1.0}"


def _nfs_version(pool):
    """The 'vers=' mount option of an NFS pool (None for one defined outside Hyperlite without it)."""
    try:
        root = ET.fromstring(pool.XMLDesc(0))
    except (libvirt.libvirtError, ET.ParseError):
        return None
    for option in root.iter(f"{_FS_NS}option"):
        name = option.get("name", "")
        if name.startswith("vers="):
            return name.split("=", 1)[1]
    return None


def _pool_root(pool):
    try:
        return ET.fromstring(pool.XMLDesc(0))
    except (libvirt.libvirtError, ET.ParseError):
        return None


def _refresh(pool, node):
    """libvirt's list of the pool's volumes read again, by hyperlite-daemon (a refresh needs a connection that may
    write), here or on the pool's node. Raises DaemonError."""
    privileged.run("storage.pool_refresh", pool=pool.name(), node=_node(node))


def _lookup_volume(pool, name):
    """libvirt_utils.lookup_volume for this node's pool, its refresh on a miss done by hyperlite-daemon. Raises
    libvirt.libvirtError when the volume really does not exist."""
    try:
        return pool.storageVolLookupByName(name)
    except libvirt.libvirtError:
        try:
            _refresh(pool, None)
        except DaemonError as e:
            logger.warning("Pool %s not refreshed: %s", pool.name(), e.detail)
    return pool.storageVolLookupByName(name)


def _lvm_volumes(vg, node):
    """lvm_storage.logical_volumes, by hyperlite-daemon (LVM reads the disks, which only root may), here or on the
    pool's node over SSH. Raises lvm_storage.LvmError."""
    try:
        return privileged.run("storage.lvm_volumes", vg=vg, node=_node(node))
    except DaemonError as e:
        raise lvm_storage.LvmError(e.detail) from None


def _pool_summary(pool, node=None):
    state, capacity, allocation, available = pool.info()
    if _pool_type(pool) == "iscsi":
        # libvirt counts every LUN as fully allocated, so an iSCSI pool always looked 100 % full. What matters is
        # how much of it VMs already use: a LUN is taken whole or not at all.
        try:
            # LUNs appear and disappear on the storage server: the cached list would count stale ones.
            if pool.isActive():
                _refresh(pool, node)
            in_use = get_disk_paths_in_use(pool.connect())
            allocation = sum(v.info()[1] for v in pool.listAllVolumes() if v.path() in in_use)
            available = capacity - allocation
        except (libvirt.libvirtError, DaemonError) as e:
            logger.debug("LUNs of %s not counted: %s", pool.name(), e)
    root = _pool_root(pool)
    smb = root is not None and smb_storage.is_smb(root)
    thin = None
    if _pool_type(pool) == "logical" and root is not None and state == libvirt.VIR_STORAGE_POOL_RUNNING:
        # VM disks in its thin pool: what the pool holds is that thin pool, filled as LVM reports it (libvirt counts
        # the thin pool as one volume taking the whole group).
        try:
            thin = lvm_storage.thin_pool_in(_lvm_volumes(root.findtext("source/name"), node))
        except lvm_storage.LvmError as e:
            logger.warning("Thin pool of %s not read: %s", pool.name(), e)
        if thin is not None:
            capacity = thin["size"]
            allocation = round(thin["size"] * (thin["data_percent"] or 0) / 100)
            available = capacity - allocation
    return {
        "lvm_thin_pool": thin["name"] if thin else None,
        # A netfs pool mounts an NFS export or an SMB share: the same shared storage, said apart here.
        "protocol": ("smb" if smb else "nfs") if _pool_type(pool) == "netfs" else None,
        "smb_version": smb_storage.version_of(root) if smb else None,
        "nfs_version": _nfs_version(pool) if _pool_type(pool) == "netfs" and not smb else None,
        "path": _pool_path(pool),
        "name": pool.name(),
        "uuid": pool.UUIDString(),
        "type": _pool_type(pool),
        "state": POOL_STATE_NAMES.get(state, "unknown"),
        "autostart": bool(pool.autostart()),
        "capacity_gb": round(capacity / (1024**3), 2),
        "allocation_gb": round(allocation / (1024**3), 2),
        "available_gb": round(available / (1024**3), 2),
    }


@router.get("")
def list_pools(node: str | None = None, user: dict = Depends(get_current_user)):
    """node: the same convention as GET /vms: list the pools of a registered remote
    node instead of the local host.

    ZFS pools are merged into the same list (type='zfs') for a unified display
    in the UI, but only when `node` designates the LOCAL host: unlike libvirt
    pools (dir/netfs), ZFS management is still single-node (direct `zpool`/`zfs`
    calls on THIS server, no remote management over SSH; see
    app/core/zfs_storage.py). A ZFS pool of a registered remote node is
    therefore not visible here for now."""
    result = _pools(node)
    log_action(user["username"], "list_storage_pools", "storage", "success")
    return listing(result)


def _ensure_default(conn, node):
    """The 'default' pool, made and started where it is missing, by hyperlite-daemon (here or on `node`), only when it
    is missing, stopped or not started at boot, so a listing costs no call to the daemon."""
    try:
        pool = conn.storagePoolLookupByName("default")
        if pool.isActive() and pool.autostart():
            return
    except libvirt.libvirtError:
        pass
    try:
        privileged.run("storage.ensure_default_pool", node=_node(node))
    except DaemonError as e:
        logger.error("The 'default' storage pool could not be made: %s", e.detail)


def _read_pool(conn, name):
    """A pool of this node by its name, for the routes' reads; 'default' made first where it is missing, as
    libvirt_utils.lookup_pool does, by hyperlite-daemon. Raises libvirt.libvirtError as the lookup."""
    if name == "default":
        _ensure_default(conn, None)
    return conn.storagePoolLookupByName(name)


def _pools(node):
    conn = open_conn(node, readonly=True)
    try:
        _ensure_default(conn, node)
        result = []
        for pool in conn.listAllStoragePools():
            # A pool removed while the list is built (another session deleting it) is skipped, not a failed list.
            try:
                result.append(_pool_summary(pool, node))
            except libvirt.libvirtError as e:
                if e.get_error_code() != libvirt.VIR_ERR_NO_STORAGE_POOL:
                    raise
                logger.debug("Storage pool vanished while listing: %s", e)
        if not node or node == "local":
            result += zfs_storage.list_pools()
        for p in result:
            p["node"] = node or "local"
            p.setdefault("path", None)
        return result
    finally:
        conn.close()


class PoolCreate(BaseModel):
    name: str
    # "dir": a directory local to the node (like the existing "default" pool).
    # "netfs": a remote NFS export mounted by libvirt (shared storage), the
    # foundation of live migration: a disk on a netfs pool is visible identically from
    # any node that mounts the same export, so it does not need to be copied when a VM
    # is migrated.
    # "zfs": a ZFS pool managed outside libvirt (see app/core/zfs_storage.py), backed
    # for now by a loopback file (`size_gb`) rather than a dedicated disk, so the
    # mechanism can be validated without touching a node's existing LVM.
    # "iscsi": a target on a NAS or storage array (see app/core/iscsi.py); its LUNs, created on the storage side,
    # become VM disks.
    # "smb": an SMB/CIFS share of a Windows server or a NAS, mounted by libvirt as a netfs pool (app/core/smb_storage.py):
    # shared storage as an NFS pool is, and listed as one ("netfs", with protocol "smb").
    type: str = Field(pattern="^(dir|netfs|smb|zfs|iscsi|lvm)$")
    path: str | None = None  # pool "dir" : repertoire local (defaut si omis)
    nfs_host: str | None = None  # "netfs" pool: NFS server host
    nfs_export_path: str | None = None  # pool "netfs" : path exporte cote server
    # "netfs" pool: the NFS protocol version to mount with. Always explicit, never negotiated (see _build_pool_xml):
    # 4.2 unless the server only offers an older one (a NAS limited to NFSv3, for instance).
    nfs_version: Literal["3", "4", "4.0", "4.1", "4.2"] = "4.2"
    size_gb: int | None = Field(None, ge=1)  # "zfs" pool: size of the loopback file
    iscsi_host: str | None = None  # "iscsi" pool: portal address of the storage server
    iscsi_port: int = Field(iscsi.DEFAULT_PORT, ge=1, le=65535)
    iscsi_target: str | None = None  # "iscsi" pool: target name (IQN)
    chap_user: str | None = None  # "iscsi" pool: CHAP credentials, when the target requires them
    chap_password: str | None = Field(None, max_length=255)
    smb_host: str | None = None  # "smb" pool: the server's address
    smb_share: str | None = None  # "smb" pool: the share's name
    smb_user: str | None = None
    smb_password: str | None = Field(None, max_length=255)  # kept in a root-only file on each node, never returned
    smb_domain: str | None = None  # "smb" pool: the Windows domain or workgroup, when the server needs it
    smb_version: Literal["default", "3.1.1", "3.0", "2.1", "2.0"] = "default"  # "default": negotiated
    # "lvm": libvirt's logical pool on a volume group (app/core/lvm_storage.py): an existing one, or one built on an
    # empty disk (lvm_device), as Proxmox VE's LVM storage.
    lvm_vg: str | None = None
    lvm_device: str | None = None
    # "lvm" pool: the thin pool its VM disks are made in (LVM-thin): an existing one of the group, or one Hyperlite
    # creates over the group's free space. None: thick volumes.
    lvm_thin_pool: str | None = None
    # Shared storage (NFS, iSCSI) declared for several nodes at once, as on Proxmox: every node, including the ones
    # registered later (all_nodes), or the nodes listed ("local" is this host). See app/core/shared_pools.py.
    all_nodes: bool = False
    nodes: list[str] | None = Field(None, max_length=64)


@router.get("/support")
def storage_support(user: dict = Depends(get_current_user)):
    """Which pool types the local host can create now, and why not: read by the pool creation form to warn
    before the attempt. nfs: 'ok' or 'no_client'; zfs: see zfs_storage.status(). Nothing is loaded or run."""
    return {
        "nfs": "ok" if shutil.which("mount.nfs") else "no_client",
        "zfs": zfs_storage.status(),
        "iscsi": "ok" if iscsi.initiator_available() else "no_initiator",
        "smb": "ok" if shutil.which("mount.cifs") else "no_client",
        "lvm": "ok" if shutil.which("lvm") else "no_tools",
        # The storage side must allow this name (ACL) before it shows any LUN.
        "iscsi_initiator": iscsi.initiator_name(),
    }


@router.post("", status_code=201)
def create_pool(payload: PoolCreate, node: str | None = None, user: dict = Depends(require_role("admin"))):
    if payload.all_nodes or payload.nodes:
        return _create_shared(payload, user)
    return _create_single(payload, node, user)


def _create_single(payload, node, user):
    node = _node(node)
    name_error = validate_name(payload.name, "storage pool")
    if name_error:
        log_action(user["username"], "create_storage_pool", payload.name, "failed", name_error)
        raise HTTPException(status_code=422, detail=name_error)

    if payload.type == "zfs":
        if node and node != "local":
            raise HTTPException(
                status_code=422,
                detail="A ZFS pool can only be created on the local host (single-node management for now)",
            )
        if not payload.size_gb:
            raise HTTPException(status_code=422, detail="size_gb is required for a ZFS pool")
        size_errors = validate_vm_resources(disk_sizes=[payload.size_gb])
        if size_errors:
            raise HTTPException(status_code=422, detail=size_errors)
        if not privileged.ask("storage.zfs_load_module", user, "create_storage_pool", payload.name)["loaded"]:
            reason = zfs_storage.STATUS_MESSAGES[zfs_storage.status()]
            log_action(user["username"], "create_storage_pool", payload.name, "failed", reason)
            raise HTTPException(status_code=422, detail=reason)
        # A refusal (a name taken, a backing file left behind, zpool's own error) answered 500 as before.
        pool = privileged.ask(
            "storage.zfs_pool_create",
            user,
            "create_storage_pool",
            payload.name,
            refused=500,
            failed="ZFS pool creation error: {}",
            name=payload.name,
            size_gb=payload.size_gb,
        )
        log_action(user["username"], "create_storage_pool", payload.name, "success")
        return pool

    # Checked here first, so another node's pool gets the same answers; hyperlite-daemon checks it again.
    try:
        storage_pools.check_spec(payload, local=node is None)
    except storage_pools.PoolError as e:
        log_action(user["username"], "create_storage_pool", payload.name, "failed", e.message)
        raise HTTPException(status_code=422, detail=e.message) from None
    result = _pool_work(
        node,
        "storage.pool_create",
        user,
        "create_storage_pool",
        payload.name,
        **payload.model_dump(exclude={"size_gb", "all_nodes", "nodes"}),
    )
    log_action(user["username"], "create_storage_pool", payload.name, "success")
    conn = open_conn(node, readonly=True)
    try:
        summary = _pool_summary(conn.storagePoolLookupByName(payload.name), node)
    finally:
        conn.close()
    if node is None and payload.type in ("netfs", "smb"):
        summary["warning"] = result["warning"]
    return summary


def _shared_targets(payload):
    """The node keys ("local" or registered names) a shared pool is created on."""
    registered = [n["name"] for n in cluster.list_nodes()]
    if payload.all_nodes:
        return ["local", *registered]
    unknown = [n for n in payload.nodes if n != "local" and n not in registered]
    if unknown:
        raise HTTPException(status_code=404, detail=f"Unknown node(s): {', '.join(unknown)}")
    return list(dict.fromkeys(payload.nodes))


def _exists_on(pool_name, node_key):
    conn = open_conn(None if node_key == "local" else node_key, readonly=True)
    try:
        conn.storagePoolLookupByName(pool_name)
        return True
    except libvirt.libvirtError:
        return False
    finally:
        conn.close()


def create_on_nodes(payload, targets, user):
    """Create the pool on each node key; [{"node", "state": cree|exists|failed, "detail", "warning"}]. One
    node failing (unreachable, NFS client missing) does not stop the others."""
    results = []
    for key in targets:
        entry = {"node": key, "state": "created", "detail": None, "warning": None}
        try:
            if _exists_on(payload.name, key):
                entry["state"] = "exists"
            else:
                summary = _create_single(payload, None if key == "local" else key, user)
                entry["warning"] = summary.get("warning")
        except HTTPException as e:
            entry.update(
                state="failed", detail=e.detail if isinstance(e.detail, str) else "; ".join(map(str, e.detail))
            )
        except libvirt.libvirtError as e:
            entry.update(state="failed", detail=f"Node unreachable: {describe_exception(e)}")
        results.append(entry)
    return results


def _create_shared(payload, user):
    """The same NFS export or iSCSI target created on several nodes (see app/core/shared_pools.py)."""
    if payload.type not in shared_pools.SHARED_TYPES:
        raise HTTPException(
            status_code=422,
            detail="Only shared storage (NFS, SMB or iSCSI) can be created on several nodes: a local directory or a "
            "ZFS pool belongs to one host",
        )
    name_error = validate_name(payload.name, "storage pool")
    if name_error:
        raise HTTPException(status_code=422, detail=name_error)
    targets = _shared_targets(payload)
    if not targets:
        raise HTTPException(status_code=422, detail="Choose at least one node")
    results = create_on_nodes(payload, targets, user)
    done = [r["node"] for r in results if r["state"] != "failed"]
    if not done:
        details = "; ".join(f"{r['node']}: {r['detail']}" for r in results)
        raise HTTPException(status_code=502, detail=f"The pool could not be created on any node. {details}")
    shared_pools.save(payload.model_dump(), payload.all_nodes, done, user["username"])
    scope = "every node" if payload.all_nodes else ", ".join(targets)
    log_action(
        user["username"], "create_shared_pool", payload.name, "success", f"{scope}: created on {', '.join(done)}"
    )
    return {"name": payload.name, "shared": True, "all_nodes": payload.all_nodes, "results": results}


def apply_shared_pools(node_name, username):
    """Create the pools declared for every node on a node registered now; the results per pool."""
    out = []
    for entry in shared_pools.for_every_node():
        payload = PoolCreate(**entry["definition"])
        result = create_on_nodes(payload, [node_name], {"username": username})[0]
        if result["state"] != "failed":
            shared_pools.add_node(entry["name"], node_name)
        out.append({"name": entry["name"], **result})
    return out


@router.get("/shared")
def list_shared_pools(user: dict = Depends(get_current_user)):
    """The pools declared for several nodes, and which ones (no secret)."""
    return listing([{k: v for k, v in e.items() if k != "definition"} for e in shared_pools.list_all()])


@router.post("/{pool_name}/check-permissions")
def check_pool_permissions(pool_name: str, user: dict = Depends(require_role("admin"))):
    """For an NFS pool of this host: can QEMU own its disk files there (see app/core/nfs_permissions.py)?"""
    result = privileged.ask("storage.pool_check", user, "check_pool_permissions", pool_name, pool=pool_name)
    log_action(
        user["username"],
        "check_pool_permissions",
        pool_name,
        "success" if result["ok"] else "failed",
        result["message"],
    )
    return {"name": pool_name, "ok": result["ok"], "message": result["message"]}


class PoolRename(BaseModel):
    new_name: str
    # A shared pool (app/core/shared_pools.py) is renamed on every node that has it, as it was created.
    everywhere: bool = False


def _rename_on(pool_name, new, node, user):
    """The pool renamed on one node (storage_pools.rename: stopped, redefined, started again; refused while a running
    VM uses it), then Hyperlite's records of it."""
    node = _node(node)
    _pool_work(
        node,
        "storage.pool_rename",
        user,
        "rename_storage_pool",
        pool_name,
        refused=409,
        pool=pool_name,
        new_name=new,
    )
    renaming.storage_pool_records(pool_name, new, node or "local")
    log_action(user["username"], "rename_storage_pool", pool_name, "success", f"-> {new} on {node or 'local'}")
    conn = open_conn(node, readonly=True)
    try:
        return _pool_summary(conn.storagePoolLookupByName(new), node)
    finally:
        conn.close()


@router.post("/{pool_name}/rename")
def rename_pool(
    pool_name: str, payload: PoolRename, node: str | None = None, user: dict = Depends(require_role("admin"))
):
    new = payload.new_name
    error = validate_name(new, "storage pool")
    if error:
        raise HTTPException(status_code=422, detail=error)
    if pool_name == "default" or new == "default":
        raise HTTPException(status_code=400, detail="The 'default' pool keeps its name: Hyperlite relies on it")
    if new == pool_name:
        raise HTTPException(status_code=422, detail="The new name is the current one")
    if (not node or node == "local") and zfs_storage.pool_exists(pool_name):
        raise HTTPException(
            status_code=409,
            detail="A ZFS pool is renamed by exporting and importing it again (zpool export/import), which "
            "Hyperlite does not do: its zvols are the disks of VMs",
        )
    if not payload.everywhere:
        return _rename_on(pool_name, new, node, user)
    results = []
    for key in ["local", *[n["name"] for n in cluster.list_nodes()]]:
        entry = {"node": key, "state": "renamed", "detail": None}
        try:
            if not _exists_on(pool_name, key):
                continue
            _rename_on(pool_name, new, None if key == "local" else key, user)
        except HTTPException as e:
            entry.update(state="failed", detail=e.detail if isinstance(e.detail, str) else str(e.detail))
        except libvirt.libvirtError as e:
            entry.update(state="failed", detail=f"Node unreachable: {describe_exception(e)}")
        results.append(entry)
    if not any(r["state"] == "renamed" for r in results):
        raise HTTPException(
            status_code=409, detail="; ".join(f"{r['node']}: {r['detail']}" for r in results) or "Pool not found"
        )
    if not any(r["state"] == "failed" for r in results):
        shared_pools.rename(pool_name, new)
    return {"name": new, "results": results}


@router.delete("/{pool_name}")
def delete_pool(
    pool_name: str,
    node: str | None = None,
    confirm: bool = False,
    detach: bool = False,
    everywhere: bool = False,
    user: dict = Depends(require_role("admin")),
):
    """everywhere: a shared pool (app/core/shared_pools.py) is removed from every node that has it, and its
    definition with it, as on Proxmox; without it, from `node` only."""
    if pool_name == "default":
        raise HTTPException(status_code=400, detail="The 'default' pool cannot be deleted")
    if not confirm:
        raise HTTPException(status_code=400, detail="Irreversible action: add ?confirm=true to confirm the deletion")
    if everywhere:
        return _delete_everywhere(pool_name, detach, user)
    return _delete_single(pool_name, node, detach, user)


def _delete_everywhere(pool_name, detach, user):
    results = []
    for key in ["local", *[n["name"] for n in cluster.list_nodes()]]:
        entry = {"node": key, "state": "deleted", "detail": None}
        try:
            if not _exists_on(pool_name, key):
                continue
            _delete_single(pool_name, None if key == "local" else key, detach, user)
        except HTTPException as e:
            entry.update(state="failed", detail=e.detail if isinstance(e.detail, str) else str(e.detail))
        except libvirt.libvirtError as e:
            entry.update(state="failed", detail=f"Node unreachable: {describe_exception(e)}")
        results.append(entry)
    failed = [r for r in results if r["state"] == "failed"]
    if not failed:
        # Kept while a node still has it: the next attempt must find what is left.
        shared_pools.delete(pool_name)
    return {"name": pool_name, "results": results, "message": None if failed else f"Pool '{pool_name}' deleted"}


def _delete_single(pool_name, node, detach, user):
    node = _node(node)

    # A ZFS pool is NOT a libvirt pool (see zfs_storage.py): it is routed separately
    # before any lookup on the libvirt side, which would simply fail with "not found"
    # for a name that only exists on the ZFS side.
    if node is None and zfs_storage.pool_exists(pool_name):
        privileged.ask("storage.zfs_pool_delete", user, "delete_storage_pool", pool_name, refused=409, name=pool_name)
        log_action(user["username"], "delete_storage_pool", pool_name, "success")
        return {"message": f"ZFS pool '{pool_name}' deleted"}

    _pool_work(
        node,
        "storage.pool_delete",
        user,
        "delete_storage_pool",
        pool_name,
        refused=409,
        pool=pool_name,
        detach=detach,
    )
    log_action(user["username"], "delete_storage_pool", pool_name, "success")
    return {"message": f"Pool '{pool_name}' deleted"}


@router.get("/{pool_name}/volumes")
def list_volumes(pool_name: str, user: dict = Depends(get_current_user)):
    result = _volumes(pool_name)
    log_action(user["username"], "list_volumes", pool_name, "success")
    return listing(result)


def _volumes(pool_name):
    conn = open_conn(readonly=True)
    try:
        if zfs_storage.pool_exists(pool_name):
            in_use = get_disk_paths_in_use(conn)
            result = zfs_storage.list_zvols(pool_name)
            for vol in result:
                vol["used"] = vol["path"] in in_use
            return result

        try:
            pool = _read_pool(conn, pool_name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"Storage pool '{pool_name}' not found") from None
        _refresh(pool, None)
        in_use = get_disk_paths_in_use(conn)
        result = []
        for vol in pool.listAllVolumes():
            vol_info = vol.info()
            result.append(
                {
                    "name": vol.name(),
                    "path": vol.path(),
                    "capacity_gb": round(vol_info[1] / (1024**3), 3),
                    "allocation_gb": round(vol_info[2] / (1024**3), 3),
                    "used": vol.path() in in_use,
                }
            )
        root = ET.fromstring(pool.XMLDesc(0))
        if root.get("type") == "logical":
            # What libvirt does not list: thin volumes and thin pools, read from LVM.
            seen = {v["name"] for v in result}
            try:
                extra = [v for v in _lvm_volumes(root.findtext("source/name"), None) if v["name"] not in seen]
            except lvm_storage.LvmError as e:
                logger.warning("Thin volumes of pool %s not listed: %s", pool_name, e)
                extra = []
            for v in extra:
                result.append(
                    {
                        "name": v["name"],
                        "path": v["path"],
                        "capacity_gb": round(v["size"] / 1024**3, 3),
                        "allocation_gb": None,
                        "used": v["path"] in in_use,
                        "lvm_kind": v["kind"],
                    }
                )
        return result
    finally:
        conn.close()


class VolumeCreate(BaseModel):
    name: str
    size_gb: int = Field(ge=1)


@router.post("/{pool_name}/volumes", status_code=201)
def create_volume(pool_name: str, payload: VolumeCreate, user: dict = Depends(get_current_user)):
    """An administrator, or storage.allocate on the pool: making a disk to attach to a VM is a step of creating one, as
    Proxmox VE's Datastore.AllocateSpace (deleting a volume stays an administrator's: it may hold another VM's data)."""
    perm.refuse_unless(user, "storage", pool_name, "storage.allocate", f"storage pool '{pool_name}'")
    size_errors = validate_vm_resources(disk_sizes=[payload.size_gb])
    if size_errors:
        raise HTTPException(status_code=422, detail=size_errors)
    if zfs_storage.pool_exists(pool_name):
        name_error = zfs_storage.validate_zfs_name(payload.name)
        if name_error:
            log_action(user["username"], "create_volume", payload.name, "failed", name_error)
            raise HTTPException(status_code=422, detail=name_error)
        path = privileged.ask(
            "storage.zfs_volume_create",
            user,
            "create_volume",
            payload.name,
            refused=500,
            failed="zvol creation error: {}",
            pool=pool_name,
            name=payload.name,
            size_gb=payload.size_gb,
        )["path"]
        log_action(user["username"], "create_volume", payload.name, "success")
        return {"name": payload.name, "path": path, "capacity_gb": float(payload.size_gb)}

    # Read here first, so the answers stay as they were; hyperlite-daemon checks it all again.
    conn = open_conn(readonly=True)
    try:
        try:
            pool = _read_pool(conn, pool_name)
        except libvirt.libvirtError:
            log_action(user["username"], "create_volume", payload.name, "failed", "Pool not found")
            raise HTTPException(status_code=404, detail=f"Storage pool '{pool_name}' not found") from None
        if _pool_type(pool) == "iscsi":
            raise HTTPException(
                status_code=422, detail="An iSCSI pool's LUNs are created and resized on the storage server, not here"
            )
        try:
            filename = storage_pools.volume_file_name(payload.name)
        except storage_pools.PoolError as e:
            log_action(user["username"], "create_volume", payload.name, "failed", e.message)
            raise HTTPException(status_code=422, detail=e.message) from None
        try:
            _lookup_volume(pool, filename)
            log_action(user["username"], "create_volume", filename, "failed", "Volume already exists")
            raise HTTPException(status_code=422, detail=f"A volume '{filename}' already exists in this pool")
        except libvirt.libvirtError:
            pass
    finally:
        conn.close()
    created = privileged.ask(
        "storage.volume_create", user, "create_volume", filename, pool=pool_name, name=filename, size_gb=payload.size_gb
    )
    log_action(user["username"], "create_volume", filename, "success")
    return created


@router.delete("/{pool_name}/volumes/{volume_name}")
def delete_volume(pool_name: str, volume_name: str, confirm: bool = False, user: dict = Depends(require_role("admin"))):
    if zfs_storage.pool_exists(pool_name):
        conn = open_conn(readonly=True)
        try:
            in_use = get_disk_paths_in_use(conn)
        finally:
            conn.close()
        if zfs_storage.device_path(pool_name, volume_name) in in_use:
            log_action(user["username"], "delete_volume", volume_name, "failed", "Volume used by a VM")
            raise HTTPException(status_code=409, detail=f"Volume '{volume_name}' is used by a VM, deletion refused")
        if not confirm:
            log_action(user["username"], "delete_volume", volume_name, "failed", "Confirmation missing")
            raise HTTPException(
                status_code=400, detail="Irreversible action: add ?confirm=true to confirm the deletion"
            )
        # In use: 409; zfs failing: 500, as before.
        privileged.ask(
            "storage.zfs_volume_delete",
            user,
            "delete_volume",
            volume_name,
            refused=500,
            pool=pool_name,
            name=volume_name,
        )
        log_action(user["username"], "delete_volume", volume_name, "success")
        return {"message": f"Volume '{volume_name}' deleted"}

    # Read here first, so the answers stay as they were; hyperlite-daemon checks it all again.
    conn = open_conn(readonly=True)
    try:
        try:
            pool = _read_pool(conn, pool_name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"Storage pool '{pool_name}' not found") from None
        if _pool_type(pool) == "iscsi":
            raise HTTPException(
                status_code=422, detail="An iSCSI pool's LUNs are deleted on the storage server, not here"
            )
        try:
            vol = _lookup_volume(pool, volume_name)
        except libvirt.libvirtError:
            log_action(user["username"], "delete_volume", volume_name, "failed", "Volume not found")
            raise HTTPException(status_code=404, detail=f"Volume '{volume_name}' not found") from None
        if vol.path() in get_disk_paths_in_use(conn):
            log_action(user["username"], "delete_volume", volume_name, "failed", "Volume used by a VM")
            raise HTTPException(status_code=409, detail=f"Volume '{volume_name}' is used by a VM, deletion refused")
    finally:
        conn.close()
    if not confirm:
        log_action(user["username"], "delete_volume", volume_name, "failed", "Confirmation missing")
        raise HTTPException(status_code=400, detail="Irreversible action: add ?confirm=true to confirm the deletion")
    privileged.ask("storage.volume_delete", user, "delete_volume", volume_name, pool=pool_name, name=volume_name)
    log_action(user["username"], "delete_volume", volume_name, "success")
    return {"message": f"Volume '{volume_name}' deleted"}


# Reading one object, registered last so that the fixed paths above (/support, /shared...) keep their routes.
@router.get("/{pool_name}/volumes/{volume_name}")
def get_volume(pool_name: str, volume_name: str, user: dict = Depends(get_current_user)):
    """One volume, as GET /storage/{pool}/volumes lists it."""
    return item(_volumes(pool_name), "name", volume_name, f"Volume '{volume_name}'")


@router.get("/{pool_name}")
def get_pool(pool_name: str, node: str | None = None, user: dict = Depends(get_current_user)):
    """One storage pool, as GET /storage lists it (node: the same convention)."""
    return item(_pools(node), "name", pool_name, f"Storage pool '{pool_name}'")
