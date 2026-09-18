"""Conteneurs LXC (chantier 18) : CRUD + cycle de vie + terminal web +
clonage/sauvegarde (backlog 2026-09-18, voir plus bas -- pas de snapshot
instantane possible, le pilote LXC de libvirt ne le supporte pas du tout).
Toujours pas de pare-feu par conteneur. Reserve aux administrateurs comme
les autres endpoints de creation de ressources (isos.py, templates.py) :
pas encore d'ACL granulaire par conteneur comme pour les VM
(app/core/permissions.py), qui reste a etendre plus tard."""
import asyncio
import json
import secrets
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import asyncssh
import libvirt
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.core.database import get_conn
from app.core.libvirt_utils import open_lxc_conn
from app.core.security import get_current_user, require_role
from app.core.audit import log_action
from app.core.tasks import create_task, finish_task, update_task_progress
from app.core.error_messages import describe_exception
from app.core.vm_builder import (
    validate_name, validate_username, get_or_create_automation_pubkey, get_automation_private_key_path,
)
from app.core.container_builder import (
    create_container_rootfs, configure_container_rootfs, delete_container_rootfs,
    build_container_xml, clone_container_rootfs, backup_container_rootfs, restore_container_rootfs,
)
from app.core.container_meta import set_container_ssh_user, get_container_ssh_user, delete_container_ssh_user
from app.core.network_alloc import generate_mac
from app.core.docker_hub import search_images

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
    # None/vide = base locale Debian 12 (rapide, deja en cache) ; sinon
    # reference d'image Docker Hub (ou tout registre OCI), ex. "ubuntu:22.04",
    # "alpine:3.19", "nginx:latest" -- voir container_builder.pull_image_rootfs.
    image: str | None = None


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
        raise HTTPException(status_code=502, detail=str(e))


# IMPORTANT : doit rester enregistree AVANT @router.get("/{name}") ci-dessous
# -- BUG REEL trouve en testant (routage FastAPI/Starlette resout par ordre
# d'enregistrement, pas par specificite) : GET /containers/backups etait
# intercepte par GET /{name} (deja enregistre plus haut a l'origine),
# renvoyant "Conteneur 'backups' introuvable" au lieu de la liste des
# sauvegardes.
@router.get("/backups")
def list_container_backups(user: dict = Depends(get_current_user)):
    with get_conn() as db:
        rows = db.execute("SELECT * FROM container_backups ORDER BY cree_le DESC").fetchall()
    return [dict(r) for r in rows]


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
            rootfs, family = create_container_rootfs(payload.name, image=payload.image)
            ssh_pubkey = get_or_create_automation_pubkey()
            configure_container_rootfs(rootfs, payload.name, payload.username, payload.password, ssh_pubkey, family=family)
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


# ---- Clonage + sauvegarde/restauration (backlog 2026-09-18) ----
# Pas de snapshot instantane possible : confirme en testant que le pilote
# LXC de libvirt ne supporte pas virDomainSnapshotCreateXML (voir
# app/core/container_builder.py pour le detail). Clonage = copie complete
# du rootfs (equivalent chantier 5) ; sauvegarde = archive tar (equivalent
# chantier 13, sans planification/retention pour cette premiere passe).

class CloneContainerRequest(BaseModel):
    new_name: str


