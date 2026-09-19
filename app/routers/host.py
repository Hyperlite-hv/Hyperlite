"""Shell interactif directement sur l'hote physique (le serveur qui fait
tourner Hyperlite) -- equivalent DCUI/ESXi Shell de vSphere, mais un shell
complet plutot qu'un menu restreint.

ATTENTION SECURITE : hyperlite.service tourne en root (voir le fichier unit
systemd, pas de `User=`), donc ce shell est un acces root complet a la
machine physique -- strictement plus sensible que le terminal SSH par VM
deja existant (lui-meme deja limite aux admins pour la meme raison). Pas de
sandboxing/liste blanche de commandes ici : la seule barriere est le controle
d'acces (admin uniquement), un ticket a usage unique de duree de vie courte
(comme les autres consoles), et une tracabilite complete (chaque ouverture
et fermeture de session passe par app.core.tasks + app.core.audit, donc
visible dans l'onglet Tâches ET dans le Journal).
"""
import asyncio
import fcntl
import json
import os
import pty
import secrets
import signal
import socket
import struct
import subprocess
import termios
import time

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

from app.core.audit import log_action
from app.core.security import get_current_user, require_role
from app.core.tasks import create_task, finish_task
from app.core.host_capabilities import get_local_capabilities
from app.core.vm_limits import compute_limits
from app.core import deployment_profile
from pydantic import BaseModel
from app.core.error_messages import describe_exception

router = APIRouter(prefix="/host", tags=["host"])


@router.get("/limits")
def host_vm_limits(user: dict = Depends(get_current_user)):
    """Limites de ressources par VM derivees de l'hote reel (chantier 2 du
    mandat portabilite) -- consommees par l'UI pour borner les champs, et
    par la validation backend. Chaque limite indique sa source
    (detecte/configuration/repli)."""
    return compute_limits()


class ProfileChoice(BaseModel):
    profil: str  # "auto" ou un nom de profil


def _profile_payload():
    active = deployment_profile.get_active()
    limits = compute_limits(force=True)
    d = dict(active["reglages"]["vm_defaults"])
    # Valeurs par defaut de l'assistant de creation : jamais au-dela des
    # limites reellement calculees pour cet hote.
    d["vcpu"] = max(limits["vcpu"]["min"], min(d["vcpu"], limits["vcpu"]["max"]))
    d["memory_mb"] = max(limits["memoire_mo"]["min"], min(d["memory_mb"], limits["memoire_mo"]["max"]))
    d["disk_gb"] = max(limits["disque_go"]["min"], min(d["disk_gb"], limits["disque_go"]["max"]))
    from app.core import vm_limits
    return {**active, "vm_defaults_effectifs": d, "profils": deployment_profile.PROFILES,
            "allocation": {**vm_limits.get_policy(), "politiques": vm_limits.POLICIES}}


@router.get("/profile")
def host_profile(user: dict = Depends(get_current_user)):
    """Profil de deploiement actif (chantier 5 du mandat portabilite) :
    homelab/standard/avance, recommande par detection du materiel ou choisi
    par un admin. Voir app/core/deployment_profile.py."""
    return _profile_payload()


@router.put("/profile")
def set_host_profile(payload: ProfileChoice, user: dict = Depends(require_role("admin"))):
    choice = payload.profil.strip().lower()
    if choice != "auto" and choice not in deployment_profile.PROFILES:
        raise HTTPException(status_code=400, detail=f"Profil inconnu : {payload.profil} (auto, {', '.join(deployment_profile.PROFILES)})")
    if deployment_profile.env_override():
        raise HTTPException(status_code=409, detail="Le profil est forcé par la variable d'environnement HYPERLITE_PROFILE : elle est prioritaire sur ce choix.")
    deployment_profile.set_choice(choice)
    log_action(user["username"], "set_host_profile", "local", "succes", f"profil={choice}")
    return _profile_payload()


class AllocationChoice(BaseModel):
    politique: str


@router.put("/allocation")
def set_host_allocation(payload: AllocationChoice, user: dict = Depends(require_role("admin"))):
    """Politique d'allocation des ressources de VM (limites/surallocation/
    libre) -- voir app/core/vm_limits.py."""
    from app.core import vm_limits
    choice = payload.politique.strip().lower()
    if choice not in vm_limits.POLICIES:
        raise HTTPException(status_code=400, detail=f"Politique inconnue : {payload.politique} ({', '.join(vm_limits.POLICIES)})")
    if vm_limits.env_policy():
        raise HTTPException(status_code=409, detail="La politique est forcée par la variable d'environnement HYPERLITE_ALLOCATION : elle est prioritaire sur ce choix.")
    vm_limits.set_policy(choice)
    log_action(user["username"], "set_host_allocation", "local", "succes", f"politique={choice}")
    return _profile_payload()


@router.get("/capabilities")
def host_capabilities(user: dict = Depends(get_current_user)):
    """Profil de capacites de l'hote LOCAL (mandat portabilite
    2026-09-18, chantier 1 -- voir CLAUDE.md). Fondation pour les limites
    de VM dynamiques, la page "Compatibilité et capacités" et le
    diagnostic de compatibilite de cluster -- voir app/core/
    host_capabilities.py pour le detail de chaque sous-profil."""
    try:
        result = get_local_capabilities()
    except Exception as e:
        msg = describe_exception(e)
        log_action(user["username"], "get_host_capabilities", "local", "echec", msg)
        raise HTTPException(status_code=500, detail=f"Erreur de découverte des capacités : {msg}")
    log_action(user["username"], "get_host_capabilities", "local", "succes")
    return result

