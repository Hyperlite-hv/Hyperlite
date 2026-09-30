"""Multi-node management endpoints. The remote connection logic lives in
app/core/cluster.py."""

import logging
import re
import threading

import libvirt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import cluster_compat, maintenance, renaming, vm_locks
from app.core.audit import log_action
from app.core.cluster import (
    get_cluster_pubkey,
    node_summary,
    register_node,
    remove_node,
    rename_reverse_trust,
    test_node_connection,
)
from app.core.database import get_conn
from app.core.error_messages import describe_exception
from app.core.host_capabilities import get_remote_capabilities
from app.core.libvirt_utils import open_conn
from app.core.metrics import get_node_live
from app.core.security import get_current_user, require_role
from app.core.tasks import (
    cancel_requested,
    create_task,
    finish_task,
    register_cancel,
    request_cancel,
    task_log,
    task_status,
    update_task_progress,
)
from app.core.tasks import requester as _cancel_requester
from app.routers.vms.migration import _migrate_vm_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/nodes", tags=["nodes"])

NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.-]{1,62}$")
# A host name or an IPv4/IPv6 address, and a Unix user name: nothing that ssh could read as an option.
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:_-]{0,253}$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


@router.get("")
def list_nodes(user: dict = Depends(get_current_user)):
    """Registered remote nodes, each with its latest live figures ("live", None until the
    metrics collector has reached it once)."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM nodes ORDER BY name").fetchall()
    live = get_node_live()
    return [{**dict(r), "live": live.get(r["name"])} for r in rows]


class NodeTest(BaseModel):
    hostname: str
    ssh_user: str = "root"
    ssh_port: int = Field(22, ge=1, le=65535)


@router.post("/test")
def test_node(payload: NodeTest, user: dict = Depends(require_role("admin"))):
    """Check that a machine can be registered (SSH trust plus a QEMU/KVM libvirt) without
    registering it, so the add-node form can say what is wrong before submitting."""
    if not HOST_RE.match(payload.hostname) or not USER_RE.match(payload.ssh_user):
        raise HTTPException(status_code=422, detail="Invalid address or SSH user")
    ok, detail = test_node_connection(payload.hostname, payload.ssh_user, payload.ssh_port)
    log_action(user["username"], "test_node", payload.hostname, "succes" if ok else "echec", None if ok else detail)
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


@router.get("/cluster-pubkey")
def get_cluster_key(user: dict = Depends(require_role("admin"))):
    """Public key to install in ~/.ssh/authorized_keys of the remote node before
    registering it, displayed in the UI so it can be copied and pasted."""
    return {"public_key": get_cluster_pubkey()}


class NodeCreate(BaseModel):
    name: str
    hostname: str
    ssh_user: str = "root"
    ssh_port: int = Field(22, ge=1, le=65535)


@router.post("", status_code=201)
def add_node(payload: NodeCreate, user: dict = Depends(require_role("admin"))):
    if not NAME_RE.match(payload.name):
        raise HTTPException(status_code=422, detail="Invalid node name (letters/digits/-/., 2-63 characters)")
    try:
        node = register_node(payload.name, payload.hostname, payload.ssh_user, payload.ssh_port, user["username"])
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    # Storage declared for every node (NFS, iSCSI, see app/core/shared_pools.py) is created on the new node too,
    # as on Proxmox. Best-effort: a pool that fails there is reported, the registration stands.
    try:
        from app.routers.storage import apply_shared_pools

        node = {**node, "pools_partages": apply_shared_pools(payload.name, user["username"])}
    except Exception:
        logger.warning("Shared storage not applied to the new node %s", payload.name, exc_info=True)
    # Compatibility diagnostic from the local host to the new node: informational, it
    # never cancels the registration.
    try:
        node = {**node, "compatibilite": _compat_with_local(payload.name)}
    except Exception:
        logger.warning("Compatibility diagnostic for the new node is unavailable", exc_info=True)
    return node


@router.get("/config-copy")
def config_copy_status(user: dict = Depends(require_role("admin"))):
    """When the configuration was last copied to each node (see app/core/config_copy.py)."""
    from app.core import config_copy

    return config_copy.status()


@router.post("/config-copy")
def config_copy_now(user: dict = Depends(require_role("admin"))):
    """Copy the configuration to every online node now, whatever changed."""
    from app.core import config_copy

    results = config_copy.copy_now(force=True, username=user["username"])
    log_action(user["username"], "config_copy", "cluster", "succes", f"{len(results)} node(s)")
    return {"resultats": results, "copies": config_copy.status()}


@router.get("/{name}/summary")
def get_node_summary(name: str, user: dict = Depends(get_current_user)):
    with get_conn() as conn:
        node = conn.execute("SELECT id FROM nodes WHERE name = ?", (name,)).fetchone()
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{name}' not found")
    try:
        return {**node_summary(name), "live": get_node_live(name)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Node unreachable: {e}") from e


def _compat_with_local(name):
    local, remote = open_conn(None), open_conn(name)
    try:
        return cluster_compat.report(cluster_compat.check_pair(local, remote))
    finally:
        local.close()
        remote.close()


@router.get("/{name}/compatibility")
def get_node_compatibility(name: str, user: dict = Depends(require_role("admin"))):
    """Compatibility from the LOCAL host (source) to this node (destination). See
    app/core/cluster_compat.py."""
    with get_conn() as conn:
        if not conn.execute("SELECT id FROM nodes WHERE name = ?", (name,)).fetchone():
            raise HTTPException(status_code=404, detail=f"Node '{name}' not found")
    try:
        return _compat_with_local(name)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Node unreachable: {describe_exception(e)}") from e


@router.get("/{name}/capabilities")
def get_node_capabilities(name: str, user: dict = Depends(get_current_user)):
    """Capability profile of a registered REMOTE node: the equivalent of GET
    /host/capabilities for the local host. See
    app/core/host_capabilities.py::get_remote_capabilities for the known limits
    (some OS information needs the cluster SSH trust to be already established,
    and is degraded to `null` otherwise rather than failing)."""
    with get_conn() as conn:
        node = conn.execute("SELECT id FROM nodes WHERE name = ?", (name,)).fetchone()
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{name}' not found")
    try:
        return get_remote_capabilities(name)
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
def delete_node(name: str, user: dict = Depends(require_role("admin"))):
    with get_conn() as conn:
        node = conn.execute("SELECT id FROM nodes WHERE name = ?", (name,)).fetchone()
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{name}' not found")
    remove_node(name, user["username"])
    maintenance.leave(name)
    return {"message": f"Node '{name}' removed"}


class RenameNodeRequest(BaseModel):
    new_name: str


@router.post("/{name}/rename")
def rename_node(name: str, payload: RenameNodeRequest, user: dict = Depends(require_role("admin"))):
    """The name Hyperlite shows for a registered node; the machine keeps its host name and address. Its VMs'
    settings, notes, metrics and maintenance state follow (app/core/renaming.py)."""
    new = payload.new_name
    if not NAME_RE.match(new) or new == maintenance.LOCAL:
        raise HTTPException(status_code=422, detail="Invalid node name (letters/digits/-/., 2-63 characters)")
    if new == name:
        raise HTTPException(status_code=422, detail="The new name is the current one")
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM nodes WHERE name = ?", (name,)).fetchone():
            raise HTTPException(status_code=404, detail=f"Node '{name}' not found")
        if conn.execute("SELECT 1 FROM nodes WHERE name = ?", (new,)).fetchone():
            raise HTTPException(status_code=409, detail=f"A node named '{new}' already exists")
    busy = vm_locks.busy_on_node(name)
    with _draining_lock:
        if name in _draining:
            busy.append("a drain")
    if busy:
        raise HTTPException(status_code=409, detail=f"Node '{name}' is busy ({', '.join(busy)}): try again later")
    renaming.node_records(name, new)
    rename_reverse_trust(name, new)
    log_action(user["username"], "rename_node", name, "succes", f"-> {new}")
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM nodes WHERE name = ?", (new,)).fetchone()
    return {**dict(row), "live": get_node_live().get(new)}


# --- Maintenance mode (see app/core/maintenance.py). {name} is "local" for the host running Hyperlite.

# Nodes being drained right now: a second drain of the same node would migrate the same VMs twice.
_draining = set()
_draining_lock = threading.Lock()


class MaintenanceRequest(BaseModel):
    # Where the running VMs go; None = only mark the node, every VM stays where it is.
    target_node: str | None = None


def _checked_pair(name, target_node):
    source = maintenance.label(name)
    maintenance.check_node_exists(source)
    target = None
    if target_node is not None:
        target = maintenance.label(target_node)
        maintenance.check_node_exists(target)
        if target == source:
            raise HTTPException(status_code=422, detail="The target node must be different from the node to drain")
        maintenance.refuse_if_in_maintenance(target, "Draining to this node")
    return source, target


def _plan(source, target):
    """The drain plan, or a 502 when a node cannot be reached."""
    try:
        src_conn = open_conn(maintenance.conn_key(source))
    except libvirt.libvirtError as e:
        raise HTTPException(status_code=502, detail=f"Node '{source}' unreachable: {describe_exception(e)}") from e
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


@router.get("/maintenance")
def list_maintenance(user: dict = Depends(get_current_user)):
    return maintenance.list_all()


@router.get("/{name}/drain-plan")
def get_drain_plan(name: str, target_node: str | None = None, user: dict = Depends(require_role("admin"))):
    """What draining would do, without doing it: shown to the admin before confirming."""
    source, target = _checked_pair(name, target_node)
    return _plan(source, target)


def _run_in_background(target, *args):
    threading.Thread(target=target, args=args, daemon=True).start()


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
                    task_log(parent_id, f"Migrating {vm_name} ({i + 1}/{len(names)})")
                    _migrate_vm_job(task_id, username, maintenance.conn_key(source), target, vm_name)
                    current.pop("task", None)
                if task_status(task_id) != "termine":
                    failed.append(vm_name)
            update_task_progress(parent_id, int((i + 1) * 100 / len(names)))
    except Exception as e:
        logger.exception("Drain of %s stopped", source)
        failed.append(f"internal error: {e}")
    finally:
        with _draining_lock:
            _draining.discard(source)
    moved = len(names) - len(failed) - len(not_started)
    summary = f"{moved}/{len(names)} VM(s) migrated to {target}"
    if failed:
        summary += f"; failed: {', '.join(failed)}"
    if not_started:
        why = "drain cancelled" if cancel_requested(parent_id) else "maintenance ended"
        summary += f"; not started ({why}): {', '.join(not_started)}"
    ok = not failed and not not_started
    finish_task(parent_id, "termine" if ok else "echec", None if ok else summary)
    log_action(username, "drain_node", source, "succes" if ok else "echec", summary)


@router.post("/{name}/maintenance", status_code=202)
def enter_maintenance(name: str, payload: MaintenanceRequest, user: dict = Depends(require_role("admin"))):
    """Put a node in maintenance, then live-migrate its running VMs to `target_node`, one after another."""
    source, target = _checked_pair(name, payload.target_node)
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
            result = _plan(source, target)
        except HTTPException as e:
            # A node that is down is still worth marking: it must not become a migration or HA target.
            log_action(user["username"], "enter_maintenance", source, "succes", "node unreachable, nothing migrated")
            raise HTTPException(
                status_code=e.status_code, detail=f"{e.detail}. The node is in maintenance, but no VM was migrated"
            ) from e
        if result["migrables"]:
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
        "succes",
        f"target {target or 'none'}; to migrate: {len(result['migrables'])}; staying: {len(result['non_migrables'])}",
    )
    if task_id:
        _run_in_background(_drain_job, task_id, user["username"], source, target, result["migrables"])
    return {"node": source, "en_maintenance": True, "task_id": task_id, **result}


@router.delete("/{name}/maintenance")
def leave_maintenance(name: str, user: dict = Depends(require_role("admin"))):
    source = maintenance.label(name)
    if not maintenance.leave(source):
        raise HTTPException(status_code=404, detail=f"Node '{source}' is not in maintenance")
    log_action(user["username"], "leave_maintenance", source, "succes")
    return {"node": source, "en_maintenance": False}
