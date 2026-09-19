import logging
import subprocess
from pathlib import Path

import libvirt
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import zfs_storage
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import (
    open_conn,
    pool_type_and_target_path,
)
from app.core.network_alloc import allocate_static_ip, generate_mac
from app.core.security import require_role
from app.core.tasks import create_task, finish_task
from app.core.unattended_install import build_seed_iso, detect_os_family, extract_casper_kernel
from app.core.vm_builder import (
    build_domain_xml,
    create_cloudinit_iso,
    create_cloudinit_reseed_iso,
    create_disk,
    create_disk_from_import,
    create_zvol_disk,
    get_or_create_automation_pubkey,
    validate_name,
    validate_username,
)
from app.core.vm_limits import validate_vm_resources
from app.core.vm_meta import (
    clear_provisioning,
    mark_provisioning,
    set_vm_auto_cleanup,
    set_vm_os_label,
    set_vm_ssh_user,
)
from app.routers.isos import ISOS_DIR
from app.routers.vm_disks import IMPORTED_DISKS_DIR
from app.routers.vms._shared import _domain_summary, router

logger = logging.getLogger(__name__)


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
