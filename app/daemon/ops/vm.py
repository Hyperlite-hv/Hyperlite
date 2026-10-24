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


# --- Part 2a: a VM's processors, memory, limits, CPU pinning and advanced hardware ---

MAX_VCPU = 1024
MAX_MEMORY_MB = 1 << 24


def _definition(fn):
    """A change to the VM's definition, in this node's libvirt: the VM looked up here, what the module refuses a
    "refused" with its reason, a libvirt failure a "failed" with libvirt's."""

    @functools.wraps(fn)
    def wrapper(args, ctx):
        from app.core import cpu_pinning, libvirt_utils
        from app.core import vm_hardware_opts as opts

        with libvirt_utils.local_conn() as conn:
            domain = _domain(conn, args.vm)
            try:
                return fn(args, conn, domain)
            except opts.OptionError as e:
                raise DaemonError("refused", str(e)) from e
            except cpu_pinning.PinningError as e:
                raise DaemonError("refused" if e.status == 422 else "in_use", e.message) from e

    return _libvirt(wrapper)


class ResourcesArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    vcpu: int | None = Field(None, ge=1, le=MAX_VCPU)
    memory_mb: int | None = Field(None, ge=1, le=MAX_MEMORY_MB)


@operation("vm.set_resources", ResourcesArgs)
@_definition
def set_resources(args, conn, domain):
    """Processors and memory of a stopped VM (the routes check the host's limits first, app/core/vm_limits.py)."""
    import libvirt

    if domain.isActive():
        raise DaemonError("in_use", "Stop the VM before changing its resources")
    if args.vcpu is not None:
        # The maximum first, or libvirt refuses a current value above the old maximum.
        domain.setVcpusFlags(args.vcpu, libvirt.VIR_DOMAIN_AFFECT_CONFIG | libvirt.VIR_DOMAIN_VCPU_MAXIMUM)
        domain.setVcpusFlags(args.vcpu, libvirt.VIR_DOMAIN_AFFECT_CONFIG)
    if args.memory_mb is not None:
        kib = args.memory_mb * 1024
        domain.setMemoryFlags(kib, libvirt.VIR_DOMAIN_AFFECT_CONFIG | libvirt.VIR_DOMAIN_MEM_MAXIMUM)
        domain.setMemoryFlags(kib, libvirt.VIR_DOMAIN_AFFECT_CONFIG)


class LimitsArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    cpu_shares: int = Field(ge=2, le=262144)
    cpu_limit_pct: int | None = Field(None, ge=1, le=100)
    mem_hard_limit_mb: int | None = Field(None, ge=64, le=MAX_MEMORY_MB)


@operation("vm.set_limits", LimitsArgs)
@_definition
def set_limits(args, conn, domain):
    """CPU shares and cap, and a hard memory cap, through libvirt's cgroup parameters; live too on a running VM."""
    import libvirt

    from app.core.vm_limits import CPU_PERIOD_US, UNLIMITED_KB

    nvcpu = domain.info()[3] or 1
    vcpu_quota = -1 if args.cpu_limit_pct is None else int(CPU_PERIOD_US * nvcpu * args.cpu_limit_pct / 100)
    flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
    if domain.isActive():
        flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
    domain.setSchedulerParametersFlags(
        {"cpu_shares": args.cpu_shares, "vcpu_period": CPU_PERIOD_US, "vcpu_quota": vcpu_quota}, flags
    )
    hard_limit_kb = UNLIMITED_KB if args.mem_hard_limit_mb is None else args.mem_hard_limit_mb * 1024
    domain.setMemoryParameters({"hard_limit": hard_limit_kb}, flags)


class PinningArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    cpuset: str | None = Field(None, max_length=200)
    strict: bool = False


@operation("vm.set_cpu_pinning", PinningArgs)
@_definition
def set_cpu_pinning(args, conn, domain):
    """The host CPUs (and NUMA cell) the VM runs on, checked against this host's topology (cpu_pinning.plan),
    written to the definition and applied to a running VM's threads. Returns the plan."""
    import xml.etree.ElementTree as ET

    import libvirt

    from app.core import cpu_pinning

    root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    nvcpu = int((root.findtext("vcpu") or "1").strip())
    how = cpu_pinning.plan(cpu_pinning.host_topology(conn), nvcpu, args.cpuset, args.strict)
    cpu_pinning.apply_to_xml(root, nvcpu, how)
    conn.defineXML(ET.tostring(root, encoding="unicode"))
    if domain.isActive():
        cpu_pinning.apply_live(domain, conn, domain.info()[3] or nvcpu, how)
    return how


class DiskOptionsArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    target: str = Field(pattern=r"^[a-z]{2,4}[0-9]{0,2}$")
    cache: str | None = Field(None, max_length=32)
    discard: str | None = Field(None, max_length=32)
    io: str | None = Field(None, max_length=32)
    iothread: bool = False
    iops: int | None = Field(None, ge=0)
    mbps: int | None = Field(None, ge=0)


@operation("vm.set_disk_options", DiskOptionsArgs)
@_definition
def set_disk_options(args, conn, domain):
    """A disk's cache, discard, I/O mode, I/O thread and throughput limits (vm_hardware_opts, which checks them)."""
    from app.core import vm_hardware_opts as opts

    fields = args.model_dump(exclude={"vm", "target"})
    return opts.set_disk_options(conn, domain, args.target, fields)


class BootOrderArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    position: list[str] = Field(min_length=1, max_length=32)


@operation("vm.set_boot_order", BootOrderArgs)
@_definition
def set_boot_order(args, conn, domain):
    from app.core import vm_hardware_opts as opts

    if any(len(device) > 64 for device in args.position):
        raise DaemonError("invalid_arguments", "A boot device's name is too long")
    return opts.set_boot_order(conn, domain, args.position)


class BalloonArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    active: bool
    minimum_mb: int | None = Field(None, ge=256, le=MAX_MEMORY_MB)


@operation("vm.set_balloon", BalloonArgs)
@_definition
def set_balloon(args, conn, domain):
    from app.core import vm_hardware_opts as opts

    return opts.set_balloon(conn, domain, args.active, args.minimum_mb)


class MachineArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    machine: str = Field(min_length=1, max_length=64)


@operation("vm.set_machine", MachineArgs)
@_definition
def set_machine(args, conn, domain):
    """A newer version of the VM's machine family only, on a stopped VM (vm_hardware_opts.set_machine)."""
    from app.core import vm_hardware_opts as opts

    return opts.set_machine(conn, domain, args.machine)
