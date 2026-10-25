"""LXC containers: CRUD + lifecycle + web terminal + clone/backup. There is no
instantaneous snapshot: libvirt's LXC driver does not support it at all. There
is still no per-container firewall. Restricted to administrators like the other
resource creation endpoints (isos.py, templates.py), with the same granular
ACLs as VMs (app/core/permissions.py)."""

import contextlib
import logging
import secrets
import threading
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import libvirt
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.core import (
    container_config,
    container_shell,
    container_snapshots,
    image_env,
    maintenance,
    object_meta,
    paths,
    privileged,
    pty_session,
    renaming,
)
from app.core.audit import log_action
from app.core.container_create import ContainerCreate
from app.core.container_meta import (
    delete_container_app,
    delete_container_ssh_user,
    delete_container_storage,
    get_container_app,
    get_container_ssh_user,
    get_container_storage,
    set_container_app,
    set_container_ssh_user,
    set_container_storage,
)
from app.core.docker_hub import search_images
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import open_lxc_conn, open_lxc_conn_if_any
from app.core.listing import listing
from app.core.security import get_current_user, require_container_privilege, require_role
from app.core.tasks import create_task, finish_task, update_task_progress
from app.core.vm_builder import (
    validate_name,
)
from app.daemon.registry import DaemonError
from app.services import backup_service

logger = logging.getLogger(__name__)

CONTAINER_BACKUP_DIR = paths.CONTAINER_BACKUPS

router = APIRouter(prefix="/containers", tags=["containers"])

STATE_NAMES = {
    libvirt.VIR_DOMAIN_NOSTATE: "unknown",
    libvirt.VIR_DOMAIN_RUNNING: "active",
    libvirt.VIR_DOMAIN_BLOCKED: "blocked",
    libvirt.VIR_DOMAIN_PAUSED: "paused",
    libvirt.VIR_DOMAIN_SHUTDOWN: "shutting_down",
    libvirt.VIR_DOMAIN_SHUTOFF: "stopped",
    libvirt.VIR_DOMAIN_CRASHED: "crashed",
    libvirt.VIR_DOMAIN_PMSUSPENDED: "suspended",
}


def _get_ip(domain):
    try:
        ifaces = domain.interfaceAddresses(libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_LEASE)
        for iface in ifaces.values():
            for addr in iface.get("addrs", []):
                if addr.get("type") == 0:
                    return addr.get("addr")
    except libvirt.libvirtError:
        logger.debug("Ignored exception in _get_ip()", exc_info=True)
    return None


def _summary(domain):
    active = domain.isActive()
    state, maxmem, _mem, nvcpu, _cputime = domain.info()
    app = get_container_app(domain.name())
    storage = get_container_storage(domain.name())
    return {
        "storage": storage["pool"] if storage else None,
        "mode": "application" if app else "system",
        "image": app["image"] if app else None,
        "name": domain.name(),
        "id": domain.ID() if active else None,
        "uuid": domain.UUIDString(),
        "state": STATE_NAMES.get(state, "unknown"),
        "vcpu": nvcpu,
        "memory_mb": maxmem // 1024,
        # An application container has no DHCP lease: its address is the one libvirt set on its interface.
        "ip": (app["ip"] if app else _get_ip(domain)) if active else None,
    }


@router.get("")
def list_containers(user: dict = Depends(get_current_user)):
    conn = open_lxc_conn_if_any()
    if conn is None:
        return listing([])  # no LXC driver on this node: no containers, as libvirt_utils.open_lxc_conn_if_any says
    try:
        return listing([_summary(d) for d in conn.listAllDomains()])
    finally:
        conn.close()


@router.get("/docker-hub/search")
def search_docker_hub(q: str = "", user: dict = Depends(get_current_user)):
    try:
        return listing(search_images(q))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.get("/image-env")
def image_environment(image: str = "", user: dict = Depends(get_current_user)):
    """The environment variables a well-known image needs to start, and useful ones (app/core/image_env.py)."""
    return image_env.hints(image)


# IMPORTANT: must stay registered BEFORE @router.get("/{name}") below. FastAPI
# and Starlette resolve routes by registration order, not by specificity:
# otherwise GET /containers/backups is intercepted by GET /{name} and returns
# "Container 'backups' not found" instead of the list of backups.
@router.get("/backups")
async def list_container_backups(user: dict = Depends(get_current_user)):
    return listing(await backup_service.list_container_backups())


