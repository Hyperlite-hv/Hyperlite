"""System settings of this node (app/core/host_system.py): package updates, DNS, time, remote syslog.
Administrators only: they change the host itself."""

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
        host_system.upgrade_command(payload.paquets)
    except host_system.SettingError as e:
        raise _refused(user, "host_upgrade", e) from e
    if not _upgrade_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A package upgrade is already running on this node")
    task_id = create_task("host_upgrade", "host", None, user["username"])
    log_action(user["username"], "host_upgrade", "host", "succes", ", ".join(payload.paquets)[:500])

    def work():
        try:
            code = host_system.run_upgrade(payload.paquets, lambda line: task_log(task_id, line))
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
