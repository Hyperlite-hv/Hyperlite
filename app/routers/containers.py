"""LXC containers: CRUD + lifecycle + web terminal + clone/backup. There is no
instantaneous snapshot: libvirt's LXC driver does not support it at all. There
is still no per-container firewall. Restricted to administrators like the other
resource creation endpoints (isos.py, templates.py), with the same granular
ACLs as VMs (app/core/permissions.py)."""

import asyncio
import json
import logging
import secrets
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import asyncssh
import libvirt
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.core import container_config, container_console, maintenance, object_meta
from app.core.audit import log_action
from app.core.container_builder import (
    app_spec,
    backup_container_rootfs,
    build_container_xml,
    clone_container_rootfs,
    configure_container_rootfs,
    container_rootfs_path,
    create_container_rootfs,
    delete_container_rootfs,
    prepare_app_rootfs,
    resolve_init,
    restore_container_rootfs,
    validate_app_overrides,
)
from app.core.container_meta import (
    delete_container_app,
    delete_container_ssh_user,
    get_container_app,
    get_container_ssh_user,
    get_container_storage,
    set_container_app,
    set_container_ssh_user,
)
from app.core.database import get_conn
from app.core.docker_hub import search_images
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import open_lxc_conn
from app.core.network_alloc import allocate_static_ip, generate_mac, network_gateway, release_static_ip
from app.core.security import get_current_user, require_container_privilege, require_role
from app.core.tasks import create_task, finish_task, update_task_progress
from app.core.vm_builder import (
    get_automation_private_key_path,
    get_or_create_automation_pubkey,
    validate_name,
    validate_username,
)
from app.core.vm_limits import compute_limits, validate_vm_resources

logger = logging.getLogger(__name__)

CONTAINER_BACKUP_DIR = Path("/root/hyperlite-container-backups")

router = APIRouter(prefix="/containers", tags=["containers"])

STATE_NAMES = {
    libvirt.VIR_DOMAIN_NOSTATE: "inconnu",
    libvirt.VIR_DOMAIN_RUNNING: "actif",
    libvirt.VIR_DOMAIN_BLOCKED: "bloque",
    libvirt.VIR_DOMAIN_PAUSED: "en_pause",
    libvirt.VIR_DOMAIN_SHUTDOWN: "en_arret",
    libvirt.VIR_DOMAIN_SHUTOFF: "arrete",
    libvirt.VIR_DOMAIN_CRASHED: "plante",
    libvirt.VIR_DOMAIN_PMSUSPENDED: "suspendu",
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
        "stockage": storage["pool"] if storage else None,
        "mode": "application" if app else "systeme",
        "image": app["image"] if app else None,
        "nom": domain.name(),
        "id": domain.ID() if active else None,
        "uuid": domain.UUIDString(),
        "etat": STATE_NAMES.get(state, "inconnu"),
        "vcpu": nvcpu,
        "memoire_mo": maxmem // 1024,
        # An application container has no DHCP lease: its address is the one libvirt set on its interface.
        "ip": (app["ip"] if app else _get_ip(domain)) if active else None,
    }


class ContainerCreate(BaseModel):
    name: str
    vcpu: int = Field(default=1, ge=1)
    memory_mb: int = Field(default=512, ge=128)
    # Required for a system container (the account created in it); unused by an application container.
    username: str | None = None
    password: str | None = None
    network: str = "default"
    # None/empty = the local Debian 12 base (fast, already cached); otherwise a
    # Docker Hub image reference (or any OCI registry), e.g. "ubuntu:22.04",
    # "alpine:3.19", "nginx:latest". See container_builder.pull_image_rootfs.
    image: str | None = None
    # "application": run the image's own process (the default with an image); "systeme": boot it as a small
    # system with systemd and sshd (the only choice without an image). See container_builder.read_image_config.
    mode: Literal["application", "systeme"] | None = None
    # A "dir" storage pool to hold the container's filesystem (None: the default /var/lib/libvirt/containers).
    storage_pool: str | None = None
    # Application containers only: replaces the image's Entrypoint+Cmd, and adds to or overrides its environment.
    command: list[str] | None = None
    env: dict[str, str] | None = None