@router.get("/{name}")
def get_container(name: str, user: dict = Depends(require_container_privilege("container.view"))):
    conn = open_lxc_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"Container '{name}' not found") from None
        return _detail(domain)
    finally:
        conn.close()


def _detail(domain):
    """The summary plus what the container's own page shows: interfaces, DNS servers, start at boot."""
    name = domain.name()
    summary = _summary(domain)
    summary["ssh_user"] = get_container_ssh_user(name)
    summary["interfaces"] = container_config.interfaces(domain)
    summary["dns"] = container_config.read_dns(name)
    summary["autostart"] = bool(domain.autostart())
    return summary


class ContainerUpdate(BaseModel):
    vcpu: int | None = Field(default=None, ge=1)
    memory_mb: int | None = Field(default=None, ge=128)
    # Started with the host (libvirt's autostart: containers start in no particular order, and quickly).
    autostart: bool | None = None
    dns: list[str] | None = Field(default=None, max_length=8)


@router.patch("/{name}")
def update_container(name: str, payload: ContainerUpdate, user: dict = Depends(require_role("admin"))):
    # Written by hyperlite-daemon (app/core/container_ops.py), the DNS servers into the container's own files.
    changed = _daemon("container.update", user, "update_container", name, **payload.model_dump())
    conn = open_lxc_conn()
    try:
        domain = _lookup(conn, name)
        log_action(user["username"], "update_container", name, "success", "; ".join(changed["changes"]))
        return {**_detail(domain), "needs_restart": changed["needs_restart"]}
    finally:
        conn.close()


def _daemon(op, user, action, name, **args):
    """`op` run by hyperlite-daemon on the container `name`: its result, or its refusal or failure recorded as
    `action` and answered (a refusal 422, what is in use 409, what does not exist 404)."""
    try:
        return privileged.run(op, user=user["username"], container=name, **args)
    except DaemonError as e:
        reason = "; ".join(e.detail) if isinstance(e.detail, list) else e.detail
        log_action(user["username"], action, name, "failed", reason)
        raise privileged.http_error(e) from None


def _domain_mac(domain):
    mac = ET.fromstring(domain.XMLDesc(0)).find(".//devices/interface/mac")
    return mac.get("address") if mac is not None else None


@router.post("", status_code=201)
def create_container(payload: ContainerCreate, user: dict = Depends(require_role("admin"))):
    # Checked, then made by hyperlite-daemon (app/core/container_create.py); its records are written here.
    maintenance.refuse_if_in_maintenance("local", "Container creation")
    mode = payload.mode or ("application" if payload.image else "system")
    # Asked of the registry before the download: an official postgres image stops at once without its password.
    before = image_env.missing(payload.image, payload.env) if mode == "application" else []
    spec = payload.model_dump()
    try:
        privileged.run("container.create", user=user["username"], spec=spec, check_only=True)
    except DaemonError as e:
        if e.kind != "refused" or not isinstance(e.detail, list) or not before:
            raise privileged.http_error(e) from None
        before = [*e.detail, *before]
    if before:
        raise HTTPException(status_code=422, detail=before)
    task_id = create_task("create_container", payload.name, node=None, username=user["username"])
    try:
        made = privileged.run(
            "container.create", user=user["username"], timeout=CREATE_TIMEOUT_S, spec=spec, check_only=False
        )
    except DaemonError as e:
        reason = "; ".join(e.detail) if isinstance(e.detail, list) else e.detail
        log_action(user["username"], "create_container", payload.name, "failed", reason, task_id=task_id)
        raise privileged.http_error(e) from None
    if made["pool"]:
        set_container_storage(payload.name, made["pool"], made["base_dir"])
    if made["app"]:
        set_container_app(payload.name, payload.image, made["app"]["spec"], made["app"]["ip"], payload.network)
    else:
        set_container_ssh_user(payload.name, payload.username)
    if made["start_error"]:
        msg = made["start_error"]
        log_action(
            user["username"],
            "create_container",
            payload.name,
            "failed",
            f"defined, start failed: {msg}",
            task_id=task_id,
        )
        raise HTTPException(status_code=500, detail=f"Container defined but its process did not start: {msg}")
    detail = f"application {payload.image}" if made["app"] else None
    log_action(user["username"], "create_container", payload.name, "success", detail, task_id=task_id)
    conn = open_lxc_conn()
    try:
        return _summary(_lookup(conn, payload.name))
    finally:
        conn.close()


