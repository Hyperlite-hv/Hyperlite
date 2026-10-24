"""The VMs' root work (docs/design/privilege-separation.md, section 13, the VM domain). Part 1: power, libvirt's
autostart flag, the pre-stop hook script and the guest agent's addresses. An operation names the VM; the daemon looks
it up in this node's libvirt itself. The routes keep who may ask, the locks, the checks before a task, the audit log
and their answers; another node's VM keeps its own connection until the cluster domain."""

import functools
from typing import Literal

from pydantic import Field

from app.daemon.registry import Args, DaemonError, operation

VM_NAME = 64
TASK_ID = 64


def _libvirt(fn):
    """A libvirt failure is "failed" with libvirt's own reason, which the routes show as before."""

    @functools.wraps(fn)
    def wrapper(args, ctx):
        import libvirt

        from app.core.error_messages import describe_exception

        try:
            return fn(args, ctx)
        except libvirt.libvirtError as e:
            raise DaemonError("failed", describe_exception(e)) from e

    return wrapper


def _domain(conn, vm):
    import libvirt

    try:
        return conn.lookupByName(vm)
    except libvirt.libvirtError:
        raise DaemonError("not_found", f"VM '{vm}' not found") from None


class PowerArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    action: Literal["start", "shutdown", "stop", "reboot", "reset"]


@operation("vm.power", PowerArgs)
@_libvirt
def power(args, ctx):
    """start; shutdown and reboot through the guest agent when it is there, else ACPI (guest_agent); stop pulls the
    plug; reset stops and starts again. Returns {"method"}: "agent" or "acpi" for a shutdown or a reboot."""
    from app.core import guest_agent, libvirt_utils

    with libvirt_utils.local_conn() as conn:
        domain = _domain(conn, args.vm)
        running = bool(domain.isActive())
        if args.action == "start":
            if running:
                raise DaemonError("in_use", f"VM '{args.vm}' is already running")
            domain.create()
            return {"method": None}
        if not running:
            raise DaemonError("in_use", f"VM '{args.vm}' is stopped")
        if args.action == "shutdown":
            return {"method": guest_agent.shutdown(domain)}
        if args.action == "reboot":
            return {"method": guest_agent.reboot(domain)}
        domain.destroy()
        if args.action == "reset":
            domain.create()
        return {"method": None}


class AutostartArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    autostart: bool


@operation("vm.autostart", AutostartArgs)
@_libvirt
def autostart(args, ctx):
    """libvirt's own autostart flag; Hyperlite's start at boot turns it off for the VMs it starts (app/core/vm_boot.py)."""
    from app.core import libvirt_utils

    with libvirt_utils.local_conn() as conn:
        _domain(conn, args.vm).setAutostart(1 if args.autostart else 0)


class PreStopArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)


@operation("vm.pre_stop", PreStopArgs)
@_libvirt
def pre_stop(args, ctx):
    """The VM's pre-stop hook script, before a shutdown or a stop (libvirt has no hook there): a script of the root
    library (app/core/hookscript.py), run as root here. Returns the run, or None without a script; the API writes it
    into the task."""
    import subprocess
    import xml.etree.ElementTree as ET

    from app.core import hookscript, libvirt_utils

    with libvirt_utils.local_conn() as conn:
        xml = _domain(conn, args.vm).XMLDesc(0)
    script = hookscript.script_of_domain_xml(ET.fromstring(xml))
    if not script:
        return None
    try:
        return hookscript.run(args.vm, "pre-stop", script, xml)
    except (hookscript.HookError, OSError, ValueError, subprocess.SubprocessError) as e:
        return {"ok": False, "exit_code": None, "phase": "pre-stop", "output": str(e), "seconds": 0}


def _agent_domains(names):
    """The running VMs named, through a connection this call does not close: a question still in flight when the list
    returns holds it, and its answer serves the next list (guest_agent.ipv4_many)."""
    import libvirt

    from app.core import libvirt_utils

    conn = libvirt_utils.open_local()
    found = []
    for name in names:
        try:
            found.append(conn.lookupByName(name))
        except libvirt.libvirtError:
            continue
    return found


class AgentArgs(Args):
    vms: list[str] = Field(max_length=4096)
    wait_s: float = Field(0.5, ge=0, le=5)


@operation("vm.agent_ipv4", AgentArgs)
@_libvirt
def agent_ipv4(args, ctx):
    """{uuid: address or None} of the running VMs named, from their guest agents, waiting at most `wait_s` for them
    all: asking an agent needs a connection that may write, which the API will not keep."""
    from app.core import guest_agent

    if any(len(name) > VM_NAME for name in args.vms):
        raise DaemonError("invalid_arguments", "A VM's name is too long")
    return guest_agent.ipv4_many(_agent_domains(args.vms), wait=args.wait_s)