@router.get("")
def list_containers(user: dict = Depends(get_current_user)):
    conn = open_lxc_conn()
    try:
        return [_summary(d) for d in conn.listAllDomains()]
    finally:
        conn.close()


@router.get("/docker-hub/search")
def search_docker_hub(q: str = "", user: dict = Depends(get_current_user)):
    try:
        return search_images(q)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


# IMPORTANT: must stay registered BEFORE @router.get("/{name}") below. FastAPI
# and Starlette resolve routes by registration order, not by specificity:
# otherwise GET /containers/backups is intercepted by GET /{name} and returns
# "Container 'backups' not found" instead of the list of backups.
@router.get("/backups")
def list_container_backups(user: dict = Depends(get_current_user)):
    with get_conn() as db:
        rows = db.execute("SELECT * FROM container_backups ORDER BY cree_le DESC").fetchall()
    return [dict(r) for r in rows]


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
    summary["utilisateur_ssh"] = get_container_ssh_user(name)
    summary["interfaces"] = container_config.interfaces(domain)
    summary["dns"] = container_config.read_dns(name)
    summary["demarrage_auto"] = bool(domain.autostart())
    return summary


class ContainerUpdate(BaseModel):
    vcpu: int | None = Field(default=None, ge=1)
    memory_mb: int | None = Field(default=None, ge=128)
    # Started with the host (libvirt's autostart: containers start in no particular order, and quickly).
    demarrage_auto: bool | None = None
    dns: list[str] | None = Field(default=None, max_length=8)


@router.patch("/{name}")
def update_container(name: str, payload: ContainerUpdate, user: dict = Depends(require_role("admin"))):
    if payload.vcpu is not None or payload.memory_mb is not None:
        errors = validate_vm_resources(payload.vcpu, payload.memory_mb)
        if errors:
            raise HTTPException(status_code=422, detail=errors)
    conn = open_lxc_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"Container '{name}' not found") from None
        changes, restart = [], False
        try:
            if payload.dns is not None:
                servers = container_config.write_dns(name, payload.dns)
                changes.append(f"DNS {', '.join(servers)}")
            if payload.vcpu is not None or payload.memory_mb is not None:
                restart = container_config.set_resources(conn, domain, payload.vcpu, payload.memory_mb)["a_redemarrer"]
                domain = conn.lookupByName(name)  # the definition was replaced
                changes.append(f"{payload.vcpu or '-'} vCPU, {payload.memory_mb or '-'} MB")
            if payload.demarrage_auto is not None:
                domain.setAutostart(1 if payload.demarrage_auto else 0)
                changes.append(f"start at boot {'on' if payload.demarrage_auto else 'off'}")
        except container_config.ConfigError as e:
            log_action(user["username"], "update_container", name, "echec", str(e))
            raise HTTPException(status_code=422, detail=str(e)) from e
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "update_container", name, "echec", msg)
            raise HTTPException(status_code=500, detail=msg) from e
        if not changes:
            raise HTTPException(status_code=422, detail="No change requested")
        log_action(user["username"], "update_container", name, "succes", "; ".join(changes))
        return {**_detail(domain), "a_redemarrer": restart}
    finally:
        conn.close()


def _app_domain_xml(conn, name, rootfs, spec, network, vcpu, memory_mb):
    """Reserve an address on the network, prepare the image's filesystem and build the domain of an application
    container. Returns (xml, mac, ip); the reservation is released again if anything after it fails."""
    gateway = network_gateway(conn, network)
    if gateway is None:
        raise ValueError(
            f"Network '{network}' has no Hyperlite-managed subnet: an application container needs a NAT or isolated "
            "network, whose address it receives from Hyperlite"
        )
    mac = generate_mac(conn)
    ip = allocate_static_ip(conn, network, mac)
    if not ip:
        raise ValueError(f"Network '{network}' has no free address left in its DHCP range")
    try:
        init = resolve_init(rootfs, spec)
        prepare_app_rootfs(rootfs, name, gateway[0])
        app = {"init": init, "spec": spec, "ip": ip, "prefix": gateway[1], "gateway": gateway[0]}
        if container_console.install(rootfs, int(spec.get("uid", 0)), int(spec.get("gid", 0))):
            app["launcher"] = (container_console.LAUNCHER, container_console.LOG)
        return build_container_xml(name, vcpu, memory_mb, rootfs, network=network, mac=mac, app=app), mac, ip
    except Exception:
        release_static_ip(conn, network, mac)
        raise


