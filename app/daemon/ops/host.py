"""The node's own settings, done as root (docs/design/privilege-separation.md, step 3, first domain): what writes /etc,
runs systemctl or apt. The implementation stays in app/core/host_system.py; the routes (app/routers/host_system.py)
ask for these by name (app/core/privileged.py)."""

import functools
from typing import Literal

from pydantic import Field

from app.core import host_system
from app.core.tasks import task_log
from app.daemon.registry import Args, DaemonError, NoArgs, operation

# An apt run lasts minutes; the API's call waits for it (app/routers/host_system.py), the daemon serves others meanwhile.
UPGRADE_TIMEOUT_S = 3 * 3600


def _refusals(fn):
    """A setting host_system refuses (SettingError): the daemon's "refused", its message for the user."""

    @functools.wraps(fn)
    def wrapper(args, ctx):
        try:
            return fn(args, ctx)
        except host_system.SettingError as e:
            raise DaemonError("refused", str(e)) from e

    return wrapper


class AutoUpdatesArgs(Args):
    active: bool
    at: str = Field(max_length=5)


@operation("host.set_auto_updates", AutoUpdatesArgs)
@_refusals
def set_auto_updates(args, ctx):
    return host_system.set_auto_updates(args.active, args.at)


class DnsArgs(Args):
    servers: list[str] = Field(max_length=16)
    search: list[str] = Field(default=[], max_length=16)


@operation("host.set_dns", DnsArgs)
@_refusals
def set_dns(args, ctx):
    return host_system.set_dns(args.servers, args.search)


class HostnameArgs(Args):
    name: str = Field(max_length=253)


@operation("host.set_hostname", HostnameArgs)
@_refusals
def set_hostname(args, ctx):
    return host_system.set_hostname(args.name)


class TimeArgs(Args):
    timezone: str | None = Field(default=None, max_length=128)
    ntp: bool | None = None
    ntp_servers: list[str] | None = Field(default=None, max_length=16)


@operation("host.set_time", TimeArgs)
@_refusals
def set_time(args, ctx):
    return host_system.set_time(args.timezone, args.ntp, args.ntp_servers)


class SyslogArgs(Args):
    host: str | None = Field(default=None, max_length=253)
    port: int = 514
    protocol: str = Field(default="udp", max_length=8)


@operation("host.set_syslog", SyslogArgs)
@_refusals
def set_syslog(args, ctx):
    return host_system.set_syslog(args.host, args.port, args.protocol)


@operation("host.refresh_package_lists", NoArgs)
@_refusals
def refresh_package_lists(args, ctx):
    """apt-get update: what reading the upgradable packages from the mirrors again takes."""
    host_system.refresh_lists()
    return None


class UpgradeArgs(Args):
    task_id: str = Field(max_length=64)
    packages: list[str] = Field(max_length=2000)


@operation("host.upgrade", UpgradeArgs)
@_refusals
def upgrade(args, ctx):
    """Upgrade the packages named, checked again here against what apt lists as upgradable (hyperlite left out),
    apt's output into the task's log as it comes. Returns apt's exit code."""
    cmd = host_system.upgrade_command(args.packages)
    return {"code": host_system.run_upgrade(cmd, lambda line: task_log(args.task_id, line))}


class PowerArgs(Args):
    action: Literal["reboot", "poweroff"]


@operation("host.schedule_power", PowerArgs)
@_refusals
def schedule_power(args, ctx):
    host_system.schedule_power(args.action)
    return {"delay_s": host_system.POWER_DELAY_S}
