"""Conteneurs LXC (chantier 18) : CRUD + cycle de vie + terminal web, en
version volontairement plus reduite que app/routers/vms.py pour cette
premiere livraison (pas de snapshots/clonage/pare-feu par conteneur pour
l'instant -- a ajouter dans une iteration suivante si besoin). Reserve aux
administrateurs comme les autres endpoints de creation de ressources
(isos.py, templates.py) : pas encore d'ACL granulaire par conteneur comme
pour les VM (app/core/permissions.py), qui reste a etendre plus tard."""
import asyncio
import json
import secrets
import subprocess
import time

import asyncssh
import libvirt
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.core.libvirt_utils import open_lxc_conn
from app.core.security import get_current_user, require_role
from app.core.audit import log_action
from app.core.tasks import create_task
from app.core.error_messages import describe_exception
from app.core.vm_builder import (
    validate_name, validate_username, get_or_create_automation_pubkey, get_automation_private_key_path,
)
from app.core.container_builder import (
    create_container_rootfs, configure_container_rootfs, delete_container_rootfs,
    build_container_xml,
)
from app.core.container_meta import set_container_ssh_user, get_container_ssh_user, delete_container_ssh_user
from app.core.network_alloc import generate_mac

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
        pass
    return None


def _summary(domain):
    active = domain.isActive()
    state, maxmem, mem, nvcpu, cputime = domain.info()
    return {
        "nom": domain.name(),
        "id": domain.ID() if active else None,
        "uuid": domain.UUIDString(),
        "etat": STATE_NAMES.get(state, "inconnu"),
        "vcpu": nvcpu,
        "memoire_mo": maxmem // 1024,
        "ip": _get_ip(domain) if active else None,
    }


class ContainerCreate(BaseModel):
    name: str
    vcpu: int = Field(default=1, ge=1, le=16)
    memory_mb: int = Field(default=512, ge=128, le=32768)
    username: str
    password: str
    network: str = "default"


@router.get("")
def list_containers(user: dict = Depends(get_current_user)):
    conn = open_lxc_conn()
    try:
        return [_summary(d) for d in conn.listAllDomains()]
    finally:
        conn.close()


@router.get("/{name}")
def get_container(name: str, user: dict = Depends(get_current_user)):
    conn = open_lxc_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"Conteneur '{name}' introuvable")
        summary = _summary(domain)
        summary["utilisateur_ssh"] = get_container_ssh_user(name)
        return summary
    finally:
        conn.close()


@router.post("", status_code=201)
def create_container(payload: ContainerCreate, user: dict = Depends(require_role("admin"))):
    errors = []
    name_error = validate_name(payload.name)
    if name_error:
        errors.append(name_error)
    username_error = validate_username(payload.username)
    if username_error:
        errors.append(username_error)
    if len(payload.password or "") < 4:
        errors.append("Le mot de passe doit contenir au moins 4 caractères")

    conn = open_lxc_conn()
    task_id = create_task("create_container", payload.name, node=conn.getHostname(), username=user["username"])
    try:
        try:
            conn.lookupByName(payload.name)
            errors.append(f"Un conteneur nommé '{payload.name}' existe déjà")
        except libvirt.libvirtError:
            pass

        if errors:
            log_action(user["username"], "create_container", payload.name, "echec", "; ".join(errors), task_id=task_id)
            raise HTTPException(status_code=422, detail=errors)

        try:
            rootfs = create_container_rootfs(payload.name)
            ssh_pubkey = get_or_create_automation_pubkey()
            configure_container_rootfs(rootfs, payload.name, payload.username, payload.password, ssh_pubkey)
        except subprocess.CalledProcessError as e:
            msg = e.stderr or str(e)
            delete_container_rootfs(payload.name)
            log_action(user["username"], "create_container", payload.name, "echec", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Échec de préparation du système de fichiers : {msg}")
        except ValueError as e:
            log_action(user["username"], "create_container", payload.name, "echec", str(e), task_id=task_id)
            raise HTTPException(status_code=422, detail=str(e))

        mac = generate_mac(conn)
        xml = build_container_xml(payload.name, payload.vcpu, payload.memory_mb, rootfs, network=payload.network, mac=mac)
        try:
            domain = conn.defineXML(xml)
            domain.create()
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            delete_container_rootfs(payload.name)
            log_action(user["username"], "create_container", payload.name, "echec", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Échec de démarrage du conteneur : {msg}")

        set_container_ssh_user(payload.name, payload.username)
        log_action(user["username"], "create_container", payload.name, "succes", task_id=task_id)
        return _summary(domain)
    finally:
        conn.close()


@router.post("/{name}/start")
def start_container(name: str, user: dict = Depends(require_role("admin"))):
    conn = open_lxc_conn()
    try:
        try:
            domain = conn.lookupByName(name)
            domain.create()
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "start_container", name, "echec", msg)
            raise HTTPException(status_code=500, detail=msg)
        log_action(user["username"], "start_container", name, "succes")
        return _summary(domain)
    finally:
        conn.close()


@router.post("/{name}/stop")
def stop_container(name: str, force: bool = False, user: dict = Depends(require_role("admin"))):
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
            raise HTTPException(status_code=500, detail=msg)
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
            try:
                domain.destroy()
            except libvirt.libvirtError:
                pass
            domain.undefine()
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "delete_container", name, "echec", msg)
            raise HTTPException(status_code=404, detail=msg)

        delete_container_rootfs(name)
        delete_container_ssh_user(name)
        log_action(user["username"], "delete_container", name, "succes")
        return {"ok": True}
    finally:
        conn.close()