@router.get("/preflight")
def host_preflight(user: dict = Depends(require_role("admin"))):
    """Preflight check (mandat portabilite, chantier 3) rejoue a chaud sur
    l'hote local : memes controles que a l'installation (voir
    app/core/preflight.py), dependances Python sondees dans l'interpreteur
    du service lui-meme."""
    import sys
    from app.core import preflight
    try:
        report = preflight.run(python=sys.executable, requirements=str(preflight.APP_DIR / "requirements.txt"))
    except Exception as e:
        msg = describe_exception(e)
        log_action(user["username"], "host_preflight", "local", "echec", msg)
        raise HTTPException(status_code=500, detail=f"Erreur du preflight : {msg}")
    log_action(user["username"], "host_preflight", "local", "succes")
    return report


# Meme pattern ticket-court-duree-de-vie-a-usage-unique que TERMINAL_TICKETS
# dans app/routers/vms.py (VM console/terminal) : un jeton JWT classique
# resterait valide pour toute sa duree de vie si intercepte, un ticket est
# consomme (pop) des la premiere connexion WebSocket.
HOST_SHELL_TICKETS = {}
HOST_SHELL_TICKET_TTL = 30


def _set_pty_size(fd, cols, rows):
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except OSError:
        pass


@router.post("/terminal-ticket")
def create_host_terminal_ticket(user: dict = Depends(require_role("admin"))):
    now = time.time()
    for old_ticket, (old_user, old_expiry) in list(HOST_SHELL_TICKETS.items()):
        if old_expiry < now:
            HOST_SHELL_TICKETS.pop(old_ticket, None)

    ticket = secrets.token_urlsafe(24)
    HOST_SHELL_TICKETS[ticket] = (user["username"], now + HOST_SHELL_TICKET_TTL)
    log_action(user["username"], "create_host_terminal_ticket", socket.gethostname(), "succes")
    return {"ticket": ticket, "expire_dans_s": HOST_SHELL_TICKET_TTL}


@router.websocket("/terminal")
async def host_terminal(websocket: WebSocket):
    ticket = websocket.query_params.get("ticket")
    entry = HOST_SHELL_TICKETS.pop(ticket, None) if ticket else None
    if entry is None:
        await websocket.close(code=4401)
        return

    username, expiry = entry
    if time.time() > expiry:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    hostname = socket.gethostname()

    # Une session shell peut durer des heures -- contrairement aux actions VM
    # instantanees, "termine" a la cloture de la tache signifie ici juste
    # "session fermee proprement" (voir aussi finish_task plus bas), pas un
    # succes/echec d'operation au sens usuel.
    task_id = create_task("host_shell", hostname, node=hostname, username=username)
    log_action(username, "host_shell_open", hostname, "succes")

    master_fd, slave_fd = pty.openpty()
    _set_pty_size(master_fd, 80, 24)
    shell = os.environ.get("SHELL", "/bin/bash")
    try:
        proc = subprocess.Popen(
            [shell, "-l"],
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            # Nouvelle session (setsid) : le shell obtient un vrai controle de
            # terminal (Ctrl+C, job control, commandes interactives comme
            # `top`/`vim`) au lieu de rester rattache au groupe de processus
            # d'uvicorn.
            preexec_fn=os.setsid,
            env={**os.environ, "TERM": "xterm-256color"},
            close_fds=True,
        )
    finally:
        # Le parent n'a plus besoin de son bout du pty une fois le process
        # enfant lance (lui seul le garde ouvert via stdin/stdout/stderr) --
        # sans ce close, master_fd ne verrait jamais d'EOF a la fin du shell.
        os.close(slave_fd)

    os.set_blocking(master_fd, False)
    loop = asyncio.get_event_loop()
    queue = asyncio.Queue()

    def _on_readable():
        try:
            data = os.read(master_fd, 65536)
        except OSError:
            data = b""
        queue.put_nowait(data)
        if not data:
            try:
                loop.remove_reader(master_fd)
            except (ValueError, OSError):
                pass

    loop.add_reader(master_fd, _on_readable)

    async def pty_to_ws():
        while True:
            data = await queue.get()
            if not data:
                break
            await websocket.send_text(data.decode(errors="replace"))

    async def ws_to_pty():
        try:
            while True:
                msg = await websocket.receive_text()
                if msg.startswith("\x00"):
                    try:
                        dims = json.loads(msg[1:])
                        _set_pty_size(master_fd, int(dims["cols"]), int(dims["rows"]))
                    except (ValueError, KeyError, TypeError):
                        pass
                else:
                    os.write(master_fd, msg.encode())
        except (WebSocketDisconnect, RuntimeError):
            pass
        except OSError:
            pass

    task1 = asyncio.ensure_future(pty_to_ws())
    task2 = asyncio.ensure_future(ws_to_pty())
    done, pending = await asyncio.wait({task1, task2}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()

    try:
        loop.remove_reader(master_fd)
    except (ValueError, OSError):
        pass
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        os.close(master_fd)
    except OSError:
        pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    finish_task(task_id, "termine")
    log_action(username, "host_shell_close", hostname, "succes")
    try:
        await websocket.close()
    except (RuntimeError, WebSocketDisconnect):
        pass