# A creation or a clone is one call to hyperlite-daemon that waits: an image is pulled, a filesystem copied.
CREATE_TIMEOUT_S = 2 * 3600


def _claim_or_409(name, what):
    """One operation at a time on a container's files (app/core/container_snapshots.py): a snapshot being taken or
    restored, a backup, a clone, a rename, a deletion, a start."""
    try:
        return container_snapshots.claim(name, what)
    except container_snapshots.ContainerBusy as e:
        raise HTTPException(status_code=409, detail=str(e)) from None


def _lookup(conn, name):
    try:
        return conn.lookupByName(name)
    except libvirt.libvirtError:
        raise HTTPException(status_code=404, detail=f"Container '{name}' not found") from None


@router.post("/{name}/start")
def start_container(name: str, user: dict = Depends(require_container_privilege("container.power"))):
    conn = open_lxc_conn()
    try:
        domain = _lookup(conn, name)
        with _claim_or_409(name, "a start"):
            _daemon("container.start", user, "start_container", name)
        log_action(user["username"], "start_container", name, "success")
        return _summary(domain)
    finally:
        conn.close()


# How long a container gets to stop by itself before it is stopped by force, as `docker stop` does (10 s there).
# An application container's process gets SIGTERM; a system container's init shuts its services down first.
STOP_GRACE_S = {"application": 30, "system": 90}


def _force_stop_later(name, run_id, grace_s):
    """Stop the container by force when it is still running after grace_s: a process can ignore SIGTERM (a shell
    script as PID 1, or an image's entrypoint still initializing), and a stop must end with a stopped container.
    run_id (the domain's ID, the pid of its libvirt controller) tells the run that was asked to stop from a new one
    started meanwhile, which hyperlite-daemon leaves alone."""
    deadline = time.monotonic() + grace_s
    conn = open_lxc_conn()
    try:
        while time.monotonic() < deadline:
            time.sleep(1)
            try:
                domain = conn.lookupByName(name)
                if not domain.isActive() or domain.ID() != run_id:
                    return
            except libvirt.libvirtError:
                return  # deleted meanwhile
    finally:
        conn.close()
    try:
        done = privileged.run("container.stop", user="system", container=name, force=True, run_id=run_id)
    except DaemonError as e:
        logger.info("Forced stop of %s not needed or failed: %s", name, e.detail)
        return
    if done["stopped"]:
        log_action("system", "stop_container", name, "success", f"stopped by force: still running after {grace_s} s")


@router.post("/{name}/stop")
def stop_container(
    name: str, force: bool = False, user: dict = Depends(require_container_privilege("container.power"))
):
    conn = open_lxc_conn()
    try:
        _lookup(conn, name)
    finally:
        conn.close()
    application = bool(get_container_app(name))
    done = _daemon("container.stop", user, "stop_container", name, force=force, application=application)
    grace = None
    if not force:
        grace = STOP_GRACE_S["application" if application else "system"]
        threading.Thread(target=_force_stop_later, args=(name, done["run_id"], grace), daemon=True).start()
    log_action(user["username"], "stop_container", name, "success", "forced" if force else None)
    return {"ok": True, "force_stop_after_s": grace}


@router.delete("/{name}")
def delete_container(name: str, confirm: bool = False, user: dict = Depends(require_role("admin"))):
    # Irreversible (its filesystem goes with it), as for a VM, a pool or a backup: it was deleted at the first call.
    if not confirm:
        raise HTTPException(status_code=400, detail="Irreversible action: add ?confirm=true to confirm the deletion")
    with _claim_or_409(name, "a deletion"):
        return _delete_container(name, user)