def resolve_container_storage(pool_name):
    """{"pool", "base_dir"} for a local directory storage pool, or raise ValueError. NFS, ZFS and iSCSI pools are
    refused: a container filesystem needs root ownership, device files and extended attributes, which an NFS
    export (root squash) does not reliably keep, and ZFS/iSCSI pools hold block volumes, not directories."""
    from app.core.libvirt_utils import open_conn

    conn = open_conn()
    try:
        try:
            pool = conn.storagePoolLookupByName(pool_name)
        except libvirt.libvirtError:
            raise ValueError(f"Storage pool '{pool_name}' not found") from None
        root = ET.fromstring(pool.XMLDesc(0))
        if root.get("type") != "dir":
            raise ValueError(f"Storage pool '{pool_name}' is not a local directory pool: containers need one")
        if not pool.isActive():
            raise ValueError(f"Storage pool '{pool_name}' is not started")
        path = root.findtext("target/path")
        if not path or not path.startswith("/"):
            raise ValueError(f"Storage pool '{pool_name}' has no usable path")
        return {"pool": pool_name, "base_dir": str(Path(path) / "hyperlite-containers")}
    finally:
        conn.close()


def _domain_mac(domain):
    mac = ET.fromstring(domain.XMLDesc(0)).find(".//devices/interface/mac")
    return mac.get("address") if mac is not None else None


