"""Multi-node management endpoints. The remote connection logic lives in
app/core/cluster.py."""

import logging
import threading

import libvirt
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.core import cluster, cluster_compat, cluster_view, maintenance, privileged, renaming, self_node, vm_locks
from app.core.audit import log_action
from app.core.cluster import (
    check_address,
    node_summary,
    register_node,
)
from app.core.error_messages import describe_exception
from app.core.host_capabilities import remote_capabilities
from app.core.libvirt_utils import open_conn
from app.core.listing import listing
from app.core.security import get_current_user, require_role
from app.core.tasks import (
    cancel_requested,
    create_task,
    finish_task,
    register_cancel,
    request_cancel,
    start_thread,
    task_log,
    task_status,
    update_task_progress,
)
from app.core.tasks import requester as _cancel_requester
from app.daemon.registry import DaemonError
from app.routers.vms.migration import _migrate_vm_job
from app.services import node_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/nodes", tags=["nodes"])

NAME_RE = cluster.NODE_NAME_RE

# A host driven over SSH from this node, with no Hyperlite of its own (one controller, nodes over SSH), is deprecated:
# a node joins a cluster of equal nodes instead, as on Proxmox VE, with no controller to lose. Its routes keep working
# for as long as API v1 lasts, and go with v2 (docs/api.md, "Deprecated").
DEPRECATED_SINCE = "@1792800000"  # RFC 9745: 2026-10-24
SSH_HOSTS_DOC = "https://github.com/Hyperlite-hv/Hyperlite/blob/master/docs/cluster-failover.md"


def _deprecated(response: Response):
    response.headers["Deprecation"] = DEPRECATED_SINCE
    response.headers["Link"] = f'<{SSH_HOSTS_DOC}>; rel="deprecation"'


SSH_HOST_ROUTE = {"deprecated": True, "dependencies": [Depends(_deprecated)]}


async def _require(name):
    try:
        await node_service.get(name)
    except node_service.NodeNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from None


@router.get("")
async def list_nodes(user: dict = Depends(get_current_user)):
    """Registered remote nodes, each with its latest live figures ("live", None until the
    metrics collector has reached it once). "member": a member of this node's cluster (it runs Hyperlite and keeps
    its own backups and history, app/core/cluster_view.py), not a node driven over SSH only."""
    nodes = await node_service.list_with_live()
    members = {n["name"] for n in await run_in_threadpool(cluster_view.members)}
    return listing([{**n, "member": n["name"] in members} for n in nodes])


class NodeTest(BaseModel):
    hostname: str
    ssh_user: str = "root"
    ssh_port: int = Field(22, ge=1, le=65535)


@router.post("/test", **SSH_HOST_ROUTE)
def test_node(payload: NodeTest, user: dict = Depends(require_role("admin"))):
    """Check that a machine can be registered (SSH trust plus a QEMU/KVM libvirt) without
    registering it, so the add-node form can say what is wrong before submitting."""
    try:
        check_address(payload.hostname, payload.ssh_user)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    # Asked of hyperlite-daemon, which alone holds the cluster's key.
    ok, detail = cluster.test_from_daemon(payload.hostname, payload.ssh_user, payload.ssh_port, user["username"])
    log_action(user["username"], "test_node", payload.hostname, "success" if ok else "failed", None if ok else detail)
    # The raw libvirt/SSH error stays in the audit log; the browser gets a fixed reason.
    if ok:
        return {"ok": True, "detail": payload.hostname}
    return {"ok": False, "detail": _connection_failure(detail)}


def _connection_failure(raw):
    text = (raw or "").lower()
    if "permission denied" in text or "publickey" in text or "authentication" in text:
        return "SSH authentication refused: authorize the cluster key for this user."
    if "host key verification" in text:
        return "SSH host key verification failed."
    if "timed out" in text or "timeout" in text:
        return "The connection timed out."
    if (
        "no route" in text
        or "unreachable" in text
        or "name or service not known" in text
        or "could not resolve" in text
    ):
        return "The host cannot be reached from this server."
    if "connection refused" in text:
        return "The SSH port refused the connection."
    if "hypervisor" in text and "qemu/kvm expected" in text:
        return "This host does not run a QEMU/KVM hypervisor."
    if "libvirt" in text or "socket" in text:
        return "libvirt does not answer on this host: is libvirtd running?"
    return "The connection failed: the details are in the audit log."


@router.get("/cluster-pubkey", **SSH_HOST_ROUTE)
def get_cluster_key(user: dict = Depends(require_role("admin"))):
    """Public key to install in ~/.ssh/authorized_keys of the remote node before
    registering it, displayed in the UI so it can be copied and pasted."""
    return {"public_key": cluster.public_key()}