def _delete_container(name, user):
    """Stopped, undefined, its snapshots and its filesystem removed by hyperlite-daemon (app/core/container_ops.py),
    which leaves a filesystem outside Hyperlite's container directories in place; its records go here."""
    application = bool(get_container_app(name))
    done = _daemon("container.delete", user, "delete_container", name, application=application)
    delete_container_storage(name)
    delete_container_ssh_user(name)
    object_meta.delete("container", name)
    if application:
        delete_container_app(name)
    kept = done["filesystem_kept"]
    log_action(user["username"], "delete_container", name, "success", f"filesystem {kept} kept" if kept else None)
    return {"ok": True}


class RenameContainerRequest(BaseModel):
    new_name: str


@router.post("/{name}/rename")
def rename_container(name: str, payload: RenameContainerRequest, user: dict = Depends(require_role("admin"))):
    """libvirt's LXC driver cannot rename a domain: the stopped container is redefined under its new name, its
    filesystem directory is renamed next to the old one (its snapshots' too), and Hyperlite's records follow
    (app/core/renaming.py)."""
    with _claim_or_409(name, "a rename"):
        return _rename_container(name, payload, user)


def _rename_container(name, payload, user):
    new = payload.new_name
    conn = open_lxc_conn()
    try:
        _lookup(conn, name)
    finally:
        conn.close()
    _daemon("container.rename", user, "rename_container", name, new_name=new)
    renaming.container_records(name, new)
    log_action(user["username"], "rename_container", name, "success", f"-> {new}")
    conn = open_lxc_conn()
    try:
        return _summary(_lookup(conn, new))
    finally:
        conn.close()


# ---- Clone + backup/restore ----
# There is no instantaneous snapshot: libvirt's LXC driver does not support
# virDomainSnapshotCreateXML (see app/core/container_builder.py for the detail).
# Clone = a full copy of the rootfs; backup = a tar archive (without scheduling or
# retention for now); snapshot = an rsync copy of the stopped container's directory (below).


class CloneContainerRequest(BaseModel):
    new_name: str


@router.post("/{name}/clone", status_code=201)
def clone_container(name: str, payload: CloneContainerRequest, user: dict = Depends(require_role("admin"))):
    maintenance.refuse_if_in_maintenance("local", "Cloning")
    with _claim_or_409(name, "a clone"):
        return _clone_container(name, payload, user)


def _clone_container(name, payload, user):
    # Copied by hyperlite-daemon (app/core/container_create.py) in its source's directory; the records follow here.
    conn = open_lxc_conn()
    try:
        _lookup(conn, name)
    finally:
        conn.close()
    task_id = create_task("clone_container", name, node=None, username=user["username"])
    app = get_container_app(name)
    try:
        made = privileged.run(
            "container.clone",
            user=user["username"],
            timeout=CREATE_TIMEOUT_S,
            container=name,
            new_name=payload.new_name,
            app=app["spec"] if app else None,
        )
    except DaemonError as e:
        log_action(user["username"], "clone_container", name, "failed", e.detail, task_id=task_id)
        raise privileged.http_error(e) from None
    storage = get_container_storage(name)
    if storage:  # a clone stays in its source's storage pool
        set_container_storage(payload.new_name, storage["pool"], storage["base_dir"])
    ssh_user = get_container_ssh_user(name)
    if ssh_user:
        set_container_ssh_user(payload.new_name, ssh_user)
    if app:
        set_container_app(payload.new_name, app["image"], app["spec"], made["ip"], made["network"])
    log_action(user["username"], "clone_container", name, "success", f"-> {payload.new_name}", task_id=task_id)
    conn = open_lxc_conn()
    try:
        return _summary(_lookup(conn, payload.new_name))
    finally:
        conn.close()


def _domain_network(domain):
    root = ET.fromstring(domain.XMLDesc(0))
    iface = root.find(".//devices/interface[@type='network']/source")
    return iface.get("network") if iface is not None else "default"


class ContainerBackupRunning(Exception):
    pass


_container_backup_lock = threading.Lock()  # same reasoning as the VM _backup_lock: one at a time


def _run_container_backup_job(task_id, username, container_name, claim):
    with claim:
        _container_backup(task_id, username, container_name)


