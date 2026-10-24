"""System settings of this node (app/core/host_system.py): package updates, host name, DNS, time, remote syslog.
Administrators only: they change the host itself. What needs root is done by hyperlite-daemon (app/daemon/ops/host.py,
docs/design/privilege-separation.md, step 3); these routes keep who may ask, the audit log and the tasks."""

import socket
import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import host_system, privileged
from app.core.audit import log_action
from app.core.listing import listing
from app.core.security import require_role
from app.core.tasks import create_task, finish_task, task_log

router = APIRouter(prefix="/host/system", tags=["host"])

# One package run at a time: apt holds the dpkg lock, a second run would only fail on it.
_upgrade_lock = threading.Lock()


def _refused(user, action, e):
    log_action(user["username"], action, "host", "failed", str(e))
    return HTTPException(status_code=422, detail=str(e))


@router.get("/updates")
def get_updates(refresh: bool = False, user: dict = Depends(require_role("admin"))):
    try:
        return host_system.updates(refresh, user=user["username"])
    except host_system.SettingError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


class UpgradeRequest(BaseModel):
    packages: list[str]


@router.post("/updates/upgrade", status_code=202)
def upgrade(payload: UpgradeRequest, user: dict = Depends(require_role("admin"))):
    try:
        # Checked here too, so a name apt does not list is a 422 at once rather than a failed task.
        host_system.upgrade_command(payload.packages)
    except host_system.SettingError as e:
        raise _refused(user, "host_upgrade", e) from e
    if not _upgrade_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A package upgrade is already running on this node")
    task_id = create_task("host_upgrade", "host", None, user["username"])
    log_action(user["username"], "host_upgrade", "host", "success", ", ".join(payload.packages)[:500])

    def work():
        try:
            # Run by hyperlite-daemon, which checks the names against apt's list again; apt's output goes into the task.
            code = privileged.run(
                "host.upgrade", user=user["username"], timeout=3 * 3600, task_id=task_id, packages=payload.packages
            )["code"]
            finish_task(
                task_id, "succeeded" if code == 0 else "failed", None if code == 0 else f"apt-get exited with {code}"
            )
        except Exception as e:  # the task must end whatever happened, with the cause in its log
            finish_task(task_id, "failed", str(e))
        finally:
            _upgrade_lock.release()

    threading.Thread(target=work, name="host-upgrade", daemon=True).start()
    return {"task": task_id}


class AutoUpdates(BaseModel):
    active: bool
    time: str = Field(default="03:30", max_length=5)


@router.get("/auto-updates")
def get_auto_updates(user: dict = Depends(require_role("admin"))):
    return host_system.auto_updates()


@router.put("/auto-updates")
def put_auto_updates(payload: AutoUpdates, user: dict = Depends(require_role("admin"))):
    result = privileged.ask(
        "host.set_auto_updates", user, "set_auto_updates", "host", active=payload.active, at=payload.time
    )
    log_action(
        user["username"],
        "set_auto_updates",
        "host",
        "success",
        f"{'on' if payload.active else 'off'} at {payload.time}",
    )
    return result


class DnsSettings(BaseModel):
    servers: list[str]
    search: list[str] = []


@router.get("/dns")
def get_dns(user: dict = Depends(require_role("admin"))):
    return host_system.dns()


@router.put("/dns")
def put_dns(payload: DnsSettings, user: dict = Depends(require_role("admin"))):
    result = privileged.ask("host.set_dns", user, "host_dns", "host", servers=payload.servers, search=payload.search)
    log_action(user["username"], "host_dns", "host", "success", " ".join(payload.servers))
    return result


class HostnameSettings(BaseModel):
    name: str


@router.put("/hostname")
def put_hostname(payload: HostnameSettings, user: dict = Depends(require_role("admin"))):
    """Rename this node: its host name (/etc/hostname) and its line in /etc/hosts. The name libvirt reports, and so
    the one shown for this node, follows at once."""
    result = privileged.ask("host.set_hostname", user, "host_hostname", "host", name=payload.name)
    log_action(user["username"], "host_hostname", result["old"], "success", f"-> {result['name']}")
    return result


class TimeSettings(BaseModel):
    timezone: str | None = None
    ntp: bool | None = None
    ntp_servers: list[str] | None = None


@router.get("/time")
def get_time(user: dict = Depends(require_role("admin"))):
    try:
        return host_system.time_settings()
    except host_system.SettingError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.get("/time/zones")
def get_timezones(user: dict = Depends(require_role("admin"))):
    return listing(host_system.timezones())


@router.put("/time")
def put_time(payload: TimeSettings, user: dict = Depends(require_role("admin"))):
    result = privileged.ask(
        "host.set_time",
        user,
        "host_time",
        "host",
        timezone=payload.timezone,
        ntp=payload.ntp,
        ntp_servers=payload.ntp_servers,
    )
    log_action(user["username"], "host_time", "host", "success", f"{payload.timezone} ntp={payload.ntp}")
    return result


class SyslogSettings(BaseModel):
    host: str | None = None
    port: int = 514
    protocol: str = "udp"


@router.get("/syslog")
def get_syslog(user: dict = Depends(require_role("admin"))):
    return host_system.syslog()


@router.put("/syslog")
def put_syslog(payload: SyslogSettings, user: dict = Depends(require_role("admin"))):
    result = privileged.ask(
        "host.set_syslog", user, "host_syslog", "host", host=payload.host, port=payload.port, protocol=payload.protocol
    )
    log_action(
        user["username"],
        "host_syslog",
        "host",
        "success",
        f"{payload.protocol}://{payload.host}:{payload.port}" if payload.host else "off",
    )
    return result


class PowerRequest(BaseModel):
    action: str  # "reboot" | "poweroff"
    confirmation: str  # the node's host name, typed by the administrator
    stop_guests: bool = False


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
    if guests and not payload.stop_guests:
        names = ", ".join(n for _, n in guests)
        raise HTTPException(
            status_code=409,
            detail=f"Running on this node: {names}. Move them (maintenance mode) or choose to shut them down first",
        )
    task_id = create_task(f"node_{payload.action}", hostname, None, user["username"])
    log_action(user["username"], f"node_{payload.action}", hostname, "success", f"{len(guests)} guests to stop")

    def work():
        try:
            if guests:
                left = host_system.stop_guests(guests, lambda line: task_log(task_id, line))
                if left:
                    finish_task(
                        task_id,
                        "failed",
                        f"Still running after the wait, {payload.action} cancelled: {', '.join(left)}",
                    )
                    return
            privileged.run("host.schedule_power", user=user["username"], action=payload.action)
            task_log(task_id, f"{payload.action} in {host_system.POWER_DELAY_S} s")
            finish_task(task_id, "succeeded")
        except Exception as e:  # the task must end whatever happened, with the cause in its log
            finish_task(task_id, "failed", str(e))

    threading.Thread(target=work, name="node-power", daemon=True).start()
    return {"task": task_id}