class NodeCreate(BaseModel):
    name: str
    hostname: str
    ssh_user: str = "root"
    ssh_port: int = Field(22, ge=1, le=65535)


def _refuse_own_name(name):
    # The shared tables store this node's rows under its own name (app/core/self_node.py): a registered node of the
    # same name would read as this one.
    if name.lower() in (maintenance.LOCAL, self_node.name()):
        raise HTTPException(status_code=422, detail=f"'{name}' is this node's own name: choose another one")


@router.post("", status_code=201, **SSH_HOST_ROUTE)
def add_node(payload: NodeCreate, user: dict = Depends(require_role("admin"))):
    if not NAME_RE.match(payload.name):
        raise HTTPException(status_code=422, detail="Invalid node name (letters/digits/-/., 2-63 characters)")
    _refuse_own_name(payload.name)
    try:
        check_address(payload.hostname, payload.ssh_user)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    logger.warning(
        "%s is registered as a host driven over SSH, which is deprecated: install Hyperlite on it and join the cluster",
        payload.name,
    )
    try:
        node = register_node(payload.name, payload.hostname, payload.ssh_user, payload.ssh_port, user["username"])
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    # Storage declared for every node (NFS, iSCSI, see app/core/shared_pools.py) is created on the new node too,
    # as on Proxmox. Best-effort: a pool that fails there is reported, the registration stands.
    try:
        from app.routers.storage import apply_shared_pools

        node = {**node, "shared_pools": apply_shared_pools(payload.name, user["username"])}
    except Exception:
        logger.warning("Shared storage not applied to the new node %s", payload.name, exc_info=True)
    # Compatibility diagnostic from the local host to the new node: informational, it
    # never cancels the registration.
    try:
        node = {**node, "compatibility": _compat_with_local(payload.name)}
    except Exception:
        logger.warning("Compatibility diagnostic for the new node is unavailable", exc_info=True)
    return node


@router.get("/config-copy", **SSH_HOST_ROUTE)
def config_copy_status(user: dict = Depends(require_role("admin"))):
    """When the configuration was last copied to each node (see app/core/config_copy.py)."""
    from app.core import config_copy

    return config_copy.status()


@router.post("/config-copy", **SSH_HOST_ROUTE)
def config_copy_now(user: dict = Depends(require_role("admin"))):
    """Copy the configuration to every online node now, whatever changed."""
    from app.core import config_copy

    results = config_copy.copy_now(force=True, username=user["username"])
    log_action(user["username"], "config_copy", "cluster", "success", f"{len(results)} node(s)")
    return {"results": results, "copies": config_copy.status()}


@router.get("/{name}/summary")
async def get_node_summary(name: str, user: dict = Depends(get_current_user)):
    await _require(name)
    try:
        return {**await run_in_threadpool(node_summary, name), "live": await node_service.live(name)}
    except HTTPException:  # the node seen down (503) or not answering (502), already said
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Node unreachable: {e}") from e


def _compat_with_local(name):
    local, remote = open_conn(None), open_conn(name, readonly=True)
    try:
        return cluster_compat.report(cluster_compat.check_pair(local, remote))
    finally:
        local.close()
        remote.close()


@router.get("/{name}/compatibility")
async def get_node_compatibility(name: str, user: dict = Depends(require_role("admin"))):
    """Compatibility from the LOCAL host (source) to this node (destination). See
    app/core/cluster_compat.py."""
    await _require(name)
    try:
        return await run_in_threadpool(_compat_with_local, name)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Node unreachable: {describe_exception(e)}") from e


@router.get("/{name}/capabilities")
async def get_node_capabilities(name: str, user: dict = Depends(get_current_user)):
    """Capability profile of a registered REMOTE node: the equivalent of GET
    /host/capabilities for the local host. See
    app/core/host_capabilities.py::get_remote_capabilities for the known limits
    (some OS information needs the cluster SSH trust to be already established,
    and is degraded to `null` otherwise rather than failing)."""
    await _require(name)
    try:
        return await run_in_threadpool(remote_capabilities, name)
    except DaemonError as e:  # the node seen down (503) or not answering (502), said by the daemon
        raise privileged.http_error(e, failed="Node unreachable: {}") from None
    except Exception as e:
        msg = describe_exception(e)
        raise HTTPException(status_code=502, detail=f"Node unreachable: {msg}") from e


@router.get("/{name}/hardware")
def get_node_hardware(name: str, user: dict = Depends(get_current_user)):
    """Network interfaces, physical disks and CPU topology of a node ('local' = this host)."""
    from app.core.cluster import get_node
    from app.core.node_hardware import get_hardware

    node = None
    if name != "local":
        node = get_node(name)
        if not node:
            raise HTTPException(status_code=404, detail=f"Node '{name}' not found")
    try:
        return get_hardware(node)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=f"Node unreachable: {e}") from e


