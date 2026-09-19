import asyncio
import json
import logging
import re
import secrets
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from xml.sax.saxutils import escape

import asyncssh
import libvirt
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.core import cluster_compat, zfs_storage
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import (
    domain_disk_paths,
    ensure_vnc_graphics,
    get_vm_uptime_s,
    open_conn,
    pool_type_and_target_path,
    uses_shared_storage,
)
from app.core.network_alloc import allocate_static_ip, generate_mac, release_static_ip
from app.core.permissions import delete_acl_for_vm, remove_vm_from_all_pools
from app.core.security import get_current_user, require_role, require_vm_privilege
from app.core.tasks import create_task, finish_task, update_task_progress
from app.core.unattended_install import build_seed_iso, detect_os_family, extract_casper_kernel
from app.core.vm_builder import (
    IMAGES_DIR,
    build_domain_xml,
    create_cloudinit_iso,
    create_cloudinit_reseed_iso,
    create_disk,
    create_disk_from_import,
    create_zvol_disk,
    get_automation_private_key_path,
    get_or_create_automation_pubkey,
    strip_install_boot_override,
    validate_name,
    validate_username,
)
from app.core.vm_limits import validate_vm_resources
from app.core.vm_meta import (
    clear_provisioning,
    delete_vm_auto_cleanup,
    delete_vm_os_label,
    delete_vm_ssh_user,
    get_provisioning,
    get_vm_auto_cleanup,
    get_vm_os_label,
    get_vm_ssh_user,
    mark_provisioning,
    rename_vm_os_label,
    rename_vm_ssh_user,
    set_vm_auto_cleanup,
    set_vm_os_label,
    set_vm_ssh_user,
    touch_vm_activity,
)
from app.routers.isos import ISOS_DIR
from app.routers.vm_disks import IMPORTED_DISKS_DIR

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vms", tags=["vms"])

TARGET_DEV_RE = re.compile(r"^[a-z]{2,4}[0-9]{0,2}$")

STATE_NAMES = {
    libvirt.VIR_DOMAIN_NOSTATE: "inconnu",
    libvirt.VIR_DOMAIN_RUNNING: "actif",
    libvirt.VIR_DOMAIN_BLOCKED: "bloque",
    libvirt.VIR_DOMAIN_PAUSED: "en_pause",
    libvirt.VIR_DOMAIN_SHUTDOWN: "en_arret",
    libvirt.VIR_DOMAIN_SHUTOFF: "arrete",
    libvirt.VIR_DOMAIN_CRASHED: "plante",
    libvirt.VIR_DOMAIN_PMSUSPENDED: "suspendu",
}


def _get_ip(domain):
    try:
        ifaces = domain.interfaceAddresses(libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_LEASE)
        for iface in ifaces.values():
            for addr in iface.get("addrs", []):
                if addr.get("type") == 0:
                    return addr.get("addr")
    except libvirt.libvirtError:
        logger.debug("Ignored exception in _get_ip()", exc_info=True)
    return None


def _domain_summary(domain):
    state, maxmem, _mem, nvcpu, _cputime = domain.info()
    active = domain.isActive()
    return {
        "nom": domain.name(),
        "id": domain.ID() if active else None,
        "uuid": domain.UUIDString(),
        "etat": STATE_NAMES.get(state, "inconnu"),
        "vcpu": nvcpu,
        "memoire_mo": round(maxmem / 1024, 1),
        "ip": _get_ip(domain) if active else None,
        "utilisateur_ssh": get_vm_ssh_user(domain.name()),
        "uptime_s": get_vm_uptime_s(domain.name()) if active else None,
        "os": get_vm_os_label(domain.name()),
        # Exposed directly here rather than letting the frontend guess from a derived
        # state: the frontend used to deduce "VM on a ZFS pool" from the list of EXISTING
        # snapshots (etat_vm=='disque_seul'), which is wrong for the VERY FIRST snapshot
        # of a VM (an empty list, nothing to deduce), so the qcow2 wording ("memory
        # included automatically") was wrongly shown while it was being created.
        "stockage_zfs": bool(_zvol_disks_of_domain(domain)),
    }


@router.get("")
def list_vms(node: str | None = None, user: dict = Depends(get_current_user)):
    """node: the name of a registered remote node, to list ITS VMs instead of those
    of the local host. Omitted or None = unchanged behaviour (local host).
    The frontend used to have no way to query a remote node here, so the VMs of
    a registered node never appeared in the main tree (only the dedicated
    "Nodes" tab showed them, through /nodes/{name}/summary): a real bug reported
    when testing with a real second physical node."""
    conn = open_conn(node)
    try:
        domains = conn.listAllDomains()
        result = [_domain_summary(d) for d in domains]
        log_action(user["username"], "list_vms", "vms", "succes")
        return result
    finally:
        conn.close()


@router.get("/{name}")
def get_vm(name: str, user: dict = Depends(get_current_user)):
    conn = open_conn()
    try:
        domain = conn.lookupByName(name)
    except libvirt.libvirtError:
        log_action(user["username"], "get_vm", name, "echec", "VM not found")
        conn.close()
        raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
    result = _domain_summary(domain)
    conn.close()
    log_action(user["username"], "get_vm", name, "succes")
    return result


class VMUpdate(BaseModel):
    # DYNAMIC upper bounds (app/core/vm_limits.py): validated in the endpoint, no
    # longer frozen at 2 vCPU / 2 GB.
    vcpu: int | None = Field(default=None, ge=1)
    memory_mb: int | None = Field(default=None, ge=1)


@router.patch("/{name}")
def update_vm(name: str, payload: VMUpdate, user: dict = Depends(require_vm_privilege("vm.resize"))):
    if payload.vcpu is None and payload.memory_mb is None:
        raise HTTPException(status_code=422, detail="No change requested (vcpu or memory_mb required)")
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


class DiskSpec(BaseModel):
    size_gb: int = Field(ge=1)


class VMCreate(BaseModel):
    name: str
    vcpu: int = Field(ge=1)
    memory_mb: int = Field(ge=1)
    disks: list[DiskSpec] = Field(min_length=1)
    network: str = "default"
    # Optional: not applicable when an installation ISO is provided (there is no
    # cloud-init in that case, see below: the user creates their own account during
    # the manual OS installation).
    username: str | None = None
    password: str | None = None
    iso: str | None = None
    # Name of a file already uploaded through POST /vm-disks (see
    # app/routers/vm_disks.py): the VM boots directly from this disk (an OS is already
    # installed on it) instead of the preinstalled Debian 12 image or an installation
    # ISO. Mutually exclusive with `iso`.
    import_disk: str | None = None
    # Storage pool choice: None/absent = the 'default' pool (the historical
    # behaviour, /var/lib/libvirt/images), unchanged by default. Needed so that an
    # HA-protected VM, or one meant for live migration, can really be created on
    # shared storage without moving its disk by hand afterwards: without a way to
    # choose the pool at creation, nobody could realistically use HA protection.
    storage_pool: str | None = None
    # Automatic deletion of inactive VMs: opt-in, None/absent = never enabled
    # (unchanged behaviour by default). The counter only runs while the VM is
    # STOPPED (see touch_vm_activity, called on every start): a VM that runs
    # continuously is never considered "inactive", whatever the threshold.
    auto_cleanup_days: int | None = Field(None, ge=1, le=365)


@router.post("", status_code=201)
def create_vm(payload: VMCreate, user: dict = Depends(require_role("admin"))):
    errors = []
    name_error = validate_name(payload.name)
    if name_error:
        errors.append(name_error)
    errors.extend(validate_vm_resources(payload.vcpu, payload.memory_mb, [d.size_gb for d in payload.disks]))

    iso_path = None
    if payload.iso:
        candidate = ISOS_DIR / payload.iso
        if not candidate.exists():
            errors.append(f"ISO '{payload.iso}' not found")
        else:
            iso_path = candidate

    import_disk_path = None
    if payload.import_disk:
        if payload.iso:
            errors.append("Cannot combine a disk import with an installation ISO")
        candidate = IMPORTED_DISKS_DIR / payload.import_disk
        if not candidate.exists():
            errors.append(f"Imported disk '{payload.import_disk}' not found")
        else:
            import_disk_path = candidate

    # "Installation from an ISO" mode: a blank system disk. If the ISO is recognized
    # (RHEL/kickstart family or Ubuntu/autoinstall, see
    # app/core/unattended_install.py), the installation is automated: a small answers
    # ISO creates the user account and installs the automation SSH key, exactly like
    # the cloud-init of Debian VMs. An unrecognized ISO falls back to the manual
    # installation (the user creates their own account through the VNC console, with
    # no web SSH terminal until access is configured by hand). Without an ISO the
    # behaviour is unchanged: the preinstalled Debian 12 image + cloud-init.
    install_mode = iso_path is not None
    os_family = detect_os_family(payload.iso) if install_mode else None
    automated_install = install_mode and os_family is not None
    # "Disk import" mode: the disk already has its own OS and accounts, so there is
    # neither cloud-init/kickstart nor credentials to ask for at creation (see
    # restore_backup(mode='new') in app/core/backups.py, the same principle already
    # used for backup restore).
    import_mode = import_disk_path is not None
    needs_account = (not install_mode or automated_install) and not import_mode
    if needs_account:
        username_error = validate_username(payload.username or "")
        if username_error:
            errors.append(username_error)
        if len(payload.password or "") < 4:
            errors.append("The password must contain at least 4 characters")

    conn = open_conn()
    task_id = create_task("create_vm", payload.name, node=conn.getHostname(), username=user["username"])
    try:
        try:
            conn.lookupByName(payload.name)
            errors.append(f"A VM named '{payload.name}' already exists")
        except libvirt.libvirtError:
            logger.debug("Ignored exception in create_vm()", exc_info=True)

        try:
            conn.networkLookupByName(payload.network)
        except libvirt.libvirtError:
            errors.append(f"Network '{payload.network}' not found")

        # Resolution of the chosen storage pool: None/absent = the 'default' pool,
        # already guaranteed present and active by ensure_default_pool() at service
        # start-up, so no need to look it up again here. Restricted to the 'dir'/'netfs'
        # types: they are the only pool types created by this project
        # (app/routers/storage.py) that expose a classic FILE path as expected by
        # qemu-img. A 'logical' (LVM) or other pool would need a different volume
        # creation mechanism, out of scope here.
        target_dir = None
        # ZFS pool: not a libvirt pool (see app/core/zfs_storage.py), so it is checked
        # BEFORE any libvirt lookup, which would simply fail with "not found" for a name
        # that only exists on the ZFS side. Single-node for now: only the LOCAL host (the
        # same limit as list_pools/delete_pool in app/routers/storage.py).
        zfs_pool_name = None
        if payload.storage_pool and payload.storage_pool != "default" and zfs_storage.pool_exists(payload.storage_pool):
            zfs_pool_name = payload.storage_pool
        elif payload.storage_pool and payload.storage_pool != "default":
            try:
                pool = conn.storagePoolLookupByName(payload.storage_pool)
            except libvirt.libvirtError:
                errors.append(f"Storage pool '{payload.storage_pool}' not found")
                pool = None
            if pool is not None:
                if not pool.isActive():
                    errors.append(f"Pool de stockage '{payload.storage_pool}' inactif")
                else:
                    pool_type, pool_path = pool_type_and_target_path(pool)
                    if pool_type not in ("dir", "netfs"):
                        errors.append(
                            f"Storage pool '{payload.storage_pool}' of type '{pool_type}' is not supported for VM creation (dir/netfs only)"
                        )
                    elif not pool_path:
                        errors.append(f"Pool de stockage '{payload.storage_pool}' : chemin illisible")
                    else:
                        target_dir = Path(pool_path)

        if errors:
            log_action(user["username"], "create_vm", payload.name, "echec", "; ".join(errors), task_id=task_id)
            raise HTTPException(status_code=422, detail=errors)

        # Fixed IP per VM (see app/core/network_alloc.py): a DHCP reservation on the
        # libvirt network for a MAC known in advance, with no change to the
        # cloud-init/kickstart/autoinstall (still plain DHCP on the guest side).
        # Best-effort: a failure here must not prevent the VM from being created, it only
        # deprives it of a fixed IP (the usual DHCP behaviour as a fallback).
        mac = generate_mac(conn)
        try:
            allocate_static_ip(conn, payload.network, mac)
        except libvirt.libvirtError:
            logger.debug("Ignored exception in create_vm()", exc_info=True)

        try:
            if zfs_pool_name:
                # Raw zvols: each path is a /dev/zvol/... device marked 'block' for
                # build_domain_xml (see create_zvol_disk in vm_builder.py), never a qcow2 file
                # path.
                if import_mode:
                    disk_paths = [
                        (create_zvol_disk(zfs_pool_name, payload.name, 0, 1, import_source=import_disk_path), "block")
                    ] + [
                        (create_zvol_disk(zfs_pool_name, payload.name, i, disk.size_gb), "block")
                        for i, disk in enumerate(payload.disks[1:], start=1)
                    ]
                else:
                    disk_paths = [
                        (
                            create_zvol_disk(
                                zfs_pool_name, payload.name, i, disk.size_gb, blank=(install_mode and i == 0)
                            ),
                            "block",
                        )
                        for i, disk in enumerate(payload.disks)
                    ]
            elif import_mode:
                # Disk 0 = conversion of the imported file (qemu-img detects the source format by
                # itself); any additional disks are always blank as usual.
                disk_paths = [create_disk_from_import(payload.name, import_disk_path, target_dir=target_dir)] + [
                    create_disk(payload.name, disk.size_gb, index=i, target_dir=target_dir)
                    for i, disk in enumerate(payload.disks[1:], start=1)
                ]
            else:
                disk_paths = [
                    create_disk(
                        payload.name, disk.size_gb, index=i, blank=(install_mode and i == 0), target_dir=target_dir
                    )
                    for i, disk in enumerate(payload.disks)
                ]
            cloudinit_path = None
            seed_iso_path = None
            kernel_path = initrd_path = kernel_cmdline = None
            if import_mode:
                # Reuses the "reseed" of cloning: if the imported disk has cloud-init on it (the
                # most frequent case: a Hyperlite export, or a generic cloud image), a new
                # instance-id forces cloud-init to run again at first boot and to regenerate its
                # network for the current MAC. Without it, cloud-init keeps the network
                # configuration of its very first run, which often pins the interface by MAC
                # ADDRESS (seen in testing: /etc/netplan/50-cloud-init.yaml with
                # `match: {macaddress: ...}`). Since this VM necessarily has a new MAC (see
                # generate_mac above), no interface matches anymore and the network never comes
                # up. Harmless if the disk has no cloud-init (the ISO is simply a CD-ROM that is
                # never read).
                cloudinit_path = create_cloudinit_reseed_iso(payload.name)
            elif not install_mode:
                ssh_pubkey = get_or_create_automation_pubkey()
                cloudinit_path = create_cloudinit_iso(
                    payload.name,
                    username=payload.username,
                    password=payload.password,
                    ssh_pubkey=ssh_pubkey,
                    target_dir=target_dir,
                )
            elif automated_install:
                # KNOWN LIMITATION: build_seed_iso() lives in app/core/unattended_install.py, so
                # this answers ISO always stays on the 'default' pool even if `storage_pool`
                # targets another one. It makes no difference for HA/migration (the ISO is only
                # needed for the initial installation and never read again).
                ssh_pubkey = get_or_create_automation_pubkey()
                seed_iso_path = build_seed_iso(
                    os_family,
                    payload.name,
                    username=payload.username,
                    password=payload.password,
                    ssh_pubkey=ssh_pubkey,
                )
                # Ubuntu/autoinstall needs the "autoinstall" keyword on the kernel command line
                # to skip Subiquity's single manual confirmation ("Continue with autoinstall?").
                # It is not needed for kickstart (RHEL), which never had this problem (Anaconda
                # detects OEMDRV without confirmation). It is removed once the installation is
                # finished, see get_vm_provisioning below (otherwise the VM would reboot in a
                # loop on the live installer instead of the installed system).
                if os_family == "autoinstall":
                    kernel_path, initrd_path = extract_casper_kernel(iso_path)
                    kernel_cmdline = "autoinstall ---"
        except subprocess.CalledProcessError as e:
            msg = f"Error while preparing the disk/cloud-init: {e.stderr or e}"
            log_action(user["username"], "create_vm", payload.name, "echec", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=msg) from e
        except zfs_storage.ZfsError as e:
            log_action(user["username"], "create_vm", payload.name, "echec", e.message, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"ZFS error while preparing the disk: {e.message}") from e
        except ValueError as e:
            log_action(user["username"], "create_vm", payload.name, "echec", str(e), task_id=task_id)
            raise HTTPException(status_code=422, detail=str(e)) from e

        xml = build_domain_xml(
            payload.name,
            payload.vcpu,
            payload.memory_mb,
            disk_paths,
            cloudinit_path,
            payload.network,
            iso_path=iso_path,
            seed_iso_path=seed_iso_path,
            mac=mac,
            kernel_path=kernel_path,
            initrd_path=initrd_path,
            kernel_cmdline=kernel_cmdline,
        )
        domain = conn.defineXML(xml)
        if needs_account:
            set_vm_ssh_user(payload.name, payload.username)
        if automated_install:
            # "Without any manual intervention": create_vm used to only DEFINE the domain,
            # and you had to click "Start" by hand for the kickstart/autoinstall installation
            # to actually begin. It now starts the VM automatically here. A dedicated task
            # ("auto_install") is created for this complete install + SSH-check cycle,
            # distinct from the "create_vm" task (which only covers the domain definition,
            # already finished when this block runs). It is closed by get_vm_provisioning
            # below, on success, failure or timeout.
            install_task_id = create_task(
                "auto_install", payload.name, node=conn.getHostname(), username=user["username"]
            )
            mark_provisioning(payload.name, os_family, task_id=install_task_id)
            try:
                domain.create()
            except libvirt.libvirtError as e:
                msg = describe_exception(e)
                finish_task(install_task_id, "echec", f"Automatic start impossible: {msg}")
                clear_provisioning(payload.name)
                log_action(user["username"], "auto_install", payload.name, "echec", msg)
                # This does not make create_vm fail: the domain is defined, and the admin can
                # start or diagnose it by hand. A failed automatic start must not make the VM
                # invisible or lost.
                # "Declared" OS label (see vm_meta.py::set_vm_os_label): deduced from the name of
                # the mounted ISO, or "Debian 12" for the default cloud-init path (no ISO,
                # preinstalled image). It is not a really "detected" OS (no qemu-guest-agent is
                # installed in guest VMs), but it is reliable: Hyperlite itself asked for that OS.
        os_label = (
            f"Imported ({Path(payload.import_disk).stem})"
            if import_mode
            else Path(payload.iso).stem
            if install_mode
            else "Debian 12"
        )
        set_vm_os_label(payload.name, os_label)
        if payload.auto_cleanup_days:
            set_vm_auto_cleanup(payload.name, payload.auto_cleanup_days)
        log_action(user["username"], "create_vm", payload.name, "succes", task_id=task_id)
        return _domain_summary(domain)
    finally:
        conn.close()


