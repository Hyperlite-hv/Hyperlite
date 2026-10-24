"""The VMs' root work (docs/design/privilege-separation.md, section 13, the VM domain). Part 1: power, libvirt's
autostart flag, the pre-stop hook script and the guest agent's addresses. An operation names the VM; the daemon looks
it up in this node's libvirt itself. The routes keep who may ask, the locks, the checks before a task, the audit log
and their answers; another node's VM keeps its own connection until the cluster domain."""

import functools
from typing import Annotated, Literal

from pydantic import Field, field_validator

from app.core.vm_create import VMCreate
from app.daemon.ops.network import FirewallConfig
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
        from app.core import cpu_pinning, libvirt_utils, vm_devices
        from app.core import vm_hardware_opts as opts

        with libvirt_utils.local_conn() as conn:
            domain = _domain(conn, args.vm)
            try:
                return fn(args, conn, domain)
            except opts.OptionError as e:
                raise DaemonError("refused", str(e)) from e
            except cpu_pinning.PinningError as e:
                raise DaemonError("refused" if e.status == 422 else "in_use", e.message) from e
            except vm_devices.DeviceError as e:
                raise DaemonError(e.kind, e.message) from e

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


# --- Part 2b: a VM's devices (app/core/vm_devices.py) ---

TARGET = r"^[a-z]{2,4}[0-9]{0,2}$"  # the routes' own rule (app/routers/vms/_shared.py, TARGET_DEV_RE)
MAC = r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$"
NAME = 64


class AttachDiskArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    pool: str = Field(min_length=1, max_length=NAME)
    volume: str = Field(min_length=1, max_length=256)
    target: str = Field(pattern=TARGET)


@operation("vm.attach_disk", AttachDiskArgs)
@_definition
def attach_disk(args, conn, domain):
    """A volume of a pool, found here, attached with the format it really has, once it names no other file to read."""
    from app.core import vm_devices

    vm_devices.attach_disk(conn, domain, args.pool, args.volume, args.target)


class DiskTargetArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    target: str = Field(pattern=TARGET)


@operation("vm.detach_disk", DiskTargetArgs)
@_definition
def detach_disk(args, conn, domain):
    from app.core import vm_devices

    vm_devices.detach_disk(domain, args.target)


class InterfaceArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    network: str = Field(min_length=1, max_length=NAME)
    vlan_tag: int | None = Field(None, ge=1, le=4094)
    vlan_trunks: str | None = Field(None, max_length=200)


class SetInterfaceArgs(InterfaceArgs):
    mac: str | None = Field(None, max_length=32)


@operation("vm.set_interface", SetInterfaceArgs)
@_definition
def set_interface(args, conn, domain):
    """An interface moved to another network in place, keeping its MAC address, with its VLANs."""
    from app.core import vm_devices

    vm_devices.set_interface(conn, domain, args.mac, args.network, args.vlan_tag, args.vlan_trunks)


@operation("vm.attach_interface", InterfaceArgs)
@_definition
def attach_interface(args, conn, domain):
    from app.core import vm_devices

    vm_devices.attach_interface(conn, domain, args.network, args.vlan_tag, args.vlan_trunks)


class DetachInterfaceArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    mac: str = Field(pattern=MAC)


@operation("vm.detach_interface", DetachInterfaceArgs)
@_definition
def detach_interface(args, conn, domain):
    """Never the VM's last interface."""
    from app.core import vm_devices

    vm_devices.detach_interface(domain, args.mac)


class HostdevArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    device: str = Field(min_length=1, max_length=80)
    confirm: bool = False


@operation("vm.attach_hostdev", HostdevArgs)
@_definition
def attach_hostdev(args, conn, domain):
    """A host device given to the VM whole or not at all, checked against the host's own use of it
    (passthrough.plan_attach); a PCI device only when confirmed."""
    from app.core import vm_devices

    return vm_devices.attach_hostdev(conn, domain, args.device, args.confirm)


class DetachHostdevArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    device: str = Field(min_length=1, max_length=80)


@operation("vm.detach_hostdev", DetachHostdevArgs)
@_definition
def detach_hostdev(args, conn, domain):
    from app.core import vm_devices

    vm_devices.detach_hostdev(conn, domain, args.device)


class CdromArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    iso: str = Field(min_length=1, max_length=256)
    target: Literal["hda", "hdb", "hdc", "hdd"] | None = None


@operation("vm.set_cdrom", CdromArgs)
@_definition
def set_cdrom(args, conn, domain):
    """An ISO of this node's library, named, put in a CD-ROM drive (never a path from the API)."""
    from app.core import vm_devices

    return {"iso": vm_devices.set_cdrom(domain, args.iso, args.target)}


class EjectArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    target: Literal["hda", "hdb", "hdc", "hdd"] | None = None


@operation("vm.eject_cdrom", EjectArgs)
@_definition
def eject_cdrom(args, conn, domain):
    from app.core import vm_devices

    vm_devices.eject_cdrom(domain, args.target)


# --- Part 2c: a VM's firewall, hook script, name and cloud-init medium ---


class VmFirewallArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    config: FirewallConfig


@operation("vm.set_firewall", VmFirewallArgs)
@_definition
def set_firewall(args, conn, domain):
    """The VM's nwfilter, built from checked rules, referenced on every interface (app/core/vm_firewall.py)."""
    from app.core import vm_firewall

    try:
        return {"applied": vm_firewall.apply(conn, domain, args.config.model_dump())}
    except vm_firewall.FirewallError as e:
        raise DaemonError(e.kind, e.message) from e


class HookScriptArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    script: str | None = Field(None, max_length=100)


@operation("vm.set_hookscript", HookScriptArgs)
@_definition
def set_hookscript(args, conn, domain):
    """A script of this node's root library attached to the VM, checked again here (hookscript.check: root's, not
    writable by others), or the VM's script detached (None). It runs as root at the VM's every start and stop."""
    from app.core import hookscript

    try:
        if args.script:
            hookscript.check(args.script)
        hookscript.set_script(domain, args.script)
    except hookscript.HookError as e:
        raise DaemonError("refused", str(e)) from e


class RenameArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    new_name: str = Field(min_length=1, max_length=VM_NAME)


@operation("vm.rename", RenameArgs)
@_definition
def rename(args, conn, domain):
    """The VM renamed with the files named after it, refused while it runs, has snapshots or the name is taken
    (app/core/vm_rename.py). Returns the replication checkpoints dropped, for the API to forget the chain."""
    from app.core import vm_rename
    from app.core.vm_builder import validate_name

    error = validate_name(args.new_name, "VM")
    if error:
        raise DaemonError("refused", error)
    try:
        return {"dropped": vm_rename.rename(conn, domain, args.vm, args.new_name)}
    except vm_rename.RenameError as e:
        raise DaemonError(e.kind, e.message) from e


class CloudInitArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    # The guest's account; not `user`, which privileged.run keeps for the Hyperlite account asking.
    account: str = Field(min_length=1, max_length=32)
    password: str | None = Field(None, max_length=256)
    ssh_keys: list[str] = Field(default_factory=list, max_length=50)
    custom: dict


@operation("vm.cloudinit_write", CloudInitArgs)
@_definition
def cloudinit_write(args, conn, domain):
    """The VM's cloud-init drive written again from checked fields (cloudinit_edit: the account, its keys, the
    customisation), private to root, and put in a running VM's drive at once. Returns {"keys", "custom", "reloaded"}:
    what the API keeps in its database."""
    import logging

    import libvirt

    from app.core import cloudinit_edit

    drive = cloudinit_edit.drive_path(domain, args.vm)
    if drive is None:
        raise DaemonError(
            "in_use", "This VM has no cloud-init drive: it was installed from an ISO image, not made from a cloud image"
        )
    try:
        keys = cloudinit_edit.validate(args.account, args.password, args.ssh_keys)
        macs = cloudinit_edit.interface_macs(domain)
        custom = cloudinit_edit.validate_custom(args.custom, macs)
        cloudinit_edit.write_drive(args.vm, drive, args.account, args.password, keys, custom, macs)
    except cloudinit_edit.CloudInitError as e:
        raise DaemonError("refused", str(e)) from e
    reloaded = False
    try:
        reloaded = cloudinit_edit.reload_media(domain, args.vm)
    except libvirt.libvirtError as e:
        # The file is written: the change applies anyway once the VM is powered off and on again.
        logging.getLogger(__name__).warning("Could not reload the cloud-init drive of %s live: %s", args.vm, e)
    return {"keys": keys, "custom": custom, "reloaded": reloaded}


