"""System settings of this node (app/core/host_system.py): package updates, host name, DNS, time, remote syslog.
Administrators only: they change the host itself."""

import socket
import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import host_system
from app.core.audit import log_action
from app.core.security import require_role
from app.core.tasks import create_task, finish_task, task_log

router = APIRouter(prefix="/host/system", tags=["host"])

# One package run at a time: apt holds the dpkg lock, a second run would only fail on it.
_upgrade_lock = threading.Lock()


def _refused(user, action, e):
    log_action(user["username"], action, "host", "echec", str(e))
    return HTTPException(status_code=422, detail=str(e))


@router.get("/updates")
def get_updates(refresh: bool = False, user: dict = Depends(require_role("admin"))):
    try:
        return host_system.updates(refresh)
    except host_system.SettingError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


class UpgradeRequest(BaseModel):
    paquets: list[str]


@router.post("/updates/upgrade", status_code=202)
def upgrade(payload: UpgradeRequest, user: dict = Depends(require_role("admin"))):
    try:
        cmd = host_system.upgrade_command(payload.paquets)
    except host_system.SettingError as e:
        raise _refused(user, "host_upgrade", e) from e
    if not _upgrade_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A package upgrade is already running on this node")
    task_id = create_task("host_upgrade", "host", None, user["username"])
    log_action(user["username"], "host_upgrade", "host", "succes", ", ".join(payload.paquets)[:500])

    def work():
        try:
            code = host_system.run_upgrade(cmd, lambda line: task_log(task_id, line))
            finish_task(
                task_id, "termine" if code == 0 else "echec", None if code == 0 else f"apt-get exited with {code}"
            )
        except Exception as e:  # the task must end whatever happened, with the cause in its log
            finish_task(task_id, "echec", str(e))
        finally:
            _upgrade_lock.release()

    threading.Thread(target=work, name="host-upgrade", daemon=True).start()
    return {"tache": task_id}


class DnsSettings(BaseModel):
    serveurs: list[str]
    recherche: list[str] = []


@router.get("/dns")
def get_dns(user: dict = Depends(require_role("admin"))):
    return host_system.dns()


@router.put("/dns")
def put_dns(payload: DnsSettings, user: dict = Depends(require_role("admin"))):
    try:
        result = host_system.set_dns(payload.serveurs, payload.recherche)
    except host_system.SettingError as e:
        raise _refused(user, "host_dns", e) from e
    log_action(user["username"], "host_dns", "host", "succes", " ".join(payload.serveurs))
    return result


class HostnameSettings(BaseModel):
    nom: str


@router.put("/hostname")
def put_hostname(payload: HostnameSettings, user: dict = Depends(require_role("admin"))):
    """Rename this node: its host name (hostnamectl) and its line in /etc/hosts. The name libvirt reports, and so
    the one shown for this node, follows at once."""
    try:
        result = host_system.set_hostname(payload.nom)
    except host_system.SettingError as e:
        raise _refused(user, "host_hostname", e) from e
    log_action(user["username"], "host_hostname", result["ancien"], "succes", f"-> {result['nom']}")
    return result


class TimeSettings(BaseModel):
    fuseau: str | None = None
    ntp: bool | None = None
    serveurs_ntp: list[str] | None = None


@router.get("/time")
def get_time(user: dict = Depends(require_role("admin"))):
    try:
        return host_system.time_settings()
    except host_system.SettingError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.get("/time/zones")
def get_timezones(user: dict = Depends(require_role("admin"))):
    return host_system.timezones()


@router.put("/time")
def put_time(payload: TimeSettings, user: dict = Depends(require_role("admin"))):
    try:
        result = host_system.set_time(payload.fuseau, payload.ntp, payload.serveurs_ntp)
    except host_system.SettingError as e:
        raise _refused(user, "host_time", e) from e
    log_action(user["username"], "host_time", "host", "succes", f"{payload.fuseau} ntp={payload.ntp}")
    return result


class SyslogSettings(BaseModel):
    hote: str | None = None
    port: int = 514
    protocole: str = "udp"


@router.get("/syslog")
def get_syslog(user: dict = Depends(require_role("admin"))):
    return host_system.syslog()


@router.put("/syslog")
def put_syslog(payload: SyslogSettings, user: dict = Depends(require_role("admin"))):
    try:
        result = host_system.set_syslog(payload.hote, payload.port, payload.protocole)
    except host_system.SettingError as e:
        raise _refused(user, "host_syslog", e) from e
    log_action(
        user["username"],
        "host_syslog",
        "host",
        "succes",
        f"{payload.protocole}://{payload.hote}:{payload.port}" if payload.hote else "off",
    )
    return result


class PowerRequest(BaseModel):
    action: str  # "reboot" | "poweroff"
    confirmation: str  # the node's host name, typed by the administrator
    arreter_invites: bool = False


@router.post("/power", status_code=202)
def power(payload: PowerRequest, user: dict = Depends(require_role("admin"))):
    """Reboot or shut down this node. The host name must be typed back; running VMs and containers are either a
    refusal (listed) or shut down cleanly first, and the node goes down only when all of them stopped."""
    if payload.action not in host_system.POWER_ACTIONS:
        raise HTTPException(status_code=422, detail="action must be reboot or poweroff")
    hostname = socket.gethostname()
    if payload.confirmation.strip() != hostname:
        raise HTTPException(status_code=422, detail=f"Type the node's name ({hostname}) to confirm")
    guests = host_system.running_guests()
    if guests and not payload.arreter_invites:
        names = ", ".join(n for _, n in guests)
        raise HTTPException(
            status_code=409,
            detail=f"Running on this node: {names}. Move them (maintenance mode) or choose to shut them down first",
        )
    task_id = create_task(f"node_{payload.action}", hostname, None, user["username"])
    log_action(user["username"], f"node_{payload.action}", hostname, "succes", f"{len(guests)} guests to stop")

    def work():
        try:
            if guests:
                left = host_system.stop_guests(guests, lambda line: task_log(task_id, line))
                if left:
                    finish_task(
                        task_id, "echec", f"Still running after the wait, {payload.action} cancelled: {', '.join(left)}"
                    )
                    return
            host_system.schedule_power(payload.action)
            task_log(task_id, f"{payload.action} in {host_system.POWER_DELAY_S} s")
            finish_task(task_id, "termine")
        except Exception as e:  # the task must end whatever happened, with the cause in its log
            finish_task(task_id, "echec", str(e))

    threading.Thread(target=work, name="node-power", daemon=True).start()
    return {"tache": task_id}