@router.post("/{name}/start")
def start_vm(name: str, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.power"))):
    conn = open_conn(node)
    task_id = create_task("start_vm", name, node=conn.getHostname(), username=user["username"])
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "start_vm", name, "echec", "VM not found", task_id=task_id)
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        if domain.isActive():
            log_action(user["username"], "start_vm", name, "echec", "VM already running", task_id=task_id)
            raise HTTPException(status_code=409, detail=f"VM '{name}' is already running")
        try:
            domain.create()
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "start_vm", name, "echec", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Unable to start the VM: {msg}") from e
        touch_vm_activity(name)  # resets the inactivity counter
        log_action(user["username"], "start_vm", name, "succes", task_id=task_id)
        return _domain_summary(domain)
    finally:
        conn.close()


@router.post("/{name}/stop")
def stop_vm(
    name: str, force: bool = False, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.power"))
):
    conn = open_conn(node)
    action_name = "force_stop_vm" if force else "stop_vm"
    task_id = create_task(action_name, name, node=conn.getHostname(), username=user["username"])
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "stop_vm", name, "echec", "VM not found", task_id=task_id)
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        if not domain.isActive():
            log_action(user["username"], "stop_vm", name, "echec", "VM already stopped", task_id=task_id)
            raise HTTPException(status_code=409, detail=f"VM '{name}' is already stopped")
        try:
            if force:
                domain.destroy()
            else:
                domain.shutdown()
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], action_name, name, "echec", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Unable to stop the VM: {msg}") from e
        log_action(user["username"], action_name, name, "succes", task_id=task_id)
        return _domain_summary(domain)
    finally:
        conn.close()


@router.post("/{name}/restart")
def restart_vm(
    name: str, force: bool = False, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.power"))
):
    conn = open_conn(node)
    task_id = create_task("restart_vm", name, node=conn.getHostname(), username=user["username"])
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "restart_vm", name, "echec", "VM not found", task_id=task_id)
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        if not domain.isActive():
            log_action(user["username"], "restart_vm", name, "echec", "VM stopped", task_id=task_id)
            raise HTTPException(status_code=409, detail=f"VM '{name}' is stopped, start it first")
        try:
            if force:
                domain.destroy()
                domain.create()
            else:
                domain.reboot()
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "restart_vm", name, "echec", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Unable to restart the VM: {msg}") from e
        log_action(user["username"], "restart_vm", name, "succes", task_id=task_id)
        return _domain_summary(domain)
    finally:
        conn.close()


def _perform_vm_deletion(conn, domain, name, node=None):
    """The real deletion sequence (disks + reserved IP + metadata), shared by
    DELETE /vms/{name} (user confirmation) and the automatic cleanup of inactive
    VMs (app/core/vm_cleanup.py): no divergence is possible between the two
    paths. The caller must have ALREADY checked that the VM is inactive; this
    function does not revalidate it. It may raise libvirt.libvirtError
    (undefine fails), which the caller must translate.

    `node`: None = local VM (the historical behaviour, files removed through
    the LOCAL Path.unlink()). On a REMOTE node, `conn` is a qemu+ssh://
    connection but disk paths remain paths on the REMOTE FILESYSTEM: a local
    unlink() could delete the wrong file (or nothing at all) on the local host.
    Remote deletion therefore goes through SSH (the same cluster key as the rest,
    see _copy_file_to_node) rather than unlink()."""
    # Captured BEFORE the undefine (no longer queryable afterwards) for ALL the disks
    # and ALL the interfaces, not only the first ones: a multi-disk/multi-NIC VM must
    # not leave an orphan qcow2 file or a phantom DHCP reservation for its disks and
    # interfaces beyond the first. (This was found when testing multi-disk cloning:
    # the secondary disk of a deleted VM stayed on the host disk and caused a name
    # conflict at the next clone.)
    # ISOs generated by Hyperlite itself for THIS VM (cloud-init/kickstart/autoinstall)
    # are recognized by their file NAME, never by their directory: they can live on ANY
    # pool (see create_vm), not necessarily IMAGES_DIR. The REAL path is captured from
    # the domain XML, as already done for the disks; rebuilding it by hand through
    # IMAGES_DIR would leave the cloud-init ISO orphaned on any non-default pool.
    OWN_GENERATED_ISO_NAMES = {f"{name}-cloudinit.iso", f"{name}-oemdrv.iso", f"{name}-autoinstall.iso"}

    disk_paths_to_remove = []
    # BLOCK disks (ZFS zvols): source='dev', not 'file'. They are handled separately
    # (deletion through `zfs destroy`, not Path.unlink()), see below. They are always
    # local for now (single-node ZFS management, the same limit as
    # create_vm/storage.py): never expected in the remote deletion path (node= towards
    # another node).
    zvol_paths_to_remove = []
    ifaces_to_release = []
    try:
        root = ET.fromstring(domain.XMLDesc())
        for disk_el in root.findall(".//devices/disk"):
            source_el = disk_el.find("source")
            if source_el is None:
                continue
            if disk_el.get("type") == "block":
                source_dev = source_el.get("dev")
                if source_dev and disk_el.get("device") == "disk":
                    zvol_paths_to_remove.append(source_dev)
                continue
            source_file = source_el.get("file")
            if not source_file:
                continue
            if disk_el.get("device") == "disk" or (
                disk_el.get("device") == "cdrom" and Path(source_file).name in OWN_GENERATED_ISO_NAMES
            ):
                disk_paths_to_remove.append(Path(source_file))
        for iface in root.findall(".//interface[@type='network']"):
            mac_el, source_el = iface.find("mac"), iface.find("source")
            if mac_el is not None and source_el is not None and source_el.get("network"):
                ifaces_to_release.append((source_el.get("network"), mac_el.get("address")))
    except (libvirt.libvirtError, ET.ParseError):
        logger.debug("Ignored exception in _perform_vm_deletion()", exc_info=True)

    # VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA: without this flag, undefine() simply
    # fails as soon as one or more snapshots remain ("cannot delete inactive domain
    # with N snapshots"), even partially deleted ones. Harmless here: the qcow2 file
    # that held the internal snapshots is deleted right afterwards anyway (unlink
    # below), and the VM itself is already irrevocably confirmed as deleted.
    domain.undefineFlags(libvirt.VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA)

    for iface_network, iface_mac in ifaces_to_release:
        try:
            release_static_ip(conn, iface_network, iface_mac)
        except libvirt.libvirtError:
            logger.debug("Ignored exception in _perform_vm_deletion()", exc_info=True)

    if node:
        from app.core.cluster import get_node, node_ssh_options

        remote_node = get_node(node)
        if remote_node:
            ssh_opts = node_ssh_options()
            ssh_target = f"{remote_node['ssh_user']}@{remote_node['hostname']}"
            for disk_path in disk_paths_to_remove:
                subprocess.run(
                    ["ssh", *ssh_opts, "-p", str(remote_node["ssh_port"]), ssh_target, "rm", "-f", str(disk_path)],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
    else:
        for disk_path in disk_paths_to_remove:
            disk_path.unlink(missing_ok=True)
        for zvol_path in zvol_paths_to_remove:
            # '/dev/zvol/<pool>/<name>': the pool is the 3rd segment, the name is the rest (a
            # zvol name never contains a '/' itself, see validate_zfs_name). Best-effort: a
            # VM already partially cleaned up (a zvol removed by hand outside Hyperlite) must
            # not make the whole deletion fail.
            parts = zvol_path.strip("/").split("/")
            if len(parts) >= 4 and parts[0] == "dev" and parts[1] == "zvol":
                try:
                    zfs_storage.delete_zvol(parts[2], "/".join(parts[3:]))
                except zfs_storage.ZfsError:
                    logger.debug("Ignored exception in _perform_vm_deletion()", exc_info=True)
    delete_vm_ssh_user(name)
    delete_vm_os_label(name)
    clear_provisioning(name)
    delete_acl_for_vm(name)
    remove_vm_from_all_pools(name)
    delete_vm_auto_cleanup(name)


@router.delete("/{name}")
def delete_vm(name: str, confirm: bool = False, node: str | None = None, user: dict = Depends(require_role("admin"))):
    conn = open_conn(node)
    task_id = create_task("delete_vm", name, node=conn.getHostname(), username=user["username"])
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "delete_vm", name, "echec", "VM not found", task_id=task_id)
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        if domain.isActive():
            log_action(user["username"], "delete_vm", name, "echec", "VM running, it must be stopped", task_id=task_id)
            raise HTTPException(status_code=409, detail=f"VM '{name}' is running. Stop it before deleting it")
        if not confirm:
            log_action(user["username"], "delete_vm", name, "echec", "Confirmation manquante", task_id=task_id)
            raise HTTPException(
                status_code=400, detail="Irreversible action: add ?confirm=true to confirm the deletion"
            )

        try:
            _perform_vm_deletion(conn, domain, name, node=node)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "delete_vm", name, "echec", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Unable to delete the VM: {msg}") from e

        log_action(user["username"], "delete_vm", name, "succes", task_id=task_id)
        return {"message": f"VM '{name}' deleted"}
    finally:
        conn.close()