@router.post("", status_code=201)
def create_container(payload: ContainerCreate, user: dict = Depends(require_role("admin"))):
    maintenance.refuse_if_in_maintenance("local", "Container creation")
    _limits = compute_limits()
    _errs = []

    def _origin(lim):
        return f"variable {lim['variable']}" if lim["source"] == "configuration" else "technical ceiling"

    if payload.vcpu > _limits["vcpu"]["max"]:
        _errs.append(f"vCPU: {payload.vcpu} is above the limit ({_limits['vcpu']['max']}, {_origin(_limits['vcpu'])})")
    if payload.memory_mb > _limits["memoire_mo"]["max"]:
        _errs.append(
            f"Memory: {payload.memory_mb} MB is above the limit "
            f"({_limits['memoire_mo']['max']} MB, {_origin(_limits['memoire_mo'])})"
        )
    if _errs:
        raise HTTPException(status_code=422, detail=" ; ".join(_errs))
    errors = []
    mode = payload.mode or ("application" if payload.image else "systeme")
    name_error = validate_name(payload.name, "container")
    if name_error:
        errors.append(name_error)
    if mode == "application":
        if not payload.image:
            errors.append("An application container needs an image")
        errors.extend(validate_app_overrides(payload.command, payload.env))
    else:
        if payload.command or payload.env:
            errors.append("A command or environment variables apply to application containers only")
        username_error = validate_username(payload.username or "")
        if username_error:
            errors.append(username_error)
        if len(payload.password or "") < 4:
            errors.append("The password must contain at least 4 characters")

    conn = open_lxc_conn()
    task_id = create_task("create_container", payload.name, node=conn.getHostname(), username=user["username"])
    try:
        try:
            conn.lookupByName(payload.name)
            errors.append(f"A container named '{payload.name}' already exists")
        except libvirt.libvirtError:
            logger.debug("Ignored exception in create_container()", exc_info=True)

        storage = None
        if payload.storage_pool:
            try:
                storage = resolve_container_storage(payload.storage_pool)
            except ValueError as e:
                errors.append(str(e))
        # Checked before the image is fetched: a download of hundreds of MB must not end in this refusal.
        try:
            if not conn.networkLookupByName(payload.network).isActive():
                errors.append(f"Network '{payload.network}' is not started")
        except libvirt.libvirtError:
            logger.debug("Network lookup failed in create_container()", exc_info=True)
        if mode == "application":
            try:
                if network_gateway(conn, payload.network) is None:
                    errors.append(
                        f"Network '{payload.network}' has no Hyperlite-managed subnet: an application container "
                        "needs a NAT or isolated network"
                    )
            except libvirt.libvirtError:
                errors.append(f"Network '{payload.network}' not found")

        if errors:
            log_action(user["username"], "create_container", payload.name, "echec", "; ".join(errors), task_id=task_id)
            raise HTTPException(status_code=422, detail=errors)

        if mode == "application":
            return _create_app_container(conn, payload, user, task_id, storage)

        try:
            rootfs, family = create_container_rootfs(payload.name, image=payload.image, storage=storage)
            ssh_pubkey = get_or_create_automation_pubkey()
            configure_container_rootfs(
                rootfs, payload.name, payload.username, payload.password, ssh_pubkey, family=family
            )
        except subprocess.CalledProcessError as e:
            msg = e.stderr or str(e)
            delete_container_rootfs(payload.name)
            log_action(user["username"], "create_container", payload.name, "echec", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Failed to prepare the filesystem: {msg}") from e
        except ValueError as e:
            log_action(user["username"], "create_container", payload.name, "echec", str(e), task_id=task_id)
            raise HTTPException(status_code=422, detail=str(e)) from e

        mac = generate_mac(conn)
        xml = build_container_xml(
            payload.name, payload.vcpu, payload.memory_mb, rootfs, network=payload.network, mac=mac
        )
        try:
            domain = conn.defineXML(xml)
            domain.create()
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            delete_container_rootfs(payload.name)
            log_action(user["username"], "create_container", payload.name, "echec", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Failed to start the container: {msg}") from e

        set_container_ssh_user(payload.name, payload.username)
        log_action(user["username"], "create_container", payload.name, "succes", task_id=task_id)
        return _summary(domain)
    finally:
        conn.close()


def _create_app_container(conn, payload, user, task_id, storage=None):
    try:
        rootfs, _family = create_container_rootfs(payload.name, image=payload.image, bootstrap=False, storage=storage)
    except subprocess.CalledProcessError as e:
        msg = e.stderr or str(e)
        delete_container_rootfs(payload.name)
        log_action(user["username"], "create_container", payload.name, "echec", msg, task_id=task_id)
        raise HTTPException(status_code=500, detail=f"Failed to fetch the image: {msg}") from e
    except ValueError as e:
        log_action(user["username"], "create_container", payload.name, "echec", str(e), task_id=task_id)
        raise HTTPException(status_code=422, detail=str(e)) from e

    mac = None
    try:
        spec = app_spec(payload.image, payload.command, payload.env)
        xml, mac, ip = _app_domain_xml(
            conn, payload.name, rootfs, spec, payload.network, payload.vcpu, payload.memory_mb
        )
        domain = conn.defineXML(xml)
    except (ValueError, libvirt.libvirtError) as e:
        msg = describe_exception(e) if isinstance(e, libvirt.libvirtError) else str(e)
        if mac:
            release_static_ip(conn, payload.network, mac)
        delete_container_rootfs(payload.name)
        log_action(user["username"], "create_container", payload.name, "echec", msg, task_id=task_id)
        raise HTTPException(status_code=422 if isinstance(e, ValueError) else 500, detail=msg) from e

    set_container_app(payload.name, payload.image, spec, ip, payload.network)
    try:
        domain.create()
    except libvirt.libvirtError as e:
        # Defined but not started (a wrong command, for instance): kept, so it can be fixed or deleted.
        msg = describe_exception(e)
        log_action(
            user["username"],
            "create_container",
            payload.name,
            "echec",
            f"defined, start failed: {msg}",
            task_id=task_id,
        )
        raise HTTPException(status_code=500, detail=f"Container defined but its process did not start: {msg}") from e
    log_action(
        user["username"], "create_container", payload.name, "succes", f"application {payload.image}", task_id=task_id
    )
    return _summary(domain)


@router.post("/{name}/start")
def start_container(name: str, user: dict = Depends(require_container_privilege("container.power"))):
    conn = open_lxc_conn()
    try:
        try:
            domain = conn.lookupByName(name)
            domain.create()
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "start_container", name, "echec", msg)
            raise HTTPException(status_code=500, detail=msg) from e
        log_action(user["username"], "start_container", name, "succes")
        return _summary(domain)
    finally:
        conn.close()


@router.post("/{name}/stop")
def stop_container(
    name: str, force: bool = False, user: dict = Depends(require_container_privilege("container.power"))
):
    conn = open_lxc_conn()
    try:
        try:
            domain = conn.lookupByName(name)
            if force:
                domain.destroy()
            else:
                domain.shutdown()
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "stop_container", name, "echec", msg)
            raise HTTPException(status_code=500, detail=msg) from e
        log_action(user["username"], "stop_container", name, "succes")
        return {"ok": True}
    finally:
        conn.close()