@router.post("/{name}/clone", status_code=201)
def clone_container(name: str, payload: CloneContainerRequest, user: dict = Depends(require_role("admin"))):
    conn = open_lxc_conn()
    task_id = create_task("clone_container", name, node=conn.getHostname(), username=user["username"])
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "clone_container", name, "echec", "conteneur source introuvable", task_id=task_id)
            raise HTTPException(status_code=404, detail=f"Conteneur '{name}' introuvable")

        name_error = validate_name(payload.new_name)
        if name_error:
            log_action(user["username"], "clone_container", name, "echec", name_error, task_id=task_id)
            raise HTTPException(status_code=422, detail=name_error)

        try:
            conn.lookupByName(payload.new_name)
            log_action(user["username"], "clone_container", name, "echec", f"'{payload.new_name}' existe déjà", task_id=task_id)
            raise HTTPException(status_code=409, detail=f"Un conteneur '{payload.new_name}' existe déjà")
        except libvirt.libvirtError:
            pass

        if domain.isActive():
            log_action(user["username"], "clone_container", name, "echec", "conteneur actif", task_id=task_id)
            raise HTTPException(status_code=409, detail="Arrêtez le conteneur avant de le cloner")

        try:
            rootfs = clone_container_rootfs(name, payload.new_name)
        except (subprocess.CalledProcessError, ValueError) as e:
            msg = e.stderr if isinstance(e, subprocess.CalledProcessError) and e.stderr else str(e)
            log_action(user["username"], "clone_container", name, "echec", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Échec de la copie du système de fichiers : {msg}")

        source_root = ET.fromstring(domain.XMLDesc(0))
        vcpu = int(source_root.findtext("vcpu") or "1")
        memory_kb = int(source_root.findtext("memory") or str(512 * 1024))
        mac = generate_mac(conn)
        xml = build_container_xml(payload.new_name, vcpu, memory_kb // 1024, rootfs, network=_domain_network(domain), mac=mac)
        try:
            new_domain = conn.defineXML(xml)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            delete_container_rootfs(payload.new_name)
            log_action(user["username"], "clone_container", name, "echec", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Échec de définition du conteneur cloné : {msg}")

        ssh_user = get_container_ssh_user(name)
        if ssh_user:
            set_container_ssh_user(payload.new_name, ssh_user)
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


_container_backup_lock = threading.Lock()  # meme raisonnement que _backup_lock des VM (chantier 29) : un seul a la fois


def _run_container_backup_job(task_id, username, container_name):
    now = datetime.now(timezone.utc)
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
            db.execute("UPDATE container_backups SET statut = 'echec', erreur = ? WHERE id = ?", (str(e)[:500], backup_id))
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
            raise HTTPException(status_code=404, detail=f"Conteneur '{name}' introuvable")
        if domain.isActive():
            raise HTTPException(status_code=409, detail="Arrêtez le conteneur avant de le sauvegarder (pas de sauvegarde à chaud pour les conteneurs)")
    finally:
        conn.close()
    task_id = create_task("backup_container", name, username=user["username"])
    threading.Thread(target=_run_container_backup_job, args=(task_id, user["username"], name), daemon=True).start()
    return {"task_id": task_id}


@router.delete("/backups/{backup_id}")
def delete_container_backup(backup_id: int, confirm: bool = False, user: dict = Depends(require_role("admin"))):
    if not confirm:
        raise HTTPException(status_code=400, detail="Ajoutez ?confirm=true pour confirmer la suppression")
    with get_conn() as db:
        row = db.execute("SELECT * FROM container_backups WHERE id = ?", (backup_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Sauvegarde introuvable")
        Path(row["chemin"]).unlink(missing_ok=True)
        db.execute("DELETE FROM container_backups WHERE id = ?", (backup_id,))
        db.commit()
    log_action(user["username"], "delete_container_backup", row["container_name"], "succes")
    return {"message": "Sauvegarde supprimée"}


class RestoreContainerRequest(BaseModel):
    new_name: str | None = None  # None = restaure SOUS LE MEME NOM (le conteneur d'origine doit deja etre supprime)


@router.post("/backups/{backup_id}/restore", status_code=201)
def restore_container_backup(backup_id: int, payload: RestoreContainerRequest, user: dict = Depends(require_role("admin"))):
    with get_conn() as db:
        row = db.execute("SELECT * FROM container_backups WHERE id = ?", (backup_id,)).fetchone()
    if not row or row["statut"] != "termine":
        raise HTTPException(status_code=404, detail="Sauvegarde introuvable ou incomplète")
    target_name = payload.new_name or row["container_name"]
    name_error = validate_name(target_name)
    if name_error:
        raise HTTPException(status_code=422, detail=name_error)

    conn = open_lxc_conn()
    try:
        try:
            conn.lookupByName(target_name)
            raise HTTPException(status_code=409, detail=f"Un conteneur '{target_name}' existe déjà -- supprimez-le d'abord ou choisissez un autre nom")
        except libvirt.libvirtError:
            pass

        try:
            rootfs = restore_container_rootfs(Path(row["chemin"]), target_name, original_name=row["container_name"])
        except (subprocess.CalledProcessError, ValueError) as e:
            msg = e.stderr if isinstance(e, subprocess.CalledProcessError) and e.stderr else str(e)
            raise HTTPException(status_code=500, detail=f"Échec de restauration : {msg}")

        # LIMITE CONNUE : vcpu/memoire d'origine non conserves dans
        # l'archive (contrairement au clonage, qui les lit directement
        # depuis le domaine source encore defini) -- retombe sur les
        # valeurs par defaut, ajustables ensuite comme pour tout conteneur.
        mac = generate_mac(conn)
        xml = build_container_xml(target_name, 1, 512, rootfs, network="default", mac=mac)
        try:
            domain = conn.defineXML(xml)
        except libvirt.libvirtError as e:
            delete_container_rootfs(target_name)
            raise HTTPException(status_code=500, detail=f"Échec de définition du conteneur restauré : {describe_exception(e)}")

        ssh_user = get_container_ssh_user(row["container_name"])
        if ssh_user:
            set_container_ssh_user(target_name, ssh_user)
        log_action(user["username"], "restore_container_backup", target_name, "succes", f"depuis sauvegarde #{backup_id}")
        return _summary(domain)
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
