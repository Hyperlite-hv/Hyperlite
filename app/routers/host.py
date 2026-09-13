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
from app.core.security import require_role
from app.core.tasks import create_task, finish_task

router = APIRouter(prefix="/host", tags=["host"])

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