# --- Part 3a: a VM made on this node (app/core/vm_create.py) ---


class CreateArgs(Args):
    spec: VMCreate  # the API's own model of POST /vms, checked again here
    # Images another node is copying here: they pass the existence check of a check_only call.
    pending: list[Annotated[str, Field(max_length=255)]] = Field(default_factory=list, max_length=4)
    check_only: bool = False

    @field_validator("spec", mode="before")
    @classmethod
    def _as_sent(cls, spec):
        # The route's model ignores a field it does not know, as an HTTP API does; the API sends the daemon its own
        # dump, so an unknown field here, at any depth (a disk's path), is refused.
        return VMCreate.model_validate(spec, extra="forbid") if isinstance(spec, dict) else spec


@operation("vm.create", CreateArgs)
@_libvirt
def create(args, ctx):
    """Every check of the VM's specification run here, then its disks, its cloud-init drive, its DHCP reservation and
    its definition made from it (vm_create.create); check_only makes nothing. One call that waits (a disk's copy)."""
    from app.core import libvirt_utils, vm_create

    with libvirt_utils.local_conn() as conn:
        try:
            return vm_create.create(conn, args.spec, pending=frozenset(args.pending), check_only=args.check_only)
        except vm_create.CreateError as e:
            raise DaemonError(e.kind, e.detail) from e


# --- Part 3b: a VM of this node deleted (app/core/vm_delete.py) ---


class DeleteArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)


@operation("vm.delete", DeleteArgs)
@_libvirt
def delete(args, ctx):
    """The stopped VM, its disks but those another VM uses or reads through or that no storage pool holds, the drives
    Hyperlite generated for it and its DHCP reservations (vm_delete.delete). {"kept": {path: reason}}."""
    from app.core import libvirt_utils, vm_delete

    with libvirt_utils.local_conn() as conn:
        try:
            return {"kept": vm_delete.delete(conn, args.vm)}
        except vm_delete.DeleteError as e:
            raise DaemonError(e.kind, e.detail) from e


# --- Part 3c: clones and templates (app/core/vm_clone.py) ---


class CloneArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    new_name: str = Field(min_length=1, max_length=VM_NAME)
    check_only: bool = False


@operation("vm.clone", CloneArgs)
@_libvirt
def clone(args, ctx):
    """Every disk of the stopped VM copied into the images directory, under a new name, UUID, NVRAM and MAC addresses
    (vm_clone.clone); check_only copies nothing. One call that waits (the copies)."""
    from app.core import libvirt_utils, vm_clone

    with libvirt_utils.local_conn() as conn:
        try:
            return vm_clone.clone(conn, args.vm, args.new_name, check_only=args.check_only)
        except vm_clone.CloneError as e:
            raise DaemonError(e.kind, e.detail) from e


def _templates(fn):
    """vm_templates' refusals and failures keep their kind."""

    @functools.wraps(fn)
    def wrapper(args, ctx):
        from app.core import vm_templates

        try:
            return fn(args, ctx)
        except vm_templates.TemplateError as e:
            raise DaemonError(e.kind, e.detail) from e

    return wrapper


class TemplateMakeArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    template: str = Field(min_length=1, max_length=VM_NAME)
    keep_vm: bool = False
    # Recorded in the template's description, from the API's database: who made it, the source VM's account and system.
    created_by: str = Field(min_length=1, max_length=256)
    ssh_user: str | None = Field(default=None, max_length=64)
    os_label: str | None = Field(default=None, max_length=128)
    check_only: bool = False


@operation("template.make", TemplateMakeArgs)
@_libvirt
@_templates
def template_make(args, ctx):
    """A template from the stopped VM: a copy of its disk (keep_vm), or the VM itself, converted (vm_templates.make)."""
    from app.core import libvirt_utils, vm_templates

    fields = args.model_dump(exclude={"vm", "template", "check_only"})
    vm_templates.check_name(args.template)
    with libvirt_utils.local_conn() as conn:
        return vm_templates.make(conn, args.vm, args.template, check_only=args.check_only, **fields)