def _container_backup(task_id, username, container_name):
    """The task and the record are the API's; hyperlite-daemon archives the filesystem its definition names
    (container.archive)."""
    store = backup_service.container_store()
    backup_id = store.create_container_backup(container_name, "", datetime.now(UTC).isoformat(), task_id)
    try:
        with _container_backup_lock:
            update_task_progress(task_id, 10)
            try:
                done = privileged.run(
                    "container.archive", user=username, timeout=ARCHIVE_TIMEOUT_S, container=container_name
                )
            except DaemonError as e:
                raise RuntimeError(e.detail) from None
            update_task_progress(task_id, 90)
        store.set_container_backup_path(backup_id, done["path"])
        store.container_backup_done(backup_id, done["size"])
        finish_task(task_id, "succeeded")
        log_action(username, "backup_container", container_name, "success")
    except Exception as e:
        store.container_backup_failed(backup_id, str(e)[:500])
        finish_task(task_id, "failed", str(e)[:500])
        log_action(username, "backup_container", container_name, "failed", str(e)[:500])


# An archive of a large filesystem takes a while.
ARCHIVE_TIMEOUT_S = 6 * 3600


@router.post("/{name}/backups", status_code=202)
def create_container_backup(name: str, user: dict = Depends(require_role("admin"))):
    conn = open_lxc_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"Container '{name}' not found") from None
        if domain.isActive():
            raise HTTPException(
                status_code=409, detail="Stop the container before backing it up (no hot backup for containers)"
            )
    finally:
        conn.close()
    claim = _claim_or_409(name, "a backup")
    try:
        task_id = create_task("backup_container", name, username=user["username"])
        threading.Thread(
            target=_run_container_backup_job, args=(task_id, user["username"], name, claim), daemon=True
        ).start()
    except BaseException:
        claim.release()
        raise
    return {"task_id": task_id}


# ---- Snapshots (docs/design/container-snapshots.md): copies of the stopped container's directory ----


class ContainerSnapshotCreate(BaseModel):
    name: str
    description: str | None = Field(default=None, max_length=500)


def _snapshot_job(task_id, username, action, name, snapshot, claim, op, args):
    with claim:
        try:
            privileged.run(op, user=username, timeout=SNAPSHOT_TIMEOUT_S, container=name, snapshot=snapshot, **args)
            finish_task(task_id, "succeeded")
            log_action(username, action, name, "success", f"Snapshot '{snapshot}'")
        except Exception as e:  # any error closes the task, said as it is
            msg = e.detail if isinstance(e, DaemonError) else describe_exception(e)
            finish_task(task_id, "failed", msg)
            log_action(username, action, name, "failed", f"Snapshot '{snapshot}': {msg}")


# Copying a container's files takes longer than an ordinary operation.
SNAPSHOT_TIMEOUT_S = 3600


def _daemon_task(op, user, action, name, snapshot, what, **args):
    """Checked by hyperlite-daemon first (check_only), so that a refusal is the answer; then the work as a task,
    holding the container's lock until it ends."""
    try:
        privileged.run(op, user=user["username"], container=name, snapshot=snapshot, check_only=True, **args)
    except DaemonError as e:
        raise privileged.http_error(e) from None
    claim = _claim_or_409(name, what)
    try:
        task_id = create_task(action, name, username=user["username"])
        threading.Thread(
            target=_snapshot_job,
            args=(task_id, user["username"], action, name, snapshot, claim, op, args),
            daemon=True,
        ).start()
    except BaseException:
        claim.release()
        raise
    return {"task_id": task_id, "name": snapshot, "status": "running"}


@router.get("/{name}/snapshots")
def list_container_snapshots(name: str, user: dict = Depends(require_container_privilege("container.view"))):
    # Read by hyperlite-daemon: the snapshots are root's.
    try:
        found = privileged.run("container.snapshot_list", user=user["username"], container=name)["snapshots"]
    except DaemonError as e:
        raise privileged.http_error(e) from None
    return listing(found)


@router.post("/{name}/snapshots", status_code=202)
def create_container_snapshot(name: str, payload: ContainerSnapshotCreate, user: dict = Depends(require_role("admin"))):
    return _daemon_task(
        "container.snapshot_create",
        user,
        "create_container_snapshot",
        name,
        payload.name,
        "a snapshot",
        description=payload.description,
    )


@router.post("/{name}/snapshots/{snapshot}/restore", status_code=202)
def restore_container_snapshot(name: str, snapshot: str, user: dict = Depends(require_role("admin"))):
    return _daemon_task(
        "container.snapshot_restore", user, "restore_container_snapshot", name, snapshot, "a snapshot restore"
    )


