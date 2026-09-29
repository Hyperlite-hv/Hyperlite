import logging
import xml.etree.ElementTree as ET

import libvirt
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import cloudinit_edit, cpu_pinning, vm_boot, vm_pending
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import (
    open_conn,
)
from app.core.security import require_vm_privilege
from app.core.vm_limits import validate_vm_resources
from app.core.vm_meta import get_vm_ssh_user, set_vm_os_label, set_vm_ssh_user
from app.routers.vms._shared import _domain_summary, router

logger = logging.getLogger(__name__)


class VMUpdate(BaseModel):
    # DYNAMIC upper bounds (app/core/vm_limits.py): validated in the endpoint, no
    # longer frozen at 2 vCPU / 2 GB.
    vcpu: int | None = Field(default=None, ge=1)
    memory_mb: int | None = Field(default=None, ge=1)
    # Declared guest OS shown in the UI ("Windows Server 2025"). A label only: it can
    # change while the VM runs and does not touch the libvirt definition.
    os_label: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[^\x00-\x1f<>]+$")


@router.patch("/{name}")
def update_vm(name: str, payload: VMUpdate, user: dict = Depends(require_vm_privilege("vm.resize"))):
    if payload.vcpu is None and payload.memory_mb is None and payload.os_label is None:
        raise HTTPException(status_code=422, detail="No change requested (vcpu, memory_mb or os_label required)")
    if payload.vcpu is None and payload.memory_mb is None:
        conn = open_conn()
        try:
            try:
                domain = conn.lookupByName(name)
            except libvirt.libvirtError:
                raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
            set_vm_os_label(name, payload.os_label.strip())
            log_action(user["username"], "update_vm", name, "succes", f"OS label: {payload.os_label.strip()}")
            return _domain_summary(domain)
        finally:
            conn.close()
    limit_errors = validate_vm_resources(payload.vcpu, payload.memory_mb)
    if limit_errors:
        raise HTTPException(status_code=422, detail=limit_errors)

    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "update_vm", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        if domain.isActive():
            log_action(user["username"], "update_vm", name, "echec", "VM active")
            raise HTTPException(status_code=409, detail="Stop the VM before changing its resources")

        try:
            if payload.vcpu is not None:
                # The max must be adjusted before (or at the same time as) the current value,
                # otherwise libvirt refuses a "current" value above the old max.
                domain.setVcpusFlags(payload.vcpu, libvirt.VIR_DOMAIN_AFFECT_CONFIG | libvirt.VIR_DOMAIN_VCPU_MAXIMUM)
                domain.setVcpusFlags(payload.vcpu, libvirt.VIR_DOMAIN_AFFECT_CONFIG)
            if payload.memory_mb is not None:
                kib = payload.memory_mb * 1024
                domain.setMemoryFlags(kib, libvirt.VIR_DOMAIN_AFFECT_CONFIG | libvirt.VIR_DOMAIN_MEM_MAXIMUM)
                domain.setMemoryFlags(kib, libvirt.VIR_DOMAIN_AFFECT_CONFIG)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "update_vm", name, "echec", msg)
            raise HTTPException(status_code=500, detail=f"Resource update error: {msg}") from e

        if payload.os_label is not None:
            set_vm_os_label(name, payload.os_label.strip())
        domain = conn.lookupByName(name)
        result = _domain_summary(domain)
        log_action(user["username"], "update_vm", name, "succes")
        return result
    finally:
        conn.close()