# --- Automatic deletion of inactive VMs: post-creation management
# (enable/reconfigure/disable). The creation itself goes through
# VMCreate.auto_cleanup_days above. The same privilege level as the per-VM
# firewall (vm.hardware): this is a VM setting, not a cluster-wide action like
# migration.


class AutoCleanupConfig(BaseModel):
    inactive_days: int = Field(ge=1, le=365)


@router.get("/{name}/auto-cleanup")
def get_vm_auto_cleanup_route(name: str, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    conn = open_conn()
    try:
        try:
            conn.lookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
    finally:
        conn.close()
    config = get_vm_auto_cleanup(name)
    if not config:
        return {"active": False}
    return {"active": True, **config}


@router.put("/{name}/auto-cleanup")
def set_vm_auto_cleanup_route(
    name: str, payload: AutoCleanupConfig, user: dict = Depends(require_vm_privilege("vm.hardware"))
):
    conn = open_conn()
    try:
        try:
            conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "set_auto_cleanup", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
    finally:
        conn.close()
    set_vm_auto_cleanup(name, payload.inactive_days)
    log_action(user["username"], "set_auto_cleanup", name, "succes", f"seuil {payload.inactive_days} jour(s)")
    return {"message": f"Automatic cleanup enabled ({payload.inactive_days} day(s) of inactivity)"}


@router.delete("/{name}/auto-cleanup")
def disable_vm_auto_cleanup_route(name: str, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    delete_vm_auto_cleanup(name)
    log_action(user["username"], "disable_auto_cleanup", name, "succes")
    return {"message": "Automatic cleanup disabled"}


class DiskAttach(BaseModel):
    volume_name: str
    pool: str = "default"
    target_dev: str = "sdb"


# libvirt bus to use depending on the target_dev prefix, to stay consistent with
# the virtio-scsi controller (sd*) set up by build_domain_xml on all VMs.
DEV_BUS_PREFIXES = {"sd": "scsi", "vd": "virtio", "hd": "ide"}


@router.post("/{name}/disks", status_code=201)
def attach_disk(name: str, payload: DiskAttach, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    if not TARGET_DEV_RE.match(payload.target_dev):
        log_action(user["username"], "attach_disk", name, "echec", "Invalid target_dev")
        raise HTTPException(status_code=422, detail="Invalid target_dev (expected e.g. vda, vdb, sdb)")
    bus = DEV_BUS_PREFIXES.get(payload.target_dev[:2], "virtio")
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "attach_disk", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        try:
            pool = conn.storagePoolLookupByName(payload.pool)
            vol = pool.storageVolLookupByName(payload.volume_name)
        except libvirt.libvirtError:
            log_action(user["username"], "attach_disk", name, "echec", "Volume not found")
            raise HTTPException(
                status_code=404, detail=f"Volume '{payload.volume_name}' not found in pool '{payload.pool}'"
            ) from None

        disk_xml = f"""
        <disk type='file' device='disk'>
          <driver name='qemu' type='qcow2'/>
          <source file='{vol.path()}'/>
          <target dev='{payload.target_dev}' bus='{bus}'/>
        </disk>
        """
        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        if domain.isActive():
            flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
        try:
            domain.attachDeviceFlags(disk_xml, flags)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "attach_disk", name, "echec", msg)
            raise HTTPException(status_code=500, detail=f"Disk attach error: {msg}") from e

        log_action(user["username"], "attach_disk", name, "succes")
        return {"message": f"Volume '{payload.volume_name}' attached to '{name}' as {payload.target_dev}"}
    finally:
        conn.close()


@router.delete("/{name}/disks/{target_dev}")
def detach_disk(name: str, target_dev: str, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    if not TARGET_DEV_RE.match(target_dev):
        log_action(user["username"], "detach_disk", name, "echec", "Invalid target_dev")
        raise HTTPException(status_code=422, detail="Invalid target_dev (expected e.g. vda, vdb, sdb)")
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "detach_disk", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        xml_desc = domain.XMLDesc(0)
        root = ET.fromstring(xml_desc)
        disk_elem = None
        for disk in root.findall(".//devices/disk"):
            target = disk.find("target")
            if target is not None and target.get("dev") == target_dev:
                disk_elem = disk
                break
        if disk_elem is None:
            log_action(user["username"], "detach_disk", name, "echec", f"Disk {target_dev} not found")
            raise HTTPException(status_code=404, detail=f"Disk '{target_dev}' not found on VM '{name}'")

        disk_xml = ET.tostring(disk_elem, encoding="unicode")
        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        if domain.isActive():
            flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
        try:
            domain.detachDeviceFlags(disk_xml, flags)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "detach_disk", name, "echec", msg)
            raise HTTPException(status_code=500, detail=f"Detach error: {msg}") from e

        log_action(user["username"], "detach_disk", name, "succes")
        return {"message": f"Disk '{target_dev}' detached from '{name}'"}
    finally:
        conn.close()


def _get_interfaces(domain):
    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)
    result = []
    for iface in root.findall(".//devices/interface"):
        mac_elem = iface.find("mac")
        source_elem = iface.find("source")
        result.append(
            {
                "mac": mac_elem.get("address") if mac_elem is not None else None,
                "reseau": source_elem.get("network") if source_elem is not None else None,
                "type_source": iface.get("type"),
            }
        )
    return result


@router.get("/{name}/disks")
def get_vm_disks(name: str, user: dict = Depends(get_current_user)):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "get_vm_disks", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        xml_desc = domain.XMLDesc(0)
        root = ET.fromstring(xml_desc)
        disks = []
        for disk in root.findall(".//devices/disk"):
            target = disk.find("target")
            source = disk.find("source")
            disks.append(
                {
                    "cible": target.get("dev") if target is not None else None,
                    "bus": target.get("bus") if target is not None else None,
                    "type": disk.get("device"),
                    "source": (source.get("file") if source is not None else None),
                }
            )
        log_action(user["username"], "get_vm_disks", name, "succes")
        return disks
    finally:
        conn.close()


@router.get("/{name}/network")
def get_vm_network(name: str, user: dict = Depends(get_current_user)):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "get_vm_network", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        interfaces = _get_interfaces(domain)
        ip = _get_ip(domain) if domain.isActive() else None
        log_action(user["username"], "get_vm_network", name, "succes")
        return {"interfaces": interfaces, "ip": ip}
    finally:
        conn.close()


class NetworkUpdate(BaseModel):
    network: str
    vlan_tag: int | None = Field(
        None,
        ge=1,
        le=4094,
        description="802.1Q tag: only effective if the underlying network/bridge handles trunking (Open vSwitch); silently ignored on a standard Linux bridge",
    )


@router.put("/{name}/network")
def set_vm_network(name: str, payload: NetworkUpdate, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "set_vm_network", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        try:
            conn.networkLookupByName(payload.network)
        except libvirt.libvirtError:
            log_action(user["username"], "set_vm_network", name, "echec", "Network not found")
            raise HTTPException(status_code=404, detail=f"Network '{payload.network}' not found") from None

        xml_desc = domain.XMLDesc(0)
        root = ET.fromstring(xml_desc)
        iface = root.find(".//devices/interface")
        if iface is None:
            log_action(user["username"], "set_vm_network", name, "echec", "No interface")
            raise HTTPException(status_code=404, detail="No network interface found on this VM")

        source = iface.find("source")
        if source is None:
            source = ET.SubElement(iface, "source")
        for k in list(source.attrib):
            del source.attrib[k]
        source.set("network", payload.network)

        vlan_el = iface.find("vlan")
        if vlan_el is not None:
            iface.remove(vlan_el)
        if payload.vlan_tag is not None:
            vlan_el = ET.SubElement(iface, "vlan")
            ET.SubElement(vlan_el, "tag", {"id": str(payload.vlan_tag)})

        iface_xml = ET.tostring(iface, encoding="unicode")
        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        if domain.isActive():
            flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
        try:
            domain.updateDeviceFlags(iface_xml, flags)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "set_vm_network", name, "echec", msg)
            raise HTTPException(status_code=500, detail=f"Network update error: {msg}") from e

        log_action(user["username"], "set_vm_network", name, "succes")
        return {"message": f"VM '{name}' attached to network '{payload.network}'"}
    finally:
        conn.close()


MAC_RE = re.compile(r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")


class InterfaceAttach(BaseModel):
    network: str
    vlan_tag: int | None = Field(None, ge=1, le=4094)


@router.post("/{name}/interfaces", status_code=201)
def attach_interface(name: str, payload: InterfaceAttach, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "attach_interface", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        try:
            conn.networkLookupByName(payload.network)
        except libvirt.libvirtError:
            log_action(user["username"], "attach_interface", name, "echec", "Network not found")
            raise HTTPException(status_code=404, detail=f"Network '{payload.network}' not found") from None

        vlan_xml = f"<vlan><tag id='{payload.vlan_tag}'/></vlan>" if payload.vlan_tag is not None else ""
        iface_xml = f"""
        <interface type='network'>
          <source network='{payload.network}'/>
          {vlan_xml}
          <model type='virtio'/>
        </interface>
        """
        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        if domain.isActive():
            flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
        try:
            domain.attachDeviceFlags(iface_xml, flags)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "attach_interface", name, "echec", msg)
            raise HTTPException(status_code=500, detail=f"Interface attach error: {msg}") from e

        log_action(user["username"], "attach_interface", name, "succes")
        return {"message": f"Interface added on network '{payload.network}' for '{name}'"}
    finally:
        conn.close()


@router.delete("/{name}/interfaces/{mac}")
def detach_interface(name: str, mac: str, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    if not MAC_RE.match(mac):
        log_action(user["username"], "detach_interface", name, "echec", "Invalid MAC")
        raise HTTPException(status_code=422, detail="Invalid MAC address")
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "detach_interface", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        xml_desc = domain.XMLDesc(0)
        root = ET.fromstring(xml_desc)
        interfaces = root.findall(".//devices/interface")
        if len(interfaces) <= 1:
            log_action(user["username"], "detach_interface", name, "echec", "Last interface")
            raise HTTPException(status_code=422, detail="Cannot detach the last network interface of a VM")

        iface_elem = None
        for iface in interfaces:
            mac_elem = iface.find("mac")
            if mac_elem is not None and mac_elem.get("address", "").lower() == mac.lower():
                iface_elem = iface
                break
        if iface_elem is None:
            log_action(user["username"], "detach_interface", name, "echec", f"Interface {mac} not found")
            raise HTTPException(status_code=404, detail=f"Interface '{mac}' not found on VM '{name}'")

        iface_xml = ET.tostring(iface_elem, encoding="unicode")
        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        if domain.isActive():
            flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
        try:
            domain.detachDeviceFlags(iface_xml, flags)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "detach_interface", name, "echec", msg)
            raise HTTPException(status_code=500, detail=f"Detach error: {msg}") from e

        log_action(user["username"], "detach_interface", name, "succes")
        return {"message": f"Interface '{mac}' detached from '{name}'"}
    finally:
        conn.close()


# --- Per-VM firewall ---
# Implemented through libvirt's nwfilter subsystem (VIR_NWFilter*) rather than
# hand-generated nftables/iptables rules: nwfilter is already libvirt's native
# mechanism for this, applied automatically by the QEMU driver at every VM
# (re)start with no external script to maintain. One filter per VM
# ("hyperlite-vm-<name>"), referenced by a <filterref> on each interface of the VM.

_FIREWALL_PROTOCOLS = {"tcp", "udp", "icmp", "all"}
_FIREWALL_ACTIONS = {"accept", "drop"}
_FIREWALL_DIRECTIONS = {"in", "out", "inout"}


class FirewallRule(BaseModel):
    action: str
    direction: str
    protocol: str
    port: int | None = Field(None, ge=1, le=65535)


class FirewallConfig(BaseModel):
    default_policy: str = "accept"
    rules: list[FirewallRule] = []


def _firewall_filter_name(vm_name):
    return f"hyperlite-vm-{vm_name}"


def _build_nwfilter_xml(vm_name, config):
    rules_xml = ""
    priority = 300
    for rule in config.rules:
        port_attr = f" dstportstart='{rule.port}'" if rule.port and rule.protocol in ("tcp", "udp") else ""
        rules_xml += f"<rule action='{rule.action}' direction='{rule.direction}' priority='{priority}'><{rule.protocol}{port_attr}/></rule>"
        priority += 1
    default_action = "accept" if config.default_policy == "accept" else "drop"
    rules_xml += f"<rule action='{default_action}' direction='inout' priority='999'><all/></rule>"
    return f"<filter name='{_firewall_filter_name(vm_name)}' chain='root'>{rules_xml}</filter>"


def _parse_nwfilter_xml(xml_desc):
    root = ET.fromstring(xml_desc)
    rules = []
    default_policy = "accept"
    for rule_el in root.findall("rule"):
        proto_el = None
        for candidate in ("tcp", "udp", "icmp", "all"):
            proto_el = rule_el.find(candidate)
            if proto_el is not None:
                break
        if proto_el is None:
            continue
        protocol = proto_el.tag
        port = proto_el.get("dstportstart")
        direction = rule_el.get("direction", "inout")
        action = rule_el.get("action", "accept")
        if protocol == "all" and direction == "inout" and int(rule_el.get("priority", 0)) >= 999:
            default_policy = action  # the catch-all rule added by _build_nwfilter_xml
            continue
        rules.append(
            {"action": action, "direction": direction, "protocol": protocol, "port": int(port) if port else None}
        )
    return {"default_policy": default_policy, "rules": rules}


@router.get("/{name}/firewall")
def get_vm_firewall(name: str, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    conn = open_conn()
    try:
        try:
            conn.lookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        try:
            nwf = conn.nwfilterLookupByName(_firewall_filter_name(name))
            return _parse_nwfilter_xml(nwf.XMLDesc(0))
        except libvirt.libvirtError:
            return {
                "default_policy": "accept",
                "rules": [],
            }  # no rule defined: everything is allowed, the default behaviour
    finally:
        conn.close()


@router.put("/{name}/firewall")
def set_vm_firewall(name: str, payload: FirewallConfig, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    if payload.default_policy not in _FIREWALL_ACTIONS:
        raise HTTPException(status_code=422, detail="default_policy must be 'accept' or 'drop'")
    for rule in payload.rules:
        if (
            rule.action not in _FIREWALL_ACTIONS
            or rule.direction not in _FIREWALL_DIRECTIONS
            or rule.protocol not in _FIREWALL_PROTOCOLS
        ):
            raise HTTPException(status_code=422, detail=f"Invalid rule: {rule}")

    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "set_vm_firewall", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        filter_name = _firewall_filter_name(name)
        try:
            conn.nwfilterDefineXML(_build_nwfilter_xml(name, payload))
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "set_vm_firewall", name, "echec", msg)
            raise HTTPException(status_code=500, detail=f"Firewall definition error: {msg}") from e

        # Reference the filter on EVERY interface of the VM (not only the first one):
        # otherwise a multi-NIC VM would silently leave an interface unfiltered, which is
        # exactly the kind of bug fixed for disks/interfaces when cloning.
        root = ET.fromstring(domain.XMLDesc(0))
        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        if domain.isActive():
            flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
        applied = 0
        for iface in root.findall(".//devices/interface"):
            existing_ref = iface.find("filterref")
            if existing_ref is not None:
                iface.remove(existing_ref)
            ET.SubElement(iface, "filterref", {"filter": filter_name})
            try:
                domain.updateDeviceFlags(ET.tostring(iface, encoding="unicode"), flags)
                applied += 1
            except libvirt.libvirtError as e:
                msg = describe_exception(e)
                log_action(user["username"], "set_vm_firewall", name, "echec", msg)
                raise HTTPException(
                    status_code=500, detail=f"Filter created but not applied to the interface: {msg}"
                ) from e

        log_action(
            user["username"], "set_vm_firewall", name, "succes", f"{len(payload.rules)} rule(s), {applied} interface(s)"
        )
        return {"message": f"Firewall applied to {applied} interface(s)", **payload.model_dump()}
    finally:
        conn.close()


# --- Snapshots ---
#
# A snapshot captures the state of a VM (disk, and memory if it is running) at an
# instant T, stored INSIDE the qcow2 file itself (an "internal" snapshot): it is
# fast to create and restore but it is NOT an independent backup (if the qcow2
# disk is lost or corrupted, so are all its snapshots). A real backup is a
# complete, self-contained copy of the data stored elsewhere, which survives the
# loss of the source disk: slower and heavier, but the only protection against a
# storage failure. A snapshot is for going back quickly (before a risky update,
# for example); a backup is for disaster recovery.
#
# Design notes:
# - The former code (flags=0, minimal XML, synchronous) created and restored
#   internal snapshots correctly. The real reproducible bug was that delete_vm()
#   called domain.undefine() WITHOUT a flag, which simply fails as soon as one or
#   more snapshots still exist ("cannot delete inactive domain with N
#   snapshots"). This is fixed above (VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA):
#   a VM on which a snapshot had been taken became impossible to delete from the
#   interface, which very probably explains the "snapshots do not work" feeling.
# - Deliberately rejected option: a "memoryless" snapshot on a RUNNING VM is
#   really an EXTERNAL snapshot on the libvirt side (a new overlay file, a chain of
#   backing files). It works at creation, but `revertToSnapshot()` returns "revert
#   to external snapshot not supported yet" with this QEMU/libvirt driver, so it
#   can NOT be restored. Offering an "include memory" checkbox that would produce
#   unrecoverable snapshots would have been a new trap, not a fix. The
#   memory/no-memory choice is therefore NOT exposed: memory is included
#   automatically if the VM is running (the only mode that restores reliably), and
#   the disk only if it is stopped (nothing else to capture).
# - Real duration: creating or restoring a snapshot with memory can take several
#   seconds (serializing the whole VM RAM into the qcow2). libvirt exposes NO
#   usable progress statistic for this operation (domain.jobStats() returns
#   {'type': VIR_DOMAIN_JOB_NONE} from start to end), so displaying a percentage
#   would be made up. Create and restore therefore run in the background (a
#   dedicated thread + a separate libvirt connection) while the HTTP endpoint
#   immediately returns a task_id (see app.core.tasks): the frontend shows an
#   indeterminate progress bar and the real elapsed time by following
#   GET /tasks/{id}, instead of blocking the request or showing a fake percentage.


def _zvol_disks_of_domain(domain):
    """List of (pool, zvol_name) for all the BLOCK disks (ZFS zvols) of a domain. A
    VM created on a ZFS pool (see create_vm) has ALL its disks as zvols in the
    SAME pool, never mixed with qcow2 files in this project. Used to route
    snapshots to the native ZFS mechanism rather than the qcow2 internal
    snapshot (which only applies to FILE disks)."""
    root = ET.fromstring(domain.XMLDesc())
    specs = []
    for disk_el in root.findall(".//devices/disk"):
        if disk_el.get("type") != "block" or disk_el.get("device") != "disk":
            continue
        source_el = disk_el.find("source")
        dev = source_el.get("dev") if source_el is not None else None
        if not dev:
            continue
        parts = dev.strip("/").split("/")
        if len(parts) >= 4 and parts[0] == "dev" and parts[1] == "zvol":
            specs.append((parts[2], "/".join(parts[3:])))
    return specs


def _snapshot_summary(snap):
    xml_desc = snap.getXMLDesc()
    root = ET.fromstring(xml_desc)
    desc_elem = root.find("description")
    creation_elem = root.find("creationTime")
    state_elem = root.find("state")
    try:
        parent_nom = snap.getParent().getName()
    except libvirt.libvirtError:
        parent_nom = None
    return {
        "nom": snap.getName(),
        "description": desc_elem.text if desc_elem is not None else None,
        "date_creation": creation_elem.text if creation_elem is not None else None,
        "etat_vm": state_elem.text if state_elem is not None else None,
        "actuel": snap.isCurrent() == 1,
        "parent": parent_nom,
    }


@router.get("/{name}/snapshots")
def list_snapshots(name: str, user: dict = Depends(get_current_user)):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "list_snapshots", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        zvol_specs = _zvol_disks_of_domain(domain)
        if zvol_specs:
            # VM on a ZFS pool: one "logical" snapshot per NAME, merged across all the VM's
            # zvols. The list of the first zvol is taken as the reference (all the zvols of a
            # VM are snapshotted together by snapshot_zvols(), so the same names exist
            # everywhere barring a very rare external inconsistency).
            pool0, name0 = zvol_specs[0]
            result = [
                {
                    "nom": s["nom"],
                    "description": None,
                    "date_creation": str(s["creation_epoch"]),
                    "etat_vm": "disque_seul",
                    "actuel": False,
                    "parent": None,
                }
                for s in zfs_storage.list_zvol_snapshots(pool0, name0)
            ]
        else:
            snaps = domain.listAllSnapshots()
            result = [_snapshot_summary(s) for s in snaps]
        log_action(user["username"], "list_snapshots", name, "succes")
        return result
    finally:
        conn.close()


class SnapshotCreate(BaseModel):
    name: str
    description: str | None = None


def _create_snapshot_job(task_id, username, vm_name, snap_name, snap_xml):
    """Runs in a separate thread (see create_snapshot) with its OWN libvirt
    connection: never share a Domain/Connect object between threads, libvirt's
    Python bindings do not guarantee it. The final log_action (success/failure) is
    written here, at the real end of the work, not at the synchronous submission,
    which does not know yet whether it will work."""
    conn = open_conn()
    try:
        domain = conn.lookupByName(vm_name)
        domain.snapshotCreateXML(snap_xml, 0)
        finish_task(task_id, "termine")
        log_action(username, "create_snapshot", snap_name, "succes")
    except libvirt.libvirtError as e:
        msg = describe_exception(e)
        finish_task(task_id, "echec", msg)
        log_action(username, "create_snapshot", snap_name, "echec", msg)
    finally:
        conn.close()


def _create_zvol_snapshot_job(task_id, username, zvol_specs, snap_name):
    """ZFS equivalent of _create_snapshot_job(). Nearly instantaneous in practice
    (unlike the qcow2 + memory snapshot, which can take several seconds), it
    still runs as a background task to keep exactly the same API contract
    (task_id, 202) on both sides, so no frontend change is needed."""
    try:
        zfs_storage.snapshot_zvols(zvol_specs, snap_name)
        finish_task(task_id, "termine")
        log_action(username, "create_snapshot", snap_name, "succes")
    except zfs_storage.ZfsError as e:
        finish_task(task_id, "echec", e.message)
        log_action(username, "create_snapshot", snap_name, "echec", e.message)


@router.post("/{name}/snapshots", status_code=202)
def create_snapshot(name: str, payload: SnapshotCreate, user: dict = Depends(require_vm_privilege("vm.snapshot"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "create_snapshot", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        name_error = validate_name(payload.name)
        if name_error:
            log_action(user["username"], "create_snapshot", payload.name, "echec", name_error)
            raise HTTPException(status_code=422, detail=name_error)

        zvol_specs = _zvol_disks_of_domain(domain)
        if zvol_specs:
            # VM on a ZFS pool: the native ZFS mechanism, see zfs_storage.py. It is a
            # DISK-ONLY snapshot (never the memory, unlike the qcow2 path below when the VM
            # is running), documented on the frontend/API side rather than silently
            # different.
            task_id = create_task("create_snapshot", payload.name, node=conn.getHostname(), username=user["username"])
            threading.Thread(
                target=_create_zvol_snapshot_job,
                args=(task_id, user["username"], zvol_specs, payload.name),
                daemon=True,
            ).start()
            return {"task_id": task_id, "nom": payload.name, "statut": "en_cours"}

        try:
            domain.snapshotLookupByName(payload.name)
            log_action(user["username"], "create_snapshot", payload.name, "echec", "Snapshot already exists")
            raise HTTPException(status_code=422, detail=f"A snapshot '{payload.name}' already exists for this VM")
        except libvirt.libvirtError:
            logger.debug("Ignored exception in create_snapshot()", exc_info=True)

        desc_xml = f"<description>{escape(payload.description)}</description>" if payload.description else ""
        snap_xml = f"""
        <domainsnapshot>
          <name>{escape(payload.name)}</name>
          {desc_xml}
        </domainsnapshot>
        """
        # flags=0: internal, memory included automatically if the VM is running, disk
        # only if it is stopped. See the design note above _snapshot_summary for why no
        # other option is offered.
        task_id = create_task("create_snapshot", payload.name, node=conn.getHostname(), username=user["username"])
        threading.Thread(
            target=_create_snapshot_job,
            args=(task_id, user["username"], name, payload.name, snap_xml),
            daemon=True,
        ).start()

        return {"task_id": task_id, "nom": payload.name, "statut": "en_cours"}
    finally:
        conn.close()


def _restore_snapshot_job(task_id, username, vm_name, snapshot_name):
    conn = open_conn()
    try:
        domain = conn.lookupByName(vm_name)
        snap = domain.snapshotLookupByName(snapshot_name)
        domain.revertToSnapshot(snap, 0)
        finish_task(task_id, "termine")
        log_action(username, "restore_snapshot", snapshot_name, "succes")
    except libvirt.libvirtError as e:
        msg = describe_exception(e)
        finish_task(task_id, "echec", msg)
        log_action(username, "restore_snapshot", snapshot_name, "echec", msg)
    finally:
        conn.close()


def _restore_zvol_snapshot_job(task_id, username, zvol_specs, snapshot_name):
    try:
        zfs_storage.rollback_zvols(zvol_specs, snapshot_name)
        finish_task(task_id, "termine")
        log_action(username, "restore_snapshot", snapshot_name, "succes")
    except zfs_storage.ZfsError as e:
        finish_task(task_id, "echec", e.message)
        log_action(username, "restore_snapshot", snapshot_name, "echec", e.message)


@router.post("/{name}/snapshots/{snapshot_name}/restore", status_code=202)
def restore_snapshot(
    name: str, snapshot_name: str, confirm: bool = False, user: dict = Depends(require_vm_privilege("vm.snapshot"))
):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "restore_snapshot", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        zvol_specs = _zvol_disks_of_domain(domain)
        if zvol_specs:
            # SAFETY: unlike the qcow2 internal snapshot (libvirt itself handles the "running
            # VM" case for revertToSnapshot), a `zfs rollback` on a zvol actively opened by
            # the qemu process of a RUNNING VM would desynchronize the guest kernel's cache
            # from the real state of the disk, which means near-certain corruption. The VM
            # MUST be stopped before a ZFS rollback, checked explicitly rather than letting it
            # fail (or worse, silently succeed) in a dangerous way.
            if domain.isActive():
                log_action(
                    user["username"],
                    "restore_snapshot",
                    snapshot_name,
                    "echec",
                    "VM running (it must be stopped for a ZFS rollback)",
                )
                raise HTTPException(
                    status_code=409,
                    detail="The VM must be stopped before restoring a ZFS snapshot (unlike the qcow2 snapshot, a ZFS rollback cannot be done while running)",
                )
            if not confirm:
                log_action(user["username"], "restore_snapshot", snapshot_name, "echec", "Confirmation manquante")
                raise HTTPException(
                    status_code=400, detail="Irreversible action: add ?confirm=true to confirm the restore"
                )
            task_id = create_task("restore_snapshot", snapshot_name, node=conn.getHostname(), username=user["username"])
            threading.Thread(
                target=_restore_zvol_snapshot_job,
                args=(task_id, user["username"], zvol_specs, snapshot_name),
                daemon=True,
            ).start()
            return {
                "task_id": task_id,
                "statut": "en_cours",
                "message": f"Restore of '{name}' to '{snapshot_name}' in progress",
            }

        try:
            domain.snapshotLookupByName(snapshot_name)
        except libvirt.libvirtError:
            log_action(user["username"], "restore_snapshot", snapshot_name, "echec", "Snapshot not found")
            raise HTTPException(status_code=404, detail=f"Snapshot '{snapshot_name}' not found") from None

        if not confirm:
            log_action(user["username"], "restore_snapshot", snapshot_name, "echec", "Confirmation manquante")
            raise HTTPException(status_code=400, detail="Irreversible action: add ?confirm=true to confirm the restore")

        task_id = create_task("restore_snapshot", snapshot_name, node=conn.getHostname(), username=user["username"])
        threading.Thread(
            target=_restore_snapshot_job,
            args=(task_id, user["username"], name, snapshot_name),
            daemon=True,
        ).start()

        return {
            "task_id": task_id,
            "statut": "en_cours",
            "message": f"Restore of '{name}' to '{snapshot_name}' in progress",
        }
    finally:
        conn.close()


@router.delete("/{name}/snapshots/{snapshot_name}")
def delete_snapshot(name: str, snapshot_name: str, user: dict = Depends(require_vm_privilege("vm.snapshot"))):
    # It stays synchronous (no background thread/task): unlike create/restore,
    # deleting an internal snapshot is nearly instantaneous even with a child (libvirt
    # reparents the child automatically), tested and confirmed on this host. `zfs
    # destroy` of a snapshot is of the same order of magnitude (nearly instantaneous),
    # hence the same choice for ZFS VMs.
    conn = open_conn()
    task_id = create_task("delete_snapshot", snapshot_name, node=conn.getHostname(), username=user["username"])
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "delete_snapshot", name, "echec", "VM not found", task_id=task_id)
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        zvol_specs = _zvol_disks_of_domain(domain)
        if zvol_specs:
            try:
                zfs_storage.delete_zvol_snapshot(zvol_specs, snapshot_name)
            except zfs_storage.ZfsError as e:
                log_action(user["username"], "delete_snapshot", snapshot_name, "echec", e.message, task_id=task_id)
                raise HTTPException(status_code=500, detail=f"Deletion error: {e.message}") from e
            log_action(user["username"], "delete_snapshot", snapshot_name, "succes", task_id=task_id)
            return {"message": f"Snapshot '{snapshot_name}' deleted"}

        try:
            snap = domain.snapshotLookupByName(snapshot_name)
        except libvirt.libvirtError:
            log_action(
                user["username"], "delete_snapshot", snapshot_name, "echec", "Snapshot not found", task_id=task_id
            )
            raise HTTPException(status_code=404, detail=f"Snapshot '{snapshot_name}' not found") from None

        try:
            snap.delete(0)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "delete_snapshot", snapshot_name, "echec", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Deletion error: {msg}") from e

        log_action(user["username"], "delete_snapshot", snapshot_name, "succes", task_id=task_id)
        return {"message": f"Snapshot '{snapshot_name}' deleted"}
    finally:
        conn.close()


class CloneRequest(BaseModel):
    new_name: str


@router.post("/{name}/clone", status_code=201)
def clone_vm(name: str, payload: CloneRequest, user: dict = Depends(require_vm_privilege("vm.clone"))):
    # Real bugs fixed in the cloning logic after an audit:
    # 1. SECURITY: there was no permission check (just get_current_user), so any
    #    account, even an "observateur" (read-only everywhere else), could clone and
    #    create a new VM. It is now gated by a dedicated ACL privilege ("vm.clone",
    #    see permissions.py), granted by default to no predefined role: an admin must
    #    add it explicitly to a custom role to delegate cloning.
    # 2. DATA CORRUPTION: only the FIRST disk (device='disk') was copied, so a
    #    multi-disk VM ended up with the clone and the original pointing at the SAME
    #    qcow2 file for the following disks (two VMs writing to the same file as soon
    #    as both run). All the disks are now cloned individually.
    # 3. LEAK BETWEEN ORIGINAL AND CLONE: the cloned disk is a bit-for-bit copy of the
    #    source disk, with the same hostname, machine-id and SSH HOST KEYS as the
    #    original as long as nothing forces a reconfiguration. For VMs created through
    #    the default cloud-init path (verifiable: a <name>-cloudinit.iso file exists),
    #    a new minimal cloud-init ISO (a new hostname + a new instance-id, NOT the
    #    password, which Hyperlite never keeps anywhere and could not reinject) is
    #    given to the clone: cloud-init detects a "new instance" at first boot and
    #    regenerates the hostname and SSH host keys by itself. For VMs installed from
    #    an ISO (kickstart/autoinstall or manual), no guest customization is possible,
    #    the same limit as a hypervisor without a guest agent, documented in the
    #    response rather than silently ignored.
    conn = open_conn()
    task_id = create_task("clone_vm", name, node=conn.getHostname(), username=user["username"])
    new_disk_paths = []
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "clone_vm", name, "echec", "Source VM not found", task_id=task_id)
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        try:
            validate_name(payload.new_name)
        except ValueError as exc:
            log_action(
                user["username"], "clone_vm", name, "echec", f"invalid name: {payload.new_name}", task_id=task_id
            )
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        try:
            conn.lookupByName(payload.new_name)
            log_action(
                user["username"], "clone_vm", name, "echec", f"'{payload.new_name}' already exists", task_id=task_id
            )
            raise HTTPException(status_code=409, detail=f"A VM '{payload.new_name}' already exists")
        except libvirt.libvirtError:
            logger.debug("Ignored exception in clone_vm()", exc_info=True)

        if domain.isActive():
            log_action(user["username"], "clone_vm", name, "echec", "VM active", task_id=task_id)
            raise HTTPException(status_code=409, detail="Stop the VM before cloning it")

        root = ET.fromstring(domain.XMLDesc(0))

        disk_els = [d for d in root.findall(".//devices/disk") if d.get("device") == "disk"]
        if not disk_els:
            log_action(user["username"], "clone_vm", name, "echec", "source disk not found", task_id=task_id)
            raise HTTPException(status_code=500, detail="Source disk not found")

        # Clone ALL the disks (not only the first one, see the note above).
        for i, disk_el in enumerate(disk_els):
            source_el = disk_el.find("source")
            source_path = source_el.get("file") if source_el is not None else None
            if not source_path:
                for p in new_disk_paths:
                    Path(p).unlink(missing_ok=True)
                log_action(user["username"], "clone_vm", name, "echec", "source disk path not found", task_id=task_id)
                raise HTTPException(status_code=500, detail="Source disk path not found")

            suffix = "" if i == 0 else f"-{i + 1}"
            new_disk_path = IMAGES_DIR / f"{payload.new_name}{suffix}.qcow2"
            if new_disk_path.exists():
                for p in new_disk_paths:
                    Path(p).unlink(missing_ok=True)
                log_action(
                    user["username"],
                    "clone_vm",
                    name,
                    "echec",
                    f"'{new_disk_path.name}' already exists",
                    task_id=task_id,
                )
                raise HTTPException(status_code=409, detail=f"A disk file '{new_disk_path.name}' already exists")

            try:
                subprocess.run(
                    ["qemu-img", "convert", "-O", "qcow2", source_path, str(new_disk_path)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as exc:
                for p in new_disk_paths:
                    Path(p).unlink(missing_ok=True)
                msg = f"disk copy: {exc.stderr or exc}"
                log_action(user["username"], "clone_vm", name, "echec", msg, task_id=task_id)
                raise HTTPException(status_code=500, detail="Disk copy failed") from exc

            new_disk_paths.append(str(new_disk_path))
            source_el.set("file", str(new_disk_path))

        name_el = root.find("name")
        if name_el is not None:
            name_el.text = payload.new_name

        uuid_el = root.find("uuid")
        if uuid_el is not None:
            root.remove(uuid_el)  # libvirt generates a new, distinct one at defineXML

        devices_el = root.find(".//devices")
        if devices_el is not None:
            for disk in list(devices_el.findall("disk")):
                if disk.get("device") == "cdrom":
                    devices_el.remove(disk)

        # Explicit MAC (rather than letting libvirt pick one at random): it allows
        # reserving a fixed IP for the clone right away, as when a VM is created (see
        # create_vm / network_alloc.py). Best-effort: a failed reservation does not block
        # the cloning, only the fixed IP.
        new_ip_reservations = []
        for iface in root.findall(".//devices/interface"):
            old_mac = iface.find("mac")
            if old_mac is not None:
                iface.remove(old_mac)
            new_mac = generate_mac(conn)
            ET.SubElement(iface, "mac", {"address": new_mac})
            source_el = iface.find("source")
            iface_network = source_el.get("network") if source_el is not None else None
            if iface_network:
                try:
                    allocate_static_ip(conn, iface_network, new_mac)
                    new_ip_reservations.append((iface_network, new_mac))
                except libvirt.libvirtError:
                    logger.debug("Ignored exception in clone_vm()", exc_info=True)

        # Guest customization (hostname + SSH host keys): only for the default cloud-init
        # path, detectable by the presence of its ISO. See the design note above the
        # function.
        reseed_iso = None
        if (IMAGES_DIR / f"{name}-cloudinit.iso").exists():
            try:
                reseed_iso = create_cloudinit_reseed_iso(payload.new_name)
                ET.SubElement(devices_el, "disk", {"type": "file", "device": "cdrom"}).extend(
                    [
                        ET.fromstring("<driver name='qemu' type='raw'/>"),
                        ET.fromstring(f"<source file='{reseed_iso}'/>"),
                        ET.fromstring("<target dev='hdc' bus='ide'/>"),
                        ET.fromstring("<readonly/>"),
                    ]
                )
            except subprocess.CalledProcessError:
                reseed_iso = None  # too bad for the customization, the clone remains functional

        new_xml = ET.tostring(root, encoding="unicode")

        try:
            new_domain = conn.defineXML(new_xml)
        except libvirt.libvirtError as exc:
            for p in new_disk_paths:
                Path(p).unlink(missing_ok=True)
            if reseed_iso:
                Path(reseed_iso).unlink(missing_ok=True)
            for iface_network, mac in new_ip_reservations:
                try:
                    release_static_ip(conn, iface_network, mac)
                except libvirt.libvirtError:
                    logger.debug("Ignored exception in clone_vm()", exc_info=True)
            msg = describe_exception(exc)
            log_action(user["username"], "clone_vm", name, "echec", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Clone definition failed: {msg}") from exc

        rename_vm_ssh_user(name, payload.new_name)
        rename_vm_os_label(name, payload.new_name)
        log_action(user["username"], "clone_vm", name, "succes", f"clone -> {payload.new_name}", task_id=task_id)
        return {
            "source": name,
            "clone": new_domain.name(),
            "etat": "arretee",
            "personnalisation_invite": reseed_iso is not None,
        }
    finally:
        conn.close()


class MigrateRequest(BaseModel):
    target_node: str
    # Compatibility checks: a heuristic can be wrong, so the admin has the last word.
    # This only disables the REFUSAL, and the checks are still logged.
    ignorer_verifications: bool = False


def _norm_node(n):
    """None/'local' = the local host."""
    return None if n in (None, "", "local") else n


# _pool_target_path/_domain_disk_paths/_uses_shared_storage moved to
# app/core/libvirt_utils.py: shared with the shared-storage detection needed for
# HA protection. See pool_type_and_target_path/domain_disk_paths/uses_shared_storage.


def _migration_progress_job(stop_event, task_id, node, vm_name):
    """Runs in its OWN thread (a third one, with its own libvirt connection: never share
    a Domain object between threads) while the main thread is blocked in
    domain.migrate(). jobInfo() can fail transiently (no job started yet, job
    finished between two calls), which is never fatal here: just ignored,
    best-effort."""
    conn = None
    try:
        conn = open_conn(node)
        domain = conn.lookupByName(vm_name)
        while not stop_event.is_set():
            try:
                info = domain.jobInfo()
                # (type, timeElapsed, timeRemaining, dataTotal, dataProcessed, dataRemaining, ...)
                data_total, data_processed = info[3], info[4]
                if data_total > 0:
                    pct = min(99, int(data_processed * 100 / data_total))
                    update_task_progress(task_id, pct)
            except libvirt.libvirtError:
                logger.debug("Ignored exception in _migration_progress_job()", exc_info=True)
            stop_event.wait(2)
    except libvirt.libvirtError:
        logger.debug("Ignored exception in _migration_progress_job()", exc_info=True)
    finally:
        if conn:
            conn.close()


def _cdrom_source_paths(domain):
    root = ET.fromstring(domain.XMLDesc(0))
    paths = []
    for disk_el in root.findall(".//devices/disk"):
        if disk_el.get("device") != "cdrom":
            continue
        source_el = disk_el.find("source")
        path = source_el.get("file") if source_el is not None else None
        if path:
            paths.append(path)
    return paths


def _delete_paths_on_node(paths, node_name):
    """Best-effort: delete a list of files, LOCALLY if node_name is None/"local", over
    SSH (cluster key) otherwise: the same logic as _perform_vm_deletion, reused
    here to clean up the SOURCE disk after a successful migration on NON-shared
    storage. A real bug found when testing multi-node VM actions: a
    VIR_MIGRATE_NON_SHARED_DISK migration copies the disk to the destination but
    NEVER deletes the source file, so every migration silently left an orphan
    qcow2 + cloud-init ISO on the origin node."""
    if not paths:
        return
    if not node_name or node_name == "local":
        for p in paths:
            Path(p).unlink(missing_ok=True)
        return
    from app.core.cluster import get_node, node_ssh_options

    remote_node = get_node(node_name)
    if not remote_node:
        return
    ssh_opts = node_ssh_options()
    ssh_target = f"{remote_node['ssh_user']}@{remote_node['hostname']}"
    for p in paths:
        subprocess.run(
            ["ssh", *ssh_opts, "-p", str(remote_node["ssh_port"]), ssh_target, "rm", "-f", str(p)],
            capture_output=True,
            text=True,
            timeout=15,
        )


def _copy_file_to_node(local_path, node_name, remote_path):
    """Best-effort scp of a LOCAL file to the same absolute path on a remote node,
    with the SSH key dedicated to the cluster (the same key as the qemu+ssh://
    connections). A real bug found when testing a real migration:
    VIR_MIGRATE_NON_SHARED_DISK only copies device='disk' disks, never the
    device='cdrom' CD-ROMs/ISOs, so the cloud-init ISO (created for every VM,
    see vm_builder.py::create_cloudinit_iso) was systematically missing on the
    destination and the migration was refused ('unable to access the storage
    file'). Only supported from the LOCAL node (Hyperlite always drives from the
    local host, see cluster.py): migrating a VM between two remote nodes fails
    here with a clear error rather than being handled silently."""
    from app.core.cluster import get_node, node_ssh_options

    node = get_node(node_name)
    if not node:
        raise RuntimeError(f"Node '{node_name}' not found")
    remote_dir = str(Path(remote_path).parent)
    ssh_target = f"{node['ssh_user']}@{node['hostname']}"
    ssh_opts = node_ssh_options()
    mkdir_r = subprocess.run(
        ["ssh", *ssh_opts, "-p", str(node["ssh_port"]), ssh_target, "mkdir", "-p", remote_dir],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if mkdir_r.returncode != 0:
        raise RuntimeError(
            f"Preparing the remote directory failed ({remote_dir} on {node_name}): {mkdir_r.stderr.strip()[:300]}"
        )
    scp_r = subprocess.run(
        ["scp", *ssh_opts, "-P", str(node["ssh_port"]), local_path, f"{ssh_target}:{remote_path}"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if scp_r.returncode != 0:
        raise RuntimeError(f"Copy of '{Path(local_path).name}' to {node_name} failed: {scp_r.stderr.strip()[:300]}")


def _copy_file_from_node(node_name, remote_path, local_path):
    """Mirror of _copy_file_to_node: scp of a file from a remote node to the LOCAL
    host, with the same SSH key dedicated to the cluster. Needed for migration
    from a remote node to the local host (reverse SSH trust): the SAME limitation
    as documented for the forward direction applies here in mirror, since
    VIR_MIGRATE_NON_SHARED_DISK only copies device='disk' disks, never
    CD-ROMs/ISOs. A real bug found when testing this precisely, previously masked
    by the ORPHAN bug fixed at the same time: a cloud-init.iso file wrongly left
    on the local host after a previous forward migration gave the illusion that
    the return direction worked, whereas nothing really copied that file."""
    from app.core.cluster import get_node, node_ssh_options

    node = get_node(node_name)
    if not node:
        raise RuntimeError(f"Node '{node_name}' not found")
    Path(local_path).parent.mkdir(parents=True, exist_ok=True)
    ssh_target = f"{node['ssh_user']}@{node['hostname']}"
    ssh_opts = node_ssh_options()
    scp_r = subprocess.run(
        ["scp", *ssh_opts, "-P", str(node["ssh_port"]), f"{ssh_target}:{remote_path}", local_path],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if scp_r.returncode != 0:
        raise RuntimeError(f"Copy of '{Path(remote_path).name}' from {node_name} failed: {scp_r.stderr.strip()[:300]}")


def _domain_network_names(domain):
    root = ET.fromstring(domain.XMLDesc(0))
    names = []
    for iface in root.findall(".//devices/interface"):
        source_el = iface.find("source")
        if source_el is not None and source_el.get("network"):
            names.append(source_el.get("network"))
    return names


def _ensure_networks_active(conn, network_names):
    """Start (and autostart) on `conn` every libvirt network named in network_names
    that exists but is inactive. A real bug found when testing a real
    cross-site migration to a manually installed node that never went through
    the Hyperlite installer (see ensure_default_pool for the same kind of guard
    on the storage side): the 'default' network existed but was not started on
    the destination node, and libvirt refused the migration with a poorly
    actionable error ("network default is not active"). Rather than letting it
    fail and leaving the user to guess, Hyperlite fixes the common case itself.
    It does nothing if the network does not exist at all on the destination:
    that remains a real error to report as is (a misconfiguration, not a simple
    forgotten start)."""
    for name in network_names:
        try:
            net = conn.networkLookupByName(name)
        except libvirt.libvirtError:
            logger.debug("Ignored exception in _ensure_networks_active()", exc_info=True)
            continue  # network missing on the destination: migrate() will fail with a clear error, nothing to "repair" here
        if not net.isActive():
            net.create()
            net.setAutostart(True)


def _local_migrate_uri_host():
    """Address through which THIS node (the local host, never a row of the `nodes`
    table: it is the host running Hyperlite itself) is reachable by ANOTHER node
    for the QEMU data stream of a migration. Only useful when the local host is
    the DESTINATION of a migration (the far more frequent direction, TOWARDS a
    registered remote node, does not need it, see below). It queries Tailscale
    (already used by this project) rather than guessing or hard-coding an IP.
    Best-effort: None if unavailable, and the migration then tries without an
    explicit migrate_uri instead of failing here."""
    try:
        r = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError):
        logger.debug("Ignored exception in _local_migrate_uri_host()", exc_info=True)
    return None


def _migrate_vm_job(task_id, username, source_node, target_node, vm_name):
    from app.core.cluster import build_libvirt_uri, get_node

    # "local" is the FRONTEND convention for the local host (see
    # fetchNodes(), dashboard/src/api/client.js), never a row of the `nodes` table on
    # the backend. A real bug found when testing a complete round trip: open_conn()
    # tries to RESOLVE it as a registered remote node and fails with 404 as soon as
    # target_node is that literal value. It is translated here to None (= a local
    # connection, the same convention as everywhere else in the code, see open_conn()).
    dest_node_key = None if target_node == "local" else target_node
    src_conn = open_conn(source_node)
    dest_conn = None
    stop_event = threading.Event()
    progress_thread = threading.Thread(
        target=_migration_progress_job, args=(stop_event, task_id, source_node, vm_name), daemon=True
    )
    try:
        domain = src_conn.lookupByName(vm_name)
        dest_conn = open_conn(dest_node_key)

        _ensure_networks_active(dest_conn, _domain_network_names(domain))

        # Paths of the SOURCE disks, captured BEFORE the migration: needed to clean up the
        # original disk AFTER a successful migration on non-shared storage (see below).
        # The source domain is UNDEFINED (VIR_MIGRATE_UNDEFINE_SOURCE) once the migration
        # is over, so it can no longer be queried at that point.
        source_disk_paths = domain_disk_paths(domain)

        # Real bug found when testing a migration from a remote node to the local host on
        # NON-shared storage: "Cannot access storage file ... No such file or directory".
        # Confirmed through research (wiki.libvirt.org/Migration_fails_because_disk_image_
        # cannot_be_found.html) that some libvirt versions require the destination file to
        # ALREADY exist before the NON_SHARED_DISK transfer: the automatic creation on the
        # destination side does not trigger reliably in this specific direction (it works
        # in the local host -> remote node direction). Fixed by PRE-CREATING blank qcow2
        # files of the right size on the local host before calling migrateToURI3. The size
        # is read with domain.blockInfo() (a libvirt API that works through the remote
        # connection, with no need for direct file access, which would collide with the
        # write lock of an ACTIVE VM's disk anyway).
        if source_node and source_node != "local" and dest_node_key is None:
            root_for_targets = ET.fromstring(domain.XMLDesc(0))
            for disk_el in root_for_targets.findall(".//devices/disk"):
                if disk_el.get("device") != "disk":
                    continue
                source_el, target_el = disk_el.find("source"), disk_el.find("target")
                path = source_el.get("file") if source_el is not None else None
                dev = target_el.get("dev") if target_el is not None else None
                if not path or not dev or Path(path).exists():
                    continue
                try:
                    capacity = domain.blockInfo(dev)[0]
                    subprocess.run(
                        ["qemu-img", "create", "-f", "qcow2", path, str(capacity)],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                except (libvirt.libvirtError, subprocess.CalledProcessError):
                    logger.debug(
                        "Ignored exception in _migrate_vm_job()", exc_info=True
                    )  # best-effort: migrateToURI3 will report a clear error if the pre-creation fails

        # Attached cloud-init ISOs/CD-ROMs are never copied by libvirt
        # (VIR_MIGRATE_NON_SHARED_DISK only copies device='disk'): handled manually here
        # in BOTH directions. Without this copy the migration fails ('unable to access the
        # storage file') as soon as the destination domain references an ISO that is
        # missing on the destination. It was hidden for a while by the ORPHAN bug fixed at
        # the same time (a cloud-init.iso file left by an earlier forward migration gave
        # the illusion that the return direction worked, by coincidence with the same
        # expected path).
        copied_cdrom_paths = []
        if not source_node or source_node == "local":
            for cdrom_path in _cdrom_source_paths(domain):
                if Path(cdrom_path).exists():
                    _copy_file_to_node(cdrom_path, target_node, cdrom_path)
                    copied_cdrom_paths.append(cdrom_path)
        else:
            for cdrom_path in _cdrom_source_paths(domain):
                try:
                    _copy_file_from_node(source_node, cdrom_path, cdrom_path)
                    copied_cdrom_paths.append(cdrom_path)
                except RuntimeError:
                    logger.debug(
                        "Ignored exception in _migrate_vm_job()", exc_info=True
                    )  # ISO missing/unreachable on the source side: let migrateToURI3 fail with a clear error rather than guessing

        shared = uses_shared_storage(src_conn, dest_conn, domain)
        # The constant is called VIR_MIGRATE_PERSIST_DEST in this libvirt-python version,
        # not VIR_MIGRATE_PERSISTENT (which does not exist at all, verified through
        # dir(libvirt)). The wrong name crashed the thread BEFORE the first jobInfo call,
        # outside the `except libvirt.libvirtError` below, leaving a task stuck "en_cours"
        # forever with no trace for the user other than the server logs.
        flags = (
            libvirt.VIR_MIGRATE_LIVE
            | libvirt.VIR_MIGRATE_PEER2PEER
            | libvirt.VIR_MIGRATE_PERSIST_DEST
            | libvirt.VIR_MIGRATE_UNDEFINE_SOURCE
        )
        if not shared:
            flags |= libvirt.VIR_MIGRATE_NON_SHARED_DISK

        # NO VIR_MIGRATE_TUNNELLED: tested for real, and it systematically fails with
        # 'internal error: the host argument key must not have a Null value' as soon as
        # PEER2PEER+TUNNELLED are combined on this libvirt version (9.0.0, Debian 12) with
        # a qemu+ssh:// URI carrying query parameters (keyfile=/sshauth=privkey). It is
        # very probably a libvirt bug in that specific code path, not something fixable on
        # the Hyperlite side. WITHOUT the tunnel, the QEMU data stream (memory + disk with
        # VIR_MIGRATE_NON_SHARED_DISK) goes DIRECTLY between the two hosts rather than
        # through the SSH tunnel. That is acceptable here: a registered node (see
        # cluster.py) must already be directly reachable for SSH, so it almost always is
        # for this direct stream too (confirmed for real between two hosts on different
        # sites through Tailscale). A second real bug found at the same time: without
        # being told explicitly, QEMU tries to resolve the destination node's OWN host name
        # as IT knows it (e.g. 'hyperlite.home', not resolvable from the other host)
        # rather than the address through which Hyperlite actually reached it. Fixed by
        # providing migrate_uri explicitly, with the address already verified reachable
        # (the same registered `hostname` used for the qemu+ssh:// connection itself).
        if dest_node_key is None:
            local_addr = _local_migrate_uri_host()
            if source_node and source_node != "local":
                # Migration from a remote node to the local host (see
                # cluster.py::ensure_reverse_trust). A plain "qemu:///system" would be interpreted
                # BY THE SOURCE LIBVIRTD (the remote node's, since this URI runs THERE) as
                # "myself", which is exactly the already documented bug ("Attempt to migrate guest
                # to the same host"). An EXPLICIT qemu+ssh:// URI to the local host is needed,
                # authenticated with the dedicated key pushed to THIS node at its registration
                # (never the shared local host -> nodes key, see the ensure_reverse_trust docstring
                # for why).
                from app.core.cluster import get_reverse_key_remote_path

                if not local_addr:
                    raise RuntimeError(
                        "Tailscale address of the local host not found: migration from a remote node to the local host is impossible without it"
                    )
                dest_uri = f"qemu+ssh://root@{local_addr}/system?keyfile={get_reverse_key_remote_path()}&no_verify=1&sshauth=privkey"
            else:
                dest_uri = "qemu:///system"
            migrate_params = {"migrate_uri": f"tcp://{local_addr}"} if local_addr else {}
        else:
            dest_node = get_node(dest_node_key)
            dest_uri = build_libvirt_uri(dest_node)
            migrate_params = {"migrate_uri": f"tcp://{dest_node['hostname']}"}

        progress_thread.start()
        domain.migrateToURI3(dest_uri, migrate_params, flags)

        # Cleanup of the SOURCE disk: VIR_MIGRATE_NON_SHARED_DISK copies to the destination
        # but NEVER deletes the original file, so every migration on non-shared storage
        # systematically left an orphan qcow2 on the source node with nothing to signal it.
        # Only if `not shared`: a disk on SHARED storage must obviously never be deleted
        # (it is the SAME file seen by both nodes).
        if not shared:
            _delete_paths_on_node(source_disk_paths, source_node)
            # cdrom files copied above (only the local host -> remote node direction, see
            # copied_cdrom_paths): the source file (on the local host, so always a LOCAL
            # unlink here) becomes useless once the copy to the destination is confirmed
            # successful.
            _delete_paths_on_node(copied_cdrom_paths, source_node)

        stop_event.set()
        update_task_progress(task_id, 100)
        finish_task(task_id, "termine")
        log_action(
            username,
            "migrate_vm",
            vm_name,
            "succes",
            f"{source_node} -> {target_node} ({'shared' if shared else 'copied'} storage)",
        )
    except libvirt.libvirtError as e:
        stop_event.set()
        msg = describe_exception(e)
        finish_task(task_id, "echec", msg)
        log_action(username, "migrate_vm", vm_name, "echec", msg)
    except Exception as e:
        # Generic safety net: ANY unexpected exception must still close the task,
        # otherwise it stays "en_cours" forever in the UI with no visible explanation for
        # the user. It was really needed in testing (see the comment above about
        # VIR_MIGRATE_PERSIST_DEST).
        stop_event.set()
        finish_task(task_id, "echec", f"Internal error: {e}")
        log_action(username, "migrate_vm", vm_name, "echec", f"Internal error: {e}")
    finally:
        stop_event.set()
        src_conn.close()
        if dest_conn:
            dest_conn.close()


@router.get("/{name}/migration-check")
def migration_check(name: str, target_node: str, node: str | None = None, user: dict = Depends(require_role("admin"))):
    """Compatibility diagnostic BEFORE a migration: read-only, it migrates nothing. See
    app/core/cluster_compat.py."""
    if _norm_node(target_node) == _norm_node(node):
        raise HTTPException(status_code=422, detail="The destination node must be different from the source node")
    src_conn = open_conn(_norm_node(node))
    try:
        try:
            domain = src_conn.lookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        dst_conn = open_conn(_norm_node(target_node))
        try:
            return cluster_compat.report(cluster_compat.check_vm_migration(src_conn, dst_conn, domain))
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


@router.post("/{name}/migrate", status_code=202)
def migrate_vm(
    name: str, payload: MigrateRequest, node: str | None = None, user: dict = Depends(require_role("admin"))
):
    """Live migration to another node (relying on shared storage when available).
    Restricted to admins (not a per-VM ACL privilege like vm.clone): moving a VM
    changes the resource allocation of a node OF THE WHOLE CLUSTER, a scope beyond
    what an ACL scoped to one VM is meant to cover."""
    if _norm_node(payload.target_node) == _norm_node(node):
        raise HTTPException(status_code=422, detail="The destination node must be different from the source node")
    # Migration from a remote node to the local host was long blocked here, because
    # peer-to-peer migration is initiated by the SOURCE libvirtd, which needs to be
    # able to connect ITSELF to the local host. It is now possible through a dedicated
    # reverse SSH trust, established automatically when each node is registered (see
    # cluster.py::ensure_reverse_trust, a key SPECIFIC to that node, never shared). If
    # that trust could not be established (a node registered before this existed, or a
    # best-effort failure at the time), the migration fails with a clear SSH error on
    # the task side rather than being refused a priori here.

    src_conn = open_conn(node)
    try:
        try:
            domain = src_conn.lookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        if not domain.isActive():
            raise HTTPException(
                status_code=409,
                detail="The VM must be running for a live migration (use export/import for a stopped VM)",
            )

        # Check the destination right away (a clear immediate error: open_conn already
        # raises an explicit HTTPException if the node is not found or unreachable) rather
        # than letting the background thread fail with no other form of trial for the
        # user. "local" is the frontend convention for the local
        # host, never a row of the `nodes` table (see _migrate_vm_job for the same
        # treatment).
        dest_conn = open_conn(_norm_node(payload.target_node))
        try:
            compat = cluster_compat.report(cluster_compat.check_vm_migration(src_conn, dest_conn, domain))
            if compat["resume"]["bloquant"] and not payload.ignorer_verifications:
                blocages = [c["message"] for c in compat["controles"] if c["statut"] == "blocking"]
                raise HTTPException(
                    status_code=409, detail="Migration refused by the compatibility diagnostic: " + " ; ".join(blocages)
                )
            try:
                dest_conn.lookupByName(name)
                raise HTTPException(status_code=409, detail=f"A VM '{name}' already exists on the destination node")
            except libvirt.libvirtError:
                logger.debug("Ignored exception in migrate_vm()", exc_info=True)
        finally:
            dest_conn.close()

        source_node = node or "local"
        task_id = create_task("migrate_vm", name, node=source_node, username=user["username"])
        threading.Thread(
            target=_migrate_vm_job,
            args=(task_id, user["username"], node, payload.target_node, name),
            daemon=True,
        ).start()
        return {"task_id": task_id, "statut": "en_cours"}
    finally:
        src_conn.close()


class CdromRequest(BaseModel):
    iso: str


@router.put("/{name}/cdrom")
def set_vm_cdrom(name: str, payload: CdromRequest, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "set_vm_cdrom", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        iso_filename = Path(payload.iso).name
        if not iso_filename.lower().endswith(".iso"):
            raise HTTPException(status_code=422, detail="Invalid ISO name")
        iso_path = ISOS_DIR / iso_filename
        if not iso_path.exists():
            raise HTTPException(status_code=404, detail=f"ISO '{iso_filename}' not found")

        root = ET.fromstring(domain.XMLDesc(0))
        devices_el = root.find(".//devices")
        cdrom = None
        if devices_el is not None:
            for disk in devices_el.findall("disk"):
                if disk.get("device") == "cdrom":
                    cdrom = disk
                    break

        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        if domain.isActive():
            flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE

        try:
            if cdrom is not None:
                source = cdrom.find("source")
                if source is None:
                    source = ET.SubElement(cdrom, "source")
                source.set("file", str(iso_path))
                new_xml = ET.tostring(cdrom, encoding="unicode")
                domain.updateDeviceFlags(new_xml, flags)
            else:
                new_cdrom_xml = (
                    '<disk type="file" device="cdrom">'
                    '<driver name="qemu" type="raw"/>'
                    f'<source file="{escape(str(iso_path))}"/>'
                    '<target dev="hdc" bus="ide"/>'
                    "<readonly/>"
                    "</disk>"
                )
                domain.attachDeviceFlags(new_cdrom_xml, flags)
        except libvirt.libvirtError as exc:
            log_action(user["username"], "set_vm_cdrom", name, "echec", str(exc))
            raise HTTPException(status_code=500, detail=f"Mount failed: {exc}") from exc

        log_action(user["username"], "set_vm_cdrom", name, "succes", iso_filename)
        return {"vm": name, "iso": iso_filename}
    finally:
        conn.close()


@router.delete("/{name}/cdrom")
def eject_vm_cdrom(name: str, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "eject_vm_cdrom", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        root = ET.fromstring(domain.XMLDesc(0))
        devices_el = root.find(".//devices")
        cdrom = None
        if devices_el is not None:
            for disk in devices_el.findall("disk"):
                if disk.get("device") == "cdrom":
                    cdrom = disk
                    break
        if cdrom is None:
            raise HTTPException(status_code=404, detail="No CD drive on this VM")

        source = cdrom.find("source")
        if source is not None:
            cdrom.remove(source)

        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        if domain.isActive():
            flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE

        try:
            new_xml = ET.tostring(cdrom, encoding="unicode")
            domain.updateDeviceFlags(new_xml, flags)
        except libvirt.libvirtError as exc:
            log_action(user["username"], "eject_vm_cdrom", name, "echec", str(exc))
            raise HTTPException(status_code=500, detail=f"Eject failed: {exc}") from exc

        log_action(user["username"], "eject_vm_cdrom", name, "succes")
        return {"vm": name, "ejecte": True}
    finally:
        conn.close()


@router.get("/{name}/metrics")
def get_vm_metrics(name: str, user: dict = Depends(get_current_user)):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "get_vm_metrics", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        if not domain.isActive():
            return {
                "etat": "arrete",
                "cpu_pourcent": None,
                "memoire_allouee_mo": None,
                "memoire_utilisee_mo": None,
                "disques": [],
                "reseaux": [],
            }

        root = ET.fromstring(domain.XMLDesc(0))
        disk_devs = []
        for disk in root.findall(".//devices/disk"):
            if disk.get("device") != "disk":
                continue
            target = disk.find("target")
            if target is not None and target.get("dev"):
                disk_devs.append(target.get("dev"))
        iface_devs = []
        for iface in root.findall(".//devices/interface"):
            target = iface.find("target")
            if target is not None and target.get("dev"):
                iface_devs.append(target.get("dev"))

        def sample():
            cpu_time = domain.getCPUStats(True)[0]["cpu_time"]
            disk_samples = {}
            for dev in disk_devs:
                try:
                    disk_samples[dev] = domain.blockStats(dev)
                except libvirt.libvirtError:
                    logger.debug("Ignored exception in sample()", exc_info=True)
            net_samples = {}
            for dev in iface_devs:
                try:
                    net_samples[dev] = domain.interfaceStats(dev)
                except libvirt.libvirtError:
                    logger.debug("Ignored exception in sample()", exc_info=True)
            return cpu_time, disk_samples, net_samples

        cpu1, disk1, net1 = sample()
        t1 = time.time()
        time.sleep(0.4)
        cpu2, disk2, net2 = sample()
        t2 = time.time()
        elapsed = max(t2 - t1, 0.001)

        info = domain.info()
        nvcpu = info[3] or 1
        cpu_pourcent = round(max(0.0, min(100.0, ((cpu2 - cpu1) / (elapsed * 1e9)) * 100 / nvcpu)), 1)
        memoire_allouee_mo = round(info[2] / 1024, 1)

        memoire_utilisee_mo = None
        try:
            mem_stats = domain.memoryStats()
            if "available" in mem_stats and "unused" in mem_stats:
                memoire_utilisee_mo = round((mem_stats["available"] - mem_stats["unused"]) / 1024, 1)
            elif "rss" in mem_stats:
                memoire_utilisee_mo = round(mem_stats["rss"] / 1024, 1)
        except libvirt.libvirtError:
            logger.debug("Ignored exception in get_vm_metrics()", exc_info=True)

        disques = []
        for dev in disk_devs:
            if dev in disk1 and dev in disk2:
                rd_rate = max(0, (disk2[dev][1] - disk1[dev][1]) / elapsed)
                wr_rate = max(0, (disk2[dev][3] - disk1[dev][3]) / elapsed)
                disques.append(
                    {
                        "cible": dev,
                        "lecture_ko_s": round(rd_rate / 1024, 1),
                        "ecriture_ko_s": round(wr_rate / 1024, 1),
                    }
                )

        reseaux = []
        for dev in iface_devs:
            if dev in net1 and dev in net2:
                rx_rate = max(0, (net2[dev][0] - net1[dev][0]) / elapsed)
                tx_rate = max(0, (net2[dev][4] - net1[dev][4]) / elapsed)
                reseaux.append(
                    {
                        "interface": dev,
                        "reception_ko_s": round(rx_rate / 1024, 1),
                        "emission_ko_s": round(tx_rate / 1024, 1),
                    }
                )

        log_action(user["username"], "get_vm_metrics", name, "succes")
        return {
            "etat": "actif",
            "cpu_pourcent": cpu_pourcent,
            "memoire_allouee_mo": memoire_allouee_mo,
            "memoire_utilisee_mo": memoire_utilisee_mo,
            "disques": disques,
            "reseaux": reseaux,
        }
    finally:
        conn.close()


# Beyond this delay without a working SSH, passive polling stops and the
# installation is declared failed. Before, a broken install (an incompatible ISO,
# a partitioning error, a network outage during the installation...) stayed "in
# progress" indefinitely and never reported an actionable error (seen in
# practice: several stale "ubuntu-autoinstall-fix" attempts in the audit log,
# never cleaned up). 30 minutes is generous for the supported families
# (kickstart/autoinstall), even on a slow disk.
PROVISIONING_TIMEOUT_S = 1800


@router.get("/{name}/provisioning")
def get_vm_provisioning(name: str, user: dict = Depends(get_current_user)):
    """State of an unattended installation (Kickstart/autoinstall) in progress, for the
    dashboard's progress bar. The signal used is whether a real SSH
    authentication with the Hyperlite automation key succeeds, NOT just "port 22
    answers" (seen in testing on Ubuntu: the live-server ISO runs its own sshd
    from the very start of the installation, long before the final system exists,
    so the port is reachable very early without our key being authorized there,
    which gave a false, premature "finished")."""
    prov = get_provisioning(name)
    if not prov:
        return {"provisioning": False}

    task_id = prov.get("task_id")

    def _fail(reason):
        clear_provisioning(name)
        if task_id:
            finish_task(task_id, "echec", reason)
        log_action(user["username"], "auto_install", name, "echec", reason)
        return {"provisioning": False, "failed": True, "erreur": reason}

    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            return _fail("The VM disappeared during the unattended installation (deleted?)")

        started = datetime.fromisoformat(prov["started_at"])
        elapsed_s = int((datetime.now(UTC) - started).total_seconds())

        if elapsed_s > PROVISIONING_TIMEOUT_S:
            return _fail(f"Timeout: SSH still unreachable after {elapsed_s // 60} minutes")

        if not domain.isActive():
            return {"provisioning": True, "phase": "arretee", "os_family": prov["os_family"], "elapsed_s": elapsed_s}

        ip = _get_ip(domain)
        if not ip:
            return {"provisioning": True, "phase": "demarrage", "os_family": prov["os_family"], "elapsed_s": elapsed_s}

        username = get_vm_ssh_user(name)
        key_path = get_automation_private_key_path()
        try:
            result = subprocess.run(
                [
                    "ssh",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "UserKnownHostsFile=/dev/null",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=3",
                    "-i",
                    str(key_path),
                    f"{username}@{ip}",
                    "true",
                ],
                capture_output=True,
                timeout=6,
            )
        except subprocess.TimeoutExpired:
            return {
                "provisioning": True,
                "phase": "installation",
                "os_family": prov["os_family"],
                "elapsed_s": elapsed_s,
                "ip": ip,
            }

        if result.returncode == 0:
            clear_provisioning(name)
            # Ubuntu/autoinstall started on a kernel/initrd extracted from the ISO (see
            # create_vm, extract_casper_kernel) to add "autoinstall" to the command line. That
            # is no longer needed once the OS is installed on the disk, and leaving it would
            # make the VM reboot forever into the live installer instead of the installed
            # system (the normal <boot order>, on the disk, is never consulted while
            # <kernel>/<initrd> are present). The override is removed from the PERSISTENT XML
            # only: the VM keeps running without interruption with its current live
            # configuration until the next restart.
            if prov["os_family"] == "autoinstall":
                try:
                    current_xml = domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)
                    new_xml = strip_install_boot_override(current_xml)
                    if new_xml != current_xml:
                        conn.defineXML(new_xml)
                except (libvirt.libvirtError, ET.ParseError):
                    logger.debug("Ignored exception in get_vm_provisioning()", exc_info=True)
            if task_id:
                finish_task(task_id, "termine")
            log_action(user["username"], "provisioning_complete", name, "succes")
            return {"provisioning": False, "just_finished": True}
        return {
            "provisioning": True,
            "phase": "installation",
            "os_family": prov["os_family"],
            "elapsed_s": elapsed_s,
            "ip": ip,
        }
    finally:
        conn.close()


CONSOLE_TICKETS = {}
CONSOLE_TICKET_TTL = 30


@router.post("/{name}/console-ticket")
def create_console_ticket(name: str, user: dict = Depends(require_vm_privilege("vm.console"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "create_console_ticket", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        root = ET.fromstring(domain.XMLDesc(0))
        devices_el = root.find(".//devices")
        graphics = devices_el.find("graphics[@type='vnc']") if devices_el is not None else None

        if graphics is None:
            if domain.isActive():
                log_action(user["username"], "create_console_ticket", name, "echec", "no VNC (VM running)")
                raise HTTPException(
                    status_code=409,
                    detail="This VM was created before the console was added. Stop it, then start it once again to enable the console.",
                )
            ensure_vnc_graphics(conn, domain)
            log_action(user["username"], "create_console_ticket", name, "echec", "VNC added, VM stopped")
            raise HTTPException(status_code=409, detail="Console enabled on this VM: start it, then try again.")

        if not domain.isActive():
            log_action(user["username"], "create_console_ticket", name, "echec", "VM stopped")
            raise HTTPException(status_code=409, detail="The VM must be started to open a console")

        port = graphics.get("port")
        if not port or port == "-1":
            log_action(user["username"], "create_console_ticket", name, "echec", "VNC port unavailable")
            raise HTTPException(status_code=500, detail="VNC port not available yet")

        now = time.time()
        for old_ticket, (_old_vm, _old_port, old_expiry) in list(CONSOLE_TICKETS.items()):
            if old_expiry < now:
                CONSOLE_TICKETS.pop(old_ticket, None)

        ticket = secrets.token_urlsafe(24)
        CONSOLE_TICKETS[ticket] = (name, int(port), now + CONSOLE_TICKET_TTL)
        log_action(user["username"], "create_console_ticket", name, "succes")
        return {"ticket": ticket, "expire_dans_s": CONSOLE_TICKET_TTL}
    finally:
        conn.close()


@router.websocket("/{name}/console")
async def vm_console(websocket: WebSocket, name: str):
    ticket = websocket.query_params.get("ticket")
    entry = CONSOLE_TICKETS.pop(ticket, None) if ticket else None
    if entry is None:
        await websocket.close(code=4401)
        return

    vm_name, port, expiry = entry
    if vm_name != name or time.time() > expiry:
        await websocket.close(code=4401)
        return

    await websocket.accept()

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
    except OSError:
        await websocket.close(code=1011)
        return

    async def ws_to_tcp():
        try:
            while True:
                data = await websocket.receive_bytes()
                writer.write(data)
                await writer.drain()
        except (WebSocketDisconnect, RuntimeError):
            logger.debug("Ignored exception in ws_to_tcp()", exc_info=True)
        except Exception:
            logger.debug("Ignored exception in ws_to_tcp()", exc_info=True)
        finally:
            writer.close()

    async def tcp_to_ws():
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                await websocket.send_bytes(data)
        except Exception:
            logger.debug("Ignored exception in tcp_to_ws()", exc_info=True)

    task1 = asyncio.ensure_future(ws_to_tcp())
    task2 = asyncio.ensure_future(tcp_to_ws())
    _done, pending = await asyncio.wait({task1, task2}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    try:
        await websocket.close()
    except RuntimeError:
        logger.debug("Ignored exception in vm_console()", exc_info=True)


# --- Web SSH terminal (xterm.js + a remote shell through the automation key) ---
# Restricted to the admin role: the automation key connects to the VM's cloud-init
# user, which has full passwordless sudo, so opening this terminal is equivalent to
# root access.

TERMINAL_TICKETS = {}
TERMINAL_TICKET_TTL = 30


@router.post("/{name}/terminal-ticket")
def create_terminal_ticket(name: str, user: dict = Depends(require_vm_privilege("vm.console"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "create_terminal_ticket", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        if not domain.isActive():
            log_action(user["username"], "create_terminal_ticket", name, "echec", "VM stopped")
            raise HTTPException(status_code=409, detail="The VM must be started to open a terminal")

        ip = _get_ip(domain)
        if not ip:
            log_action(user["username"], "create_terminal_ticket", name, "echec", "IP inconnue")
            raise HTTPException(status_code=409, detail="VM IP address not known yet (no DHCP lease yet?)")

        ssh_user = get_vm_ssh_user(name)
        if not ssh_user:
            log_action(user["username"], "create_terminal_ticket", name, "echec", "unknown SSH user")
            raise HTTPException(
                status_code=409,
                detail=(
                    f"No known SSH user for '{name}' (VM created before this feature). "
                    "Deploy the automation key with a manual ssh-copy-id, then try again."
                ),
            )

        now = time.time()
        for old_ticket, (_old_vm, _old_ip, _old_user, old_expiry) in list(TERMINAL_TICKETS.items()):
            if old_expiry < now:
                TERMINAL_TICKETS.pop(old_ticket, None)

        ticket = secrets.token_urlsafe(24)
        TERMINAL_TICKETS[ticket] = (name, ip, ssh_user, now + TERMINAL_TICKET_TTL)
        log_action(user["username"], "create_terminal_ticket", name, "succes")
        return {"ticket": ticket, "utilisateur": ssh_user, "expire_dans_s": TERMINAL_TICKET_TTL}
    finally:
        conn.close()


@router.websocket("/{name}/terminal")
async def vm_terminal(websocket: WebSocket, name: str):
    ticket = websocket.query_params.get("ticket")
    entry = TERMINAL_TICKETS.pop(ticket, None) if ticket else None
    if entry is None:
        await websocket.close(code=4401)
        return

    vm_name, ip, ssh_user, expiry = entry
    if vm_name != name or time.time() > expiry:
        await websocket.close(code=4401)
        return

    await websocket.accept()

    private_key = get_automation_private_key_path()
    try:
        ssh_conn = await asyncssh.connect(
            ip,
            username=ssh_user,
            client_keys=[str(private_key)],
            known_hosts=None,
            connect_timeout=10,
        )
    except (asyncssh.Error, OSError) as e:
        await websocket.send_text(f"\r\n\x1b[31m[hyperlite] SSH connection to {ip} failed: {e}\x1b[0m\r\n")
        await websocket.close(code=1011)
        return

    try:
        process = await ssh_conn.create_process(term_type="xterm-256color", term_size=(80, 24))
    except asyncssh.Error as e:
        await websocket.send_text(f"\r\n\x1b[31m[hyperlite] Failed to open the shell: {e}\x1b[0m\r\n")
        ssh_conn.close()
        await websocket.close(code=1011)
        return

    async def ws_to_ssh():
        try:
            while True:
                msg = await websocket.receive_text()
                if msg.startswith("\x00"):
                    try:
                        dims = json.loads(msg[1:])
                        process.change_terminal_size(int(dims["cols"]), int(dims["rows"]))
                    except (ValueError, KeyError, TypeError):
                        logger.debug("Ignored exception in ws_to_ssh()", exc_info=True)
                else:
                    process.stdin.write(msg)
        except (WebSocketDisconnect, RuntimeError):
            logger.debug("Ignored exception in ws_to_ssh()", exc_info=True)
        except Exception:
            logger.debug("Ignored exception in ws_to_ssh()", exc_info=True)
        finally:
            try:
                process.stdin.write_eof()
            except Exception:
                logger.debug("Ignored exception in ws_to_ssh()", exc_info=True)

    async def ssh_to_ws():
        try:
            while True:
                data = await process.stdout.read(65536)
                if not data:
                    break
                await websocket.send_text(data)
        except Exception:
            logger.debug("Ignored exception in ssh_to_ws()", exc_info=True)

    task1 = asyncio.ensure_future(ws_to_ssh())
    task2 = asyncio.ensure_future(ssh_to_ws())
    _done, pending = await asyncio.wait({task1, task2}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    try:
        process.close()
    except Exception:
        logger.debug("Ignored exception in vm_terminal()", exc_info=True)
    ssh_conn.close()
    try:
        await websocket.close()
    except (RuntimeError, WebSocketDisconnect):
        logger.debug("Ignored exception in vm_terminal()", exc_info=True)