@router.delete("/{name}/snapshots/{snapshot}")
def delete_container_snapshot(name: str, snapshot: str, user: dict = Depends(require_role("admin"))):
    with _claim_or_409(name, "a snapshot deletion"):
        try:
            privileged.run("container.snapshot_delete", user=user["username"], container=name, snapshot=snapshot)
        except DaemonError as e:
            if e.kind != "not_found":
                log_action(
                    user["username"], "delete_container_snapshot", name, "failed", f"Snapshot '{snapshot}': {e.detail}"
                )
            raise privileged.http_error(e) from None
    log_action(user["username"], "delete_container_snapshot", name, "success", f"Snapshot '{snapshot}'")
    return {"ok": True}


@router.delete("/backups/{backup_id}")
def delete_container_backup(backup_id: int, confirm: bool = False, user: dict = Depends(require_role("admin"))):
    if not confirm:
        raise HTTPException(status_code=400, detail="Add ?confirm=true to confirm the deletion")
    row = backup_service.delete_container_backup(backup_id)
    if not row:
        raise HTTPException(status_code=404, detail="Backup not found")
    log_action(user["username"], "delete_container_backup", row["container_name"], "success")
    return {"message": "Backup deleted"}


class RestoreContainerRequest(BaseModel):
    new_name: str | None = None  # None = restore UNDER THE SAME NAME (the original container must already be deleted)


@router.post("/backups/{backup_id}/restore", status_code=201)
def restore_container_backup(
    backup_id: int, payload: RestoreContainerRequest, user: dict = Depends(require_role("admin"))
):
    maintenance.refuse_if_in_maintenance("local", "Restoring a backup")
    row = backup_service.get_container_backup(backup_id)
    if not row or row["status"] != "succeeded":
        raise HTTPException(status_code=404, detail="Backup not found or incomplete")
    target_name = payload.new_name or row["container_name"]
    name_error = validate_name(target_name, "container")
    if name_error:
        raise HTTPException(status_code=422, detail=name_error)

    # hyperlite-daemon unpacks the archive and defines the container (container.archive_restore).
    done = privileged.ask(
        "container.archive_restore",
        user,
        "restore_container_backup",
        target_name,
        failed="{}",
        path=row["path"] or "",
        container=target_name,
        original=row["container_name"],
    )
    app = get_container_app(row["container_name"])
    ssh_user = get_container_ssh_user(row["container_name"])
    if ssh_user:
        set_container_ssh_user(target_name, ssh_user)
    if app:
        set_container_app(target_name, app["image"], app["spec"], done["ip"], done["network"])
    log_action(user["username"], "restore_container_backup", target_name, "success", f"from backup #{backup_id}")
    conn = open_lxc_conn()
    try:
        return _summary(conn.lookupByName(target_name))
    finally:
        conn.close()


# ---- Output of an application container (app/core/container_console.py) ----


@router.get("/{name}/logs")
def container_logs(name: str, lines: int = 300, user: dict = Depends(require_container_privilege("container.console"))):
    """The last lines an application container's process printed: what `docker logs` shows."""
    if not get_container_app(name):
        raise HTTPException(status_code=409, detail="Only Docker (application) containers have a log: use the terminal")
    try:
        found = privileged.run("container.log", user=user["username"], container=name, lines=max(1, min(lines, 5000)))
    except DaemonError as e:
        raise privileged.http_error(e) from None
    tail = found["lines"]
    return {"name": name, "active": found["active"], "available": tail is not None, "lines": tail or []}


# ---- Web terminal (SSH, same mechanism as app/routers/vms.py) ----

TERMINAL_TICKETS = {}
TERMINAL_TICKET_TTL = 30
# ssh's own exit status when it could not connect or sign in: said in the terminal, after ssh's own words.
SSH_FAILED = 255