@router.delete("/{name}")
def delete_container(name: str, user: dict = Depends(require_role("admin"))):
    conn = open_lxc_conn()
    try:
        try:
            domain = conn.lookupByName(name)
            mac, network = _domain_mac(domain), _domain_network(domain)
            try:
                domain.destroy()
            except libvirt.libvirtError:
                logger.debug("Ignored exception in delete_container()", exc_info=True)
            domain.undefine()
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "delete_container", name, "echec", msg)
            raise HTTPException(status_code=404, detail=msg) from e

        delete_container_rootfs(name)
        delete_container_ssh_user(name)
        object_meta.delete("container", name)
        if get_container_app(name):
            if mac:
                release_static_ip(conn, network, mac)
            delete_container_app(name)
        log_action(user["username"], "delete_container", name, "succes")
        return {"ok": True}
    finally:
        conn.close()


# ---- Clone + backup/restore ----
# There is no instantaneous snapshot: libvirt's LXC driver does not support
# virDomainSnapshotCreateXML (see app/core/container_builder.py for the detail).
# Clone = a full copy of the rootfs; backup = a tar archive (without scheduling or
# retention for now).


class CloneContainerRequest(BaseModel):
    new_name: str


@router.post("/{name}/clone", status_code=201)
def clone_container(name: str, payload: CloneContainerRequest, user: dict = Depends(require_role("admin"))):
    maintenance.refuse_if_in_maintenance("local", "Cloning")
    conn = open_lxc_conn()
    task_id = create_task("clone_container", name, node=conn.getHostname(), username=user["username"])
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(
                user["username"], "clone_container", name, "echec", "source container not found", task_id=task_id
            )
            raise HTTPException(status_code=404, detail=f"Container '{name}' not found") from None

        name_error = validate_name(payload.new_name, "container")
        if name_error:
            log_action(user["username"], "clone_container", name, "echec", name_error, task_id=task_id)
            raise HTTPException(status_code=422, detail=name_error)

        try:
            conn.lookupByName(payload.new_name)
            log_action(
                user["username"],
                "clone_container",
                name,
                "echec",
                f"'{payload.new_name}' already exists",
                task_id=task_id,
            )
            raise HTTPException(status_code=409, detail=f"A container '{payload.new_name}' already exists")
        except libvirt.libvirtError:
            logger.debug("Ignored exception in clone_container()", exc_info=True)

        if domain.isActive():
            log_action(user["username"], "clone_container", name, "echec", "conteneur actif", task_id=task_id)
            raise HTTPException(status_code=409, detail="Stop the container before cloning it")

        try:
            rootfs = clone_container_rootfs(name, payload.new_name)
        except (subprocess.CalledProcessError, ValueError) as e:
            msg = e.stderr if isinstance(e, subprocess.CalledProcessError) and e.stderr else str(e)
            log_action(user["username"], "clone_container", name, "echec", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Failed to copy the filesystem: {msg}") from e

        source_root = ET.fromstring(domain.XMLDesc(0))
        vcpu = int(source_root.findtext("vcpu") or "1")
        memory_kb = int(source_root.findtext("memory") or str(512 * 1024))
        network = _domain_network(domain)
        app = get_container_app(name)
        mac = ip = None
        try:
            if app:
                xml, mac, ip = _app_domain_xml(
                    conn, payload.new_name, rootfs, app["spec"], network, vcpu, memory_kb // 1024
                )
            else:
                mac = generate_mac(conn)
                xml = build_container_xml(payload.new_name, vcpu, memory_kb // 1024, rootfs, network=network, mac=mac)
            new_domain = conn.defineXML(xml)
        except (ValueError, libvirt.libvirtError) as e:
            msg = describe_exception(e) if isinstance(e, libvirt.libvirtError) else str(e)
            if app and ip:
                release_static_ip(conn, network, mac)
            delete_container_rootfs(payload.new_name)
            log_action(user["username"], "clone_container", name, "echec", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Failed to define the cloned container: {msg}") from e

        ssh_user = get_container_ssh_user(name)
        if ssh_user:
            set_container_ssh_user(payload.new_name, ssh_user)
        if app:
            set_container_app(payload.new_name, app["image"], app["spec"], ip, network)
        log_action(user["username"], "clone_container", name, "succes", f"-> {payload.new_name}", task_id=task_id)
        return _summary(new_domain)
    finally:
        conn.close()


def _domain_network(domain):
    root = ET.fromstring(domain.XMLDesc(0))
    iface = root.find(".//devices/interface[@type='network']/source")
    return iface.get("network") if iface is not None else "default"


class ContainerBackupRunning(Exception):
    pass


_container_backup_lock = threading.Lock()  # same reasoning as the VM _backup_lock: one at a time


def _run_container_backup_job(task_id, username, container_name):
    now = datetime.now(UTC)
    CONTAINER_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{container_name}-{now.strftime('%Y%m%dT%H%M%SZ')}.tar.gz"
    dest_path = CONTAINER_BACKUP_DIR / filename
    with get_conn() as db:
        cur = db.execute(
            "INSERT INTO container_backups (container_name, chemin, cree_le, statut, task_id) VALUES (?, ?, ?, 'en_cours', ?)",
            (container_name, str(dest_path), now.isoformat(), task_id),
        )
        db.commit()
        backup_id = cur.lastrowid
    try:
        with _container_backup_lock:
            update_task_progress(task_id, 10)
            backup_container_rootfs(container_name, dest_path)
            update_task_progress(task_id, 90)
        size = dest_path.stat().st_size
        with get_conn() as db:
            db.execute(
                "UPDATE container_backups SET statut = 'termine', taille_octets = ? WHERE id = ?",
                (size, backup_id),
            )
            db.commit()
        finish_task(task_id, "termine")
        log_action(username, "backup_container", container_name, "succes")
    except Exception as e:
        dest_path.unlink(missing_ok=True)
        with get_conn() as db:
            db.execute(
                "UPDATE container_backups SET statut = 'echec', erreur = ? WHERE id = ?", (str(e)[:500], backup_id)
            )
            db.commit()
        finish_task(task_id, "echec", str(e)[:500])
        log_action(username, "backup_container", container_name, "echec", str(e)[:500])


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
    task_id = create_task("backup_container", name, username=user["username"])
    threading.Thread(target=_run_container_backup_job, args=(task_id, user["username"], name), daemon=True).start()
    return {"task_id": task_id}


@router.delete("/backups/{backup_id}")
def delete_container_backup(backup_id: int, confirm: bool = False, user: dict = Depends(require_role("admin"))):
    if not confirm:
        raise HTTPException(status_code=400, detail="Add ?confirm=true to confirm the deletion")
    with get_conn() as db:
        row = db.execute("SELECT * FROM container_backups WHERE id = ?", (backup_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Backup not found")
        Path(row["chemin"]).unlink(missing_ok=True)
        db.execute("DELETE FROM container_backups WHERE id = ?", (backup_id,))
        db.commit()
    log_action(user["username"], "delete_container_backup", row["container_name"], "succes")
    return {"message": "Backup deleted"}


class RestoreContainerRequest(BaseModel):
    new_name: str | None = None  # None = restore UNDER THE SAME NAME (the original container must already be deleted)


@router.post("/backups/{backup_id}/restore", status_code=201)
def restore_container_backup(
    backup_id: int, payload: RestoreContainerRequest, user: dict = Depends(require_role("admin"))
):
    maintenance.refuse_if_in_maintenance("local", "Restoring a backup")
    with get_conn() as db:
        row = db.execute("SELECT * FROM container_backups WHERE id = ?", (backup_id,)).fetchone()
    if not row or row["statut"] != "termine":
        raise HTTPException(status_code=404, detail="Backup not found or incomplete")
    target_name = payload.new_name or row["container_name"]
    name_error = validate_name(target_name, "container")
    if name_error:
        raise HTTPException(status_code=422, detail=name_error)

    conn = open_lxc_conn()
    try:
        try:
            conn.lookupByName(target_name)
            raise HTTPException(
                status_code=409,
                detail=f"A container '{target_name}' already exists: delete it first or choose another name",
            )
        except libvirt.libvirtError:
            logger.debug("Ignored exception in restore_container_backup()", exc_info=True)

        try:
            rootfs = restore_container_rootfs(Path(row["chemin"]), target_name, original_name=row["container_name"])
        except (subprocess.CalledProcessError, ValueError) as e:
            msg = e.stderr if isinstance(e, subprocess.CalledProcessError) and e.stderr else str(e)
            raise HTTPException(status_code=500, detail=f"Restore failed: {msg}") from e

        # KNOWN LIMITATION: the original vcpu/memory are not kept in the archive (unlike
        # cloning, which reads them straight from the still defined source domain), so
        # they fall back to the default values, adjustable afterwards like for any
        # container.
        app = get_container_app(row["container_name"])
        mac = ip = None
        try:
            if app:
                # The original network, not "default": that one may not even be started on this host.
                network = app["network"] or "default"
                xml, mac, ip = _app_domain_xml(conn, target_name, rootfs, app["spec"], network, 1, 512)
            else:
                mac = generate_mac(conn)
                xml = build_container_xml(target_name, 1, 512, rootfs, network="default", mac=mac)
            domain = conn.defineXML(xml)
        except (ValueError, libvirt.libvirtError) as e:
            if app and ip:
                release_static_ip(conn, network, mac)
            delete_container_rootfs(target_name)
            detail = describe_exception(e) if isinstance(e, libvirt.libvirtError) else str(e)
            raise HTTPException(status_code=500, detail=f"Failed to define the restored container: {detail}") from e

        ssh_user = get_container_ssh_user(row["container_name"])
        if ssh_user:
            set_container_ssh_user(target_name, ssh_user)
        if app:
            set_container_app(target_name, app["image"], app["spec"], ip, network)
        log_action(user["username"], "restore_container_backup", target_name, "succes", f"from backup #{backup_id}")
        return _summary(domain)
    finally:
        conn.close()


# ---- Output of an application container (app/core/container_console.py) ----


@router.get("/{name}/logs")
def container_logs(name: str, lines: int = 300, user: dict = Depends(require_container_privilege("container.console"))):
    """The last lines an application container's process printed: what `docker logs` shows."""
    if not get_container_app(name):
        raise HTTPException(status_code=409, detail="Only Docker (application) containers have a log: use the terminal")
    conn = open_lxc_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"Container '{name}' not found") from None
        active = bool(domain.isActive())
    finally:
        conn.close()
    tail = container_console.read_tail(container_rootfs_path(name), max(1, min(lines, 5000)))
    return {"nom": name, "actif": active, "disponible": tail is not None, "lignes": tail or []}


# ---- Web terminal (SSH, same mechanism as app/routers/vms.py) ----

TERMINAL_TICKETS = {}
TERMINAL_TICKET_TTL = 30


@router.post("/{name}/terminal-ticket")
def create_terminal_ticket(name: str, user: dict = Depends(require_container_privilege("container.console"))):
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
        for old_ticket, (_old_ct, _old_ip, _old_user, old_expiry) in list(TERMINAL_TICKETS.items()):
            if old_expiry < now:
                TERMINAL_TICKETS.pop(old_ticket, None)

        ticket = secrets.token_urlsafe(24)
        TERMINAL_TICKETS[ticket] = (name, ip, ssh_user, now + TERMINAL_TICKET_TTL)
        log_action(user["username"], "create_container_terminal_ticket", name, "succes")
        return {"ticket": ticket, "utilisateur": ssh_user, "expire_dans_s": TERMINAL_TICKET_TTL}
    finally:
        conn.close()


@router.websocket("/{name}/terminal")
async def container_terminal(websocket: WebSocket, name: str):
    ticket = websocket.query_params.get("ticket")
    entry = TERMINAL_TICKETS.pop(ticket, None) if ticket else None
    if entry is None:
        await websocket.close(code=4401)
        return

    ct_name, ip, ssh_user, expiry = entry
    if ct_name != name or time.time() > expiry:
        await websocket.close(code=4401)
        return

    await websocket.accept()

    private_key = get_automation_private_key_path()
    try:
        ssh_conn = await asyncssh.connect(
            ip,
            username=ssh_user,
            client_keys=[str(private_key)],
            known_hosts=None,
            connect_timeout=10,
        )
    except (asyncssh.Error, OSError) as e:
        await websocket.send_text(f"\r\n\x1b[31m[hyperlite] SSH connection to {ip} failed: {e}\x1b[0m\r\n")
        await websocket.close(code=1011)
        return

    try:
        process = await ssh_conn.create_process(term_type="xterm-256color", term_size=(80, 24))
    except asyncssh.Error as e:
        await websocket.send_text(f"\r\n\x1b[31m[hyperlite] Failed to open the shell: {e}\x1b[0m\r\n")
        ssh_conn.close()
        await websocket.close(code=1011)
        return

    async def ws_to_ssh():
        try:
            while True:
                msg = await websocket.receive_text()
                if msg.startswith("\x00"):
                    try:
                        dims = json.loads(msg[1:])
                        process.change_terminal_size(int(dims["cols"]), int(dims["rows"]))
                    except (ValueError, KeyError, TypeError):
                        logger.debug("Ignored exception in ws_to_ssh()", exc_info=True)
                else:
                    process.stdin.write(msg)
        except (WebSocketDisconnect, RuntimeError):
            logger.debug("Ignored exception in ws_to_ssh()", exc_info=True)
        except Exception:
            logger.debug("Ignored exception in ws_to_ssh()", exc_info=True)
        finally:
            try:
                process.stdin.write_eof()
            except Exception:
                logger.debug("Ignored exception in ws_to_ssh()", exc_info=True)

    async def ssh_to_ws():
        try:
            while True:
                data = await process.stdout.read(65536)
                if not data:
                    break
                await websocket.send_text(data)
        except Exception:
            logger.debug("Ignored exception in ssh_to_ws()", exc_info=True)

    task1 = asyncio.ensure_future(ws_to_ssh())
    task2 = asyncio.ensure_future(ssh_to_ws())
    _done, pending = await asyncio.wait({task1, task2}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    try:
        process.close()
    except Exception:
        logger.debug("Ignored exception in container_terminal()", exc_info=True)
    ssh_conn.close()
    try:
        await websocket.close()
    except (RuntimeError, WebSocketDisconnect):
        logger.debug("Ignored exception in container_terminal()", exc_info=True)