@router.delete("/{name}")
async def delete_node(name: str, user: dict = Depends(require_role("admin"))):
    try:
        await node_service.remove(name, user["username"])
    except node_service.NodeNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    return {"message": f"Node '{name}' removed"}


class RenameNodeRequest(BaseModel):
    new_name: str


@router.post("/{name}/rename")
async def rename_node(name: str, payload: RenameNodeRequest, user: dict = Depends(require_role("admin"))):
    """The name Hyperlite shows for a registered node; the machine keeps its host name and address. Its VMs'
    settings, notes, metrics and maintenance state follow (app/core/renaming.py)."""
    new = payload.new_name
    if not NAME_RE.match(new) or new == maintenance.LOCAL:
        raise HTTPException(status_code=422, detail="Invalid node name (letters/digits/-/., 2-63 characters)")
    if new == name:
        raise HTTPException(status_code=422, detail="The new name is the current one")
    _refuse_own_name(new)
    try:
        await node_service.check_rename(name, new)
    except node_service.NodeNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except node_service.NodeNameTaken as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    busy = vm_locks.busy_on_node(name)
    with _draining_lock:
        if name in _draining:
            busy.append("a drain")
    if busy:
        raise HTTPException(status_code=409, detail=f"Node '{name}' is busy ({', '.join(busy)}): try again later")
    await run_in_threadpool(renaming.node_records, name, new)
    await run_in_threadpool(privileged.run, "cluster.rename_trust", user=user["username"], node=name, new_name=new)
    log_action(user["username"], "rename_node", name, "success", f"-> {new}")
    return await node_service.wire_with_live(new)


# --- Maintenance mode (see app/core/maintenance.py). {name} is "local" for the host running Hyperlite.

# Nodes being drained right now: a second drain of the same node would migrate the same VMs twice.
_draining = set()
_draining_lock = threading.Lock()


class MaintenanceRequest(BaseModel):
    # Where the running VMs go; None = only mark the node, every VM stays where it is.
    target_node: str | None = None
    # Each running VM where load balancing places it instead (docs/design/drs.md, 3.6): never with a target_node.
    spread: bool = False


def _checked_pair(name, target_node, spread=False):
    source = maintenance.label(name)
    maintenance.check_node_exists(source)
    if spread and target_node is not None:
        raise HTTPException(status_code=422, detail="Either a target node or the spread by load balancing, not both")
    target = None
    if target_node is not None:
        target = maintenance.label(target_node)
        maintenance.check_node_exists(target)
        if target == source:
            raise HTTPException(status_code=422, detail="The target node must be different from the node to drain")
        maintenance.refuse_if_in_maintenance(target, "Draining to this node")
    return source, target


def _plan(source, target, spread=False):
    """The drain plan, or a 502 when a node cannot be reached."""
    try:
        src_conn = open_conn(maintenance.conn_key(source))
    except libvirt.libvirtError as e:
        raise HTTPException(status_code=502, detail=f"Node '{source}' unreachable: {describe_exception(e)}") from e
    if spread:
        try:
            return _spread_plan(source, src_conn)
        finally:
            src_conn.close()
    dst_conn = None
    try:
        if target is not None:
            try:
                dst_conn = open_conn(maintenance.conn_key(target))
            except libvirt.libvirtError as e:
                raise HTTPException(
                    status_code=502, detail=f"Node '{target}' unreachable: {describe_exception(e)}"
                ) from e
        return maintenance.plan(src_conn, dst_conn, target)
    finally:
        src_conn.close()
        if dst_conn:
            dst_conn.close()


def _spread_plan(source, src_conn):
    """The drain plan when each running VM goes where load balancing places it, with "targets": {vm: label}."""
    from app.core import drs, self_node

    me = self_node.name()
    running = [d.name() for d in src_conn.listAllDomains(libvirt.VIR_CONNECT_LIST_DOMAINS_ACTIVE)]
    placed = drs.spread(me if source == maintenance.LOCAL else source, running)
    targets = {vm: (maintenance.LOCAL if n == me else n) if n else None for vm, n in placed.items()}
    conns = {}
    try:
        for label in {t for t in targets.values() if t}:
            try:
                conns[label] = open_conn(maintenance.conn_key(label))
            except libvirt.libvirtError:
                conns[label] = None  # its VMs stay, said why
        return maintenance.spread_plan(src_conn, targets, conns)
    finally:
        for conn in conns.values():
            if conn:
                conn.close()


@router.get("/maintenance")
def list_maintenance(user: dict = Depends(get_current_user)):
    return listing(maintenance.list_all())


@router.get("/{name}/drain-plan")
def get_drain_plan(
    name: str, target_node: str | None = None, spread: bool = False, user: dict = Depends(require_role("admin"))
):
    """What draining would do, without doing it: shown to the admin before confirming."""
    source, target = _checked_pair(name, target_node, spread)
    return _plan(source, target, spread)