# Restricted to the admin role, as the VM one (app/routers/vms/console.py): the automation key signs in as the
# container's account, which has passwordless sudo, and its root has the host's capabilities (no user namespace,
# app/core/container_shell.py). Opening it is root on the host (GHSA-527j-fwg2-7646).
@router.post("/{name}/terminal-ticket")
def create_terminal_ticket(name: str, user: dict = Depends(require_role("admin"))):
    conn = open_lxc_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"Container '{name}' not found") from None

        if get_container_app(name):
            raise HTTPException(
                status_code=409,
                detail="An application container runs only its image's process, with no SSH server: reach its "
                "service on its address instead",
            )
        if not domain.isActive():
            raise HTTPException(status_code=409, detail="The container must be running to open a terminal")

        ip = _get_ip(domain)
        if not ip:
            raise HTTPException(status_code=409, detail="Container IP address not known yet (no DHCP lease yet?)")

        ssh_user = get_container_ssh_user(name)
        if not ssh_user:
            raise HTTPException(status_code=409, detail=f"No known SSH user for '{name}'")

        now = time.time()
        for old_ticket, entry in list(TERMINAL_TICKETS.items()):
            if entry[3] < now:
                TERMINAL_TICKETS.pop(old_ticket, None)

        ticket = secrets.token_urlsafe(24)
        TERMINAL_TICKETS[ticket] = (name, ip, ssh_user, now + TERMINAL_TICKET_TTL, user["username"])
        log_action(user["username"], "create_container_terminal_ticket", name, "success")
        return {"ticket": ticket, "user": ssh_user, "expires_in_s": TERMINAL_TICKET_TTL}
    finally:
        conn.close()


@router.websocket("/{name}/terminal")
async def container_terminal(websocket: WebSocket, name: str):
    ticket = websocket.query_params.get("ticket")
    entry = TERMINAL_TICKETS.pop(ticket, None) if ticket else None
    if entry is None:
        await websocket.close(code=4401)
        return

    ct_name, _ip, _ssh_user, expiry, username = entry
    if ct_name != name or time.time() > expiry:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    try:
        # hyperlite-daemon runs ssh with the automation key, which only root reads, to the address it reads itself.
        session = await pty_session.open_session("console.container_terminal", username, container=name)
    except DaemonError as e:
        await pty_session.say(websocket, e.detail)
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close(code=1011)
        return
    if await session.serve(websocket) == SSH_FAILED:
        await pty_session.say(websocket, f"SSH connection to {session.result['address']} failed")
    with contextlib.suppress(RuntimeError, WebSocketDisconnect):
        await websocket.close()


# ---- Root shell inside the container (app/core/container_shell.py), administrators only ----

SHELL_TICKETS = {}


@router.post("/{name}/shell-ticket")
def create_shell_ticket(name: str, user: dict = Depends(require_role("admin"))):
    conn = open_lxc_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"Container '{name}' not found") from None
        if not domain.isActive() or container_shell.init_pid(domain.ID()) is None:
            raise HTTPException(status_code=409, detail="The container must be running to open a shell")
    finally:
        conn.close()
    now = time.time()
    for old_ticket, entry in list(SHELL_TICKETS.items()):
        if entry[2] < now:
            SHELL_TICKETS.pop(old_ticket, None)
    ticket = secrets.token_urlsafe(24)
    SHELL_TICKETS[ticket] = (name, user["username"], now + TERMINAL_TICKET_TTL)
    log_action(user["username"], "create_container_shell_ticket", name, "success")
    return {"ticket": ticket, "expires_in_s": TERMINAL_TICKET_TTL}


@router.websocket("/{name}/shell")
async def container_shell_session(websocket: WebSocket, name: str):
    ticket = websocket.query_params.get("ticket")
    entry = SHELL_TICKETS.pop(ticket, None) if ticket else None
    if entry is None or entry[0] != name or time.time() > entry[2]:
        await websocket.close(code=4401)
        return
    username = entry[1]
    await websocket.accept()
    try:
        # hyperlite-daemon finds the container's PID 1 itself and enters its namespaces (console.container_shell).
        session = await pty_session.open_session("console.container_shell", username, container=name)
    except DaemonError as e:
        await pty_session.say(websocket, e.detail)
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close(code=1011)
        return
    log_action(username, "container_shell_open", name, "success")
    await session.serve(websocket)
    log_action(username, "container_shell_close", name, "success")
    try:
        await websocket.close()
    except (RuntimeError, WebSocketDisconnect):
        logger.debug("Ignored exception in container_shell_session()", exc_info=True)