class TemplateDeployArgs(Args):
    template: str = Field(min_length=1, max_length=VM_NAME)
    new_name: str = Field(min_length=1, max_length=VM_NAME)
    network: str | None = Field(default=None, min_length=1, max_length=64)
    linked: bool = False
    check_only: bool = False


@operation("template.deploy", TemplateDeployArgs)
@_libvirt
@_templates
def template_deploy(args, ctx):
    """A new VM with a copy of the template's disk, or a linked disk over it (vm_templates.deploy)."""
    from app.core import libvirt_utils, vm_templates

    with libvirt_utils.local_conn() as conn:
        return vm_templates.deploy(
            conn, args.template, args.new_name, args.network, linked=args.linked, check_only=args.check_only
        )


class TemplateRenameArgs(Args):
    template: str = Field(min_length=1, max_length=VM_NAME)
    new_name: str = Field(min_length=1, max_length=VM_NAME)


@operation("template.rename", TemplateRenameArgs)
@_libvirt
@_templates
def template_rename(args, ctx):
    """Refused while a linked clone reads the template's disk, which the rename moves."""
    from app.core import libvirt_utils, vm_templates

    vm_templates.check_name(args.new_name)
    with libvirt_utils.local_conn() as conn:
        vm_templates.rename(conn, args.template, args.new_name)


class TemplateArgs(Args):
    template: str = Field(min_length=1, max_length=VM_NAME)


@operation("template.delete", TemplateArgs)
@_libvirt
@_templates
def template_delete(args, ctx):
    """Refused while a linked clone reads the template's disk."""
    from app.core import libvirt_utils, vm_templates

    with libvirt_utils.local_conn() as conn:
        vm_templates.delete(conn, args.template)


# --- Part 4: snapshots (app/core/vm_snapshots.py) ---


def _snapshots(fn):
    """vm_snapshots' refusals and failures keep their kind."""

    @functools.wraps(fn)
    def wrapper(args, ctx):
        from app.core import vm_snapshots

        try:
            return fn(args, ctx)
        except vm_snapshots.SnapshotError as e:
            raise DaemonError(e.kind, e.detail) from e

    return wrapper


class SnapshotArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)
    name: str = Field(min_length=1, max_length=VM_NAME)


class SnapshotCreateArgs(SnapshotArgs):
    description: str | None = Field(default=None, max_length=4096)
    check_only: bool = False


class SnapshotRestoreArgs(SnapshotArgs):
    check_only: bool = False


class SnapshotListArgs(Args):
    vm: str = Field(min_length=1, max_length=VM_NAME)


@operation("vm.snapshot_create", SnapshotCreateArgs)
@_libvirt
@_snapshots
def snapshot_create(args, ctx):
    """libvirt's internal snapshot, `zfs snapshot` of every zvol, or a thin snapshot of every logical volume, after
    every check; check_only takes none. One call that waits (a running VM's memory is saved)."""
    from app.core import libvirt_utils, vm_snapshots

    with libvirt_utils.local_conn() as conn:
        return vm_snapshots.create(conn, args.vm, args.name, args.description, check_only=args.check_only)


@operation("vm.snapshot_restore", SnapshotRestoreArgs)
@_libvirt
@_snapshots
def snapshot_restore(args, ctx):
    """A ZFS rollback or an LVM restore only on a stopped VM, checked again here."""
    from app.core import libvirt_utils, vm_snapshots

    with libvirt_utils.local_conn() as conn:
        return vm_snapshots.restore(conn, args.vm, args.name, check_only=args.check_only)


@operation("vm.snapshot_delete", SnapshotArgs)
@_libvirt
@_snapshots
def snapshot_delete(args, ctx):
    from app.core import libvirt_utils, vm_snapshots

    with libvirt_utils.local_conn() as conn:
        vm_snapshots.delete(conn, args.vm, args.name)


@operation("vm.lvm_snapshots", SnapshotListArgs)
@_libvirt
@_snapshots
def lvm_snapshots(args, ctx):
    """The LVM-thin snapshots of a VM, for its list: `lvm` reads as root only."""
    from app.core import libvirt_utils, vm_snapshots

    with libvirt_utils.local_conn() as conn:
        return {"snapshots": vm_snapshots.lvm_snapshots(conn, args.vm)}