def _run_in_background(target, task_id, *args):
    start_thread(target, task_id, *args, task_id=task_id)


def _drain_job(parent_id, username, source, target, names):
    failed, not_started = [], []
    current = {}

    def abort():
        # Cancelling the drain stops the migration in flight too; the VMs after it are not started.
        child = current.get("task")
        if child:
            request_cancel(child, _cancel_requester(parent_id))

    register_cancel(parent_id, abort)
    try:
        for i, vm_name in enumerate(names):
            if cancel_requested(parent_id):
                not_started = names[i:]
                break
            if not maintenance.get(source):
                # The admin ended the maintenance: stop moving VMs away.
                not_started = names[i:]
                break
            try:
                claim = vm_locks.claim(vm_name, "a migration (node drain)", node=source)
            except vm_locks.VmBusy as busy:
                failed.append(f"{vm_name} ({busy.running} in progress)")
            else:
                with claim:
                    task_id = create_task("migrate_vm", vm_name, node=source, username=username)
                    current["task"] = task_id
                    to = target[vm_name] if isinstance(target, dict) else target  # spread: each its own
                    task_log(parent_id, f"Migrating {vm_name} to {to} ({i + 1}/{len(names)})")
                    _migrate_vm_job(task_id, username, maintenance.conn_key(source), to, vm_name)
                    current.pop("task", None)
                if task_status(task_id) != "succeeded":
                    failed.append(vm_name)
            update_task_progress(parent_id, int((i + 1) * 100 / len(names)))
    except Exception as e:
        logger.exception("Drain of %s stopped", source)
        failed.append(f"internal error: {e}")
    finally:
        with _draining_lock:
            _draining.discard(source)
    moved = len(names) - len(failed) - len(not_started)
    summary = f"{moved}/{len(names)} VM(s) migrated to " + (
        "the members load balancing chose" if isinstance(target, dict) else target
    )
    if failed:
        summary += f"; failed: {', '.join(failed)}"
    if not_started:
        why = "drain cancelled" if cancel_requested(parent_id) else "maintenance ended"
        summary += f"; not started ({why}): {', '.join(not_started)}"
    ok = not failed and not not_started
    finish_task(parent_id, "succeeded" if ok else "failed", None if ok else summary)
    log_action(username, "drain_node", source, "success" if ok else "failed", summary)


@router.post("/{name}/maintenance", status_code=202)
def enter_maintenance(name: str, payload: MaintenanceRequest, user: dict = Depends(require_role("admin"))):
    """Put a node in maintenance, then live-migrate its running VMs to `target_node`, or each where load balancing
    places it (`spread`), one after another."""
    source, target = _checked_pair(name, payload.target_node, payload.spread)
    # Reserved under the lock, planned outside it: the plan talks to both nodes and can take seconds.
    with _draining_lock:
        if source in _draining:
            raise HTTPException(status_code=409, detail=f"Node '{source}' is already being drained")
        _draining.add(source)
    task_id = None
    try:
        # Marked before the plan: from now on no VM can be created on it or sent to it.
        maintenance.enter(source, user["username"])
        try:
            result = _plan(source, target, payload.spread)
        except HTTPException as e:
            # A node that is down is still worth marking: it must not become a migration or HA target.
            log_action(user["username"], "enter_maintenance", source, "success", "node unreachable, nothing migrated")
            raise HTTPException(
                status_code=e.status_code, detail=f"{e.detail}. The node is in maintenance, but no VM was migrated"
            ) from e
        if result["migratable"]:
            task_id = create_task("drain_node", source, node=source, username=user["username"])
    finally:
        if task_id is None:
            # Nothing to drain (or it failed before starting): release the reservation; the drain job does it
            # otherwise.
            with _draining_lock:
                _draining.discard(source)
    log_action(
        user["username"],
        "enter_maintenance",
        source,
        "success",
        f"target {'spread' if payload.spread else target or 'none'}; to migrate: {len(result['migratable'])}; staying: {len(result['not_migratable'])}",
    )
    if task_id:
        where = result["targets"] if payload.spread else target
        _run_in_background(_drain_job, task_id, user["username"], source, where, result["migratable"])
    return {"node": source, "in_maintenance": True, "task_id": task_id, **result}


@router.delete("/{name}/maintenance")
def leave_maintenance(name: str, user: dict = Depends(require_role("admin"))):
    source = maintenance.label(name)
    if not maintenance.leave(source):
        raise HTTPException(status_code=404, detail=f"Node '{source}' is not in maintenance")
    log_action(user["username"], "leave_maintenance", source, "success")
    return {"node": source, "in_maintenance": False}