# --- Resource limits and reservations: a simplified equivalent of vSphere
# Resource Pools (reservation/limit/shares), applied through the cgroup
# mechanisms that libvirt exposes directly (schedulerParametersFlags and
# memoryParameters). No manual XML manipulation is needed, unlike the rest of
# this file: these two calls exist as is in the libvirt API.
#
# Deliberate simplifications (to be documented for the user, no over-engineering
# at the level of full vSphere):
# - CPU "shares": RELATIVE priority under real contention for the host core(s)
#   (cgroup cpu.shares, default 1024). It is not an absolute guarantee and has no
#   effect as long as the host is not saturated.
# - CPU "limit": a hard cap in % of one core PER vCPU (cgroup
#   cpu.cfs_quota_us/cfs_period_us through vcpu_quota/vcpu_period). A VM with 2
#   vCPUs and a 50% limit can consume at most the equivalent of 1 full core,
#   never more, even if the host is idle.
# - RAM: NO real "guaranteed reservation" here. libvirt does expose
#   <memtune><min_guarantee> in its XML schema, but that field is only honoured by
#   the Xen hypervisor and is a no-op on QEMU/KVM (checked in the libvirt
#   documentation). The only real RAM reservation on KVM is not to over-allocate
#   the host (check the available RAM before raising memory_mb, already done by
#   update_vm). What IS really applied here: a hard limit separate from the
#   allocated RAM (<memtune><hard_limit>, cgroup memory.limit_in_bytes), useful
#   to cap a qemu process that would drift beyond the RAM allocated to the guest,
#   not to guarantee a minimum.
UNLIMITED_KB = 9007199254740991  # sentinel documented by libvirt for "no limit"
DEFAULT_CPU_SHARES = 1024
CPU_PERIOD_US = 100000  # standard cgroup period (100 ms), consistent with the libvirt default


class ResourceLimits(BaseModel):
    cpu_shares: int = Field(DEFAULT_CPU_SHARES, ge=2, le=262144)
    cpu_limit_pct: int | None = Field(None, ge=1, le=100, description="% of one host core PER vCPU; null = unlimited")
    mem_hard_limit_mb: int | None = Field(
        None, ge=64, description="Hard RAM cap in MB, separate from the allocated RAM; null = unlimited"
    )


def _limits_summary(domain):
    flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
    sched = domain.schedulerParametersFlags(flags)
    mem = domain.memoryParameters(flags)
    nvcpu = domain.info()[3] or 1
    quota = sched.get("vcpu_quota", 0)
    period = sched.get("vcpu_period", 0) or CPU_PERIOD_US
    cpu_limit_pct = None
    if quota and quota > 0:
        cpu_limit_pct = round((quota / period) / nvcpu * 100)
    hard_limit_kb = mem.get("hard_limit", UNLIMITED_KB)
    return {
        # libvirt returns 0 as long as no explicit value was ever set (the effective
        # cgroup default is 1024, not 0).
        "cpu_shares": sched.get("cpu_shares") or DEFAULT_CPU_SHARES,
        "cpu_limit_pct": cpu_limit_pct,
        "mem_hard_limit_mb": None if hard_limit_kb >= UNLIMITED_KB else round(hard_limit_kb / 1024),
    }