# ---- Terminal web (SSH, meme mecanisme que app/routers/vms.py) ----

TERMINAL_TICKETS = {}
TERMINAL_TICKET_TTL = 30


@router.post("/{name}/terminal-ticket")
def create_terminal_ticket(name: str, user: dict = Depends(require_role("admin"))):
    conn = open_lxc_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"Conteneur '{name}' introuvable")

        if not domain.isActive():
            raise HTTPException(status_code=409, detail="Le conteneur doit être démarré pour ouvrir un terminal")

        ip = _get_ip(domain)
        if not ip:
            raise HTTPException(status_code=409, detail="Adresse IP du conteneur inconnue pour le moment (pas encore de bail DHCP ?)")

        ssh_user = get_container_ssh_user(name)
        if not ssh_user:
            raise HTTPException(status_code=409, detail=f"Aucun utilisateur SSH connu pour '{name}'")

        now = time.time()
        for old_ticket, (old_ct, old_ip, old_user, old_expiry) in list(TERMINAL_TICKETS.items()):
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
            ip, username=ssh_user, client_keys=[str(private_key)],
            known_hosts=None, connect_timeout=10,
        )
    except (asyncssh.Error, OSError) as e:
        await websocket.send_text(f"\r\n\x1b[31m[hyperlite] Échec de connexion SSH à {ip} : {e}\x1b[0m\r\n")
        await websocket.close(code=1011)
        return

    try:
        process = await ssh_conn.create_process(term_type="xterm-256color", term_size=(80, 24))
    except asyncssh.Error as e:
        await websocket.send_text(f"\r\n\x1b[31m[hyperlite] Échec d'ouverture du shell : {e}\x1b[0m\r\n")
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
                        pass
                else:
                    process.stdin.write(msg)
        except (WebSocketDisconnect, RuntimeError):
            pass
        except Exception:
            pass
        finally:
            try:
                process.stdin.write_eof()
            except Exception:
                pass

    async def ssh_to_ws():
        try:
            while True:
                data = await process.stdout.read(65536)
                if not data:
                    break
                await websocket.send_text(data)
        except Exception:
            pass

    task1 = asyncio.ensure_future(ws_to_ssh())
    task2 = asyncio.ensure_future(ssh_to_ws())
    done, pending = await asyncio.wait({task1, task2}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    try:
        process.close()
    except Exception:
        pass
    ssh_conn.close()
    try:
        await websocket.close()
    except (RuntimeError, WebSocketDisconnect):
        pass