@router.get("/{name}/limits")
def get_vm_limits(name: str, user: dict = Depends(require_vm_privilege("vm.resize"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        return _limits_summary(domain)
    finally:
        conn.close()


@router.put("/{name}/limits")
def set_vm_limits(name: str, payload: ResourceLimits, user: dict = Depends(require_vm_privilege("vm.resize"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "set_vm_limits", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        nvcpu = domain.info()[3] or 1
        # libvirt convention: -1 means unlimited
        vcpu_quota = -1 if payload.cpu_limit_pct is None else int(CPU_PERIOD_US * nvcpu * payload.cpu_limit_pct / 100)

        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        if domain.isActive():
            flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE

        try:
            domain.setSchedulerParametersFlags(
                {"cpu_shares": payload.cpu_shares, "vcpu_period": CPU_PERIOD_US, "vcpu_quota": vcpu_quota},
                flags,
            )
            hard_limit_kb = UNLIMITED_KB if payload.mem_hard_limit_mb is None else payload.mem_hard_limit_mb * 1024
            domain.setMemoryParameters({"hard_limit": hard_limit_kb}, flags)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "set_vm_limits", name, "echec", msg)
            raise HTTPException(status_code=500, detail=f"Error applying the limits: {msg}") from e

        log_action(user["username"], "set_vm_limits", name, "succes")
        return _limits_summary(domain)
    finally:
        conn.close()


# --- CPU pinning and NUMA placement (see app/core/cpu_pinning.py).


class CpuPinning(BaseModel):
    # Host CPUs, "4-7" or "0,2,4"; null removes the pinning.
    cpuset: str | None = Field(None, max_length=200)
    strict: bool = False


def _pinning_summary(conn, domain):
    summary = cpu_pinning.read(domain)
    summary["topologie"] = cpu_pinning.host_topology(conn)
    return summary


@router.get("/{name}/cpu-pinning")
def get_vm_cpu_pinning(name: str, user: dict = Depends(require_vm_privilege("vm.resize"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        return _pinning_summary(conn, domain)
    finally:
        conn.close()


@router.put("/{name}/cpu-pinning")
def set_vm_cpu_pinning(name: str, payload: CpuPinning, user: dict = Depends(require_vm_privilege("vm.resize"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "set_vm_cpu_pinning", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        nvcpu = int((root.findtext("vcpu") or "1").strip())
        before = cpu_pinning.read(domain)
        try:
            how = cpu_pinning.plan(cpu_pinning.host_topology(conn), nvcpu, payload.cpuset, payload.strict)
        except cpu_pinning.PinningError as e:
            log_action(user["username"], "set_vm_cpu_pinning", name, "echec", e.message)
            raise HTTPException(status_code=e.status, detail=e.message) from e

        cpu_pinning.apply_to_xml(root, nvcpu, how)
        try:
            conn.defineXML(ET.tostring(root, encoding="unicode"))
            if domain.isActive():
                cpu_pinning.apply_live(domain, conn, domain.info()[3] or nvcpu, how)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "set_vm_cpu_pinning", name, "echec", msg)
            raise HTTPException(status_code=500, detail=f"CPU pinning error: {msg}") from e

        detail = (
            f"CPUs {cpu_pinning.format_cpuset(how['cpus'])}{' strict' if how['strict'] else ''}"
            if how["cpus"]
            else "unpinned"
        )
        log_action(user["username"], "set_vm_cpu_pinning", name, "succes", detail)
        result = _pinning_summary(conn, domain)
        # The CPUs move at once; a new NUMA memory placement only applies when the guest's RAM is allocated again.
        result["redemarrage_requis"] = bool(domain.isActive()) and before["numa_cellule"] != how["numa_cellule"]
        return result
    finally:
        conn.close()


class BootSetting(BaseModel):
    demarrage_auto: bool
    # Position in the node's start sequence; none: after the numbered VMs, by name.
    ordre: int | None = Field(default=None, ge=0, le=vm_boot.MAX_ORDER)
    # Wait after this VM has started, before the next one (a database before its applications).
    delai_s: int = Field(default=0, ge=0, le=vm_boot.MAX_DELAY_S)


def _lookup(conn, name):
    try:
        return conn.lookupByName(name)
    except libvirt.libvirtError:
        raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None


@router.get("/{name}/boot")
def get_vm_boot(name: str, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.view"))):
    conn = open_conn(node)
    try:
        domain = _lookup(conn, name)
        # A libvirt autostart flag set outside Hyperlite (virsh autostart) starts the VM in no order: say so.
        return {**vm_boot.get_setting(name, node), "autostart_libvirt": bool(domain.autostart())}
    finally:
        conn.close()


@router.put("/{name}/boot")
def set_vm_boot(
    name: str, payload: BootSetting, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.options"))
):
    conn = open_conn(node)
    try:
        domain = _lookup(conn, name)
        vm_boot.set_setting(name, payload.demarrage_auto, payload.ordre, payload.delai_s, node)
        if payload.demarrage_auto and domain.autostart():
            # Two mechanisms would start it twice and out of order: Hyperlite's sequence takes over.
            domain.setAutostart(0)
        detail = (
            f"on, order {payload.ordre if payload.ordre is not None else '-'}, delay {payload.delai_s} s"
            if payload.demarrage_auto
            else "off"
        )
        log_action(user["username"], "set_vm_boot", name, "succes", f"Start at boot {detail}")
        return {**vm_boot.get_setting(name, node), "autostart_libvirt": bool(domain.autostart())}
    finally:
        conn.close()


class CloudInitUpdate(BaseModel):
    utilisateur: str = Field(min_length=1, max_length=32)
    # None: the password is left as it is (Hyperlite never stores it, so it cannot be shown or kept otherwise).
    mot_de_passe: str | None = Field(default=None, max_length=256)
    cles_ssh: list[str] = Field(default_factory=list, max_length=50)


def _cloudinit_view(name, domain):
    drive = cloudinit_edit.drive_path(domain, name)
    state = cloudinit_edit.get_state(name) or {"utilisateur": get_vm_ssh_user(name), "cles_ssh": [], "modifie_le": None}
    return {"disponible": drive is not None, "en_marche": bool(domain.isActive()), **state}


@router.get("/{name}/cloud-init")
def get_vm_cloudinit(name: str, user: dict = Depends(require_vm_privilege("vm.view"))):
    """Whether the VM has a cloud-init drive Hyperlite can rewrite, and the account and keys last written there."""
    conn = open_conn()
    try:
        return _cloudinit_view(name, _lookup(conn, name))
    finally:
        conn.close()


@router.put("/{name}/cloud-init")
def set_vm_cloudinit(name: str, payload: CloudInitUpdate, user: dict = Depends(require_vm_privilege("vm.options"))):
    """Rewrite the VM's cloud-init drive; the guest applies it at its next boot."""
    conn = open_conn()
    try:
        domain = _lookup(conn, name)
        drive = cloudinit_edit.drive_path(domain, name)
        if drive is None:
            raise HTTPException(
                status_code=409,
                detail="This VM has no cloud-init drive: it was installed from an ISO image, not made from a cloud image",
            )
        try:
            keys = cloudinit_edit.validate(payload.utilisateur, payload.mot_de_passe, payload.cles_ssh)
            cloudinit_edit.rewrite_drive(name, drive, payload.utilisateur, payload.mot_de_passe, keys)
        except cloudinit_edit.CloudInitError as e:
            log_action(user["username"], "update_vm_cloudinit", name, "echec", str(e))
            raise HTTPException(status_code=422, detail=str(e)) from e
        reloaded = False
        try:
            reloaded = cloudinit_edit.reload_media(domain, name)
        except libvirt.libvirtError as e:
            # The file is written: the change applies anyway once the VM is powered off and on again.
            logger.warning("Could not reload the cloud-init drive of %s live: %s", name, e)
        set_vm_ssh_user(name, payload.utilisateur)
        log_action(
            user["username"],
            "update_vm_cloudinit",
            name,
            "succes",
            f"user {payload.utilisateur}, {len(keys)} key(s), password {'changed' if payload.mot_de_passe else 'kept'}",
        )
        return {**_cloudinit_view(name, domain), "recharge_a_chaud": reloaded}
    finally:
        conn.close()


@router.get("/{name}/pending-changes")
def get_vm_pending_changes(name: str, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.view"))):
    """Settings changed on a running VM that wait for its next start (app/core/vm_pending.py)."""
    conn = open_conn(node)
    try:
        domain = _lookup(conn, name)
        try:
            running = bool(domain.isActive())
            return {"en_marche": running, "changements": vm_pending.pending_changes(domain)}
        except libvirt.libvirtError as e:
            raise HTTPException(status_code=500, detail=describe_exception(e)) from e
    finally:
        conn.close()
