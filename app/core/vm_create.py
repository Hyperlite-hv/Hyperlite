"""A VM made on this node (POST /vms, app/routers/vms/create.py): its checks, its disks in the pool chosen (files,
zvols, logical volumes or iSCSI LUNs), its cloud-init drive, its DHCP reservation and its definition, written from its
checked specification (app/core/vm_builder.py). hyperlite-daemon runs it (app/daemon/ops/vm.py,
docs/design/privilege-separation.md, section 13): the checks read the libraries and the pools, and the disks are
written, as root. The API keeps who may ask, the lock, the task, what it records in its database and its answers.

A refusal or a failure is a CreateError, whose kind is the daemon's; the checks' refusals are one list, as the route
always answered them."""

import json
import logging
import subprocess
from pathlib import Path
from typing import Literal

import libvirt
from pydantic import BaseModel, Field

from app.core import cloudinit_edit, disk_images, disk_library, firmware, lvm_storage, zfs_storage
from app.core.error_messages import describe_exception
from app.core.guest_hardware import guest_profile
from app.core.libvirt_utils import (
    ensure_default_pool,
    get_disk_paths_in_use,
    pool_type_and_target_path,
    refresh_pools_for_paths,
)
from app.core.network_alloc import allocate_static_ip, generate_mac
from app.core.safe_paths import safe_child
from app.core.vm_builder import (
    BASE_IMAGE,
    IMAGES_DIR,
    build_domain_xml,
    create_cloudinit_iso,
    create_cloudinit_reseed_iso,
    create_disk,
    create_disk_from_import,
    create_zvol_disk,
    disk_file,
    ensure_base_image,
    get_or_create_automation_pubkey,
    validate_name,
    validate_username,
)
from app.core.vm_limits import validate_vm_resources

logger = logging.getLogger(__name__)


class CreateError(Exception):
    """A refusal or a failure, with the daemon's kind; `detail` a sentence, or the list of the checks' refusals."""

    def __init__(self, kind, detail):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


class StaticInterface(BaseModel):
    # The interface, by its MAC address; absent: the VM's first interface.
    mac: str | None = Field(default=None, max_length=17)
    addresses: list[str] = Field(min_length=1, max_length=16)  # with their prefix: "10.0.20.11/24", "2001:db8::5/64"
    gateway: str | None = Field(default=None, max_length=45)


class CloudInitCustom(BaseModel):
    """The rest of a guest's customisation, as vSphere's and Proxmox VE's (cloudinit_edit.CUSTOM). A field left out
    keeps what the drive holds; an empty value ("" or []) clears it."""

    hostname: str | None = Field(default=None, max_length=63)  # the VM's name when empty
    domain: str | None = Field(default=None, max_length=253)
    network: list[StaticInterface] | None = Field(default=None, max_length=16)  # the interfaces not listed use DHCP
    dns: list[str] | None = Field(default=None, max_length=8)
    dns_search: list[str] | None = Field(default=None, max_length=8)
    user_data: str | None = Field(default=None, max_length=16 * 1024)  # a cloud-config added to Hyperlite's own


class DiskSpec(BaseModel):
    size_gb: int = Field(ge=1)
    # iSCSI pool only: the LUN (volume name in the pool, e.g. "unit:0:0:1") this disk takes whole; size_gb is then
    # only its size, for the resource checks.
    lun: str | None = Field(None, max_length=200)
    # A data disk (not the first) made from a file uploaded through POST /vm-disks, as the other disks of an OVA
    # (app/core/ova.py); size_gb is then the file's own size. The first disk's is VMCreate.import_disk.
    import_disk: str | None = Field(None, max_length=255)


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
    drivers_iso: str | None = None
    # Node whose library holds `iso` / `drivers_iso`, when it is not this host's. A VM boots only from an image
    # on its own host, so such an image is first copied here (a copy_iso task), then the VM is created.
    iso_node: str | None = None
    drivers_iso_node: str | None = None
    guest_os: Literal["auto", "windows", "linux", "other"] = "auto"
    disk_controller: Literal["auto", "sata", "virtio-scsi"] = "auto"
    # "bios" (the historical default), "uefi", or "uefi_secure" (UEFI + Secure Boot + TPM 2.0, what Windows 11
    # requires). See app/core/firmware.py.
    firmware: Literal["bios", "uefi", "uefi_secure"] = "bios"
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
    # iSCSI pool: the chosen LUNs are overwritten (system image, or their start wiped for an ISO installation, so
    # the VM never boots what they held). Required as an explicit confirmation.
    erase_luns: bool = False
    # Automatic deletion of inactive VMs: opt-in, None/absent = never enabled
    # (unchanged behaviour by default). The counter only runs while the VM is
    # STOPPED (see touch_vm_activity, called on every start): a VM that runs
    # continuously is never considered "inactive", whatever the threshold.
    auto_cleanup_days: int | None = Field(None, ge=1, le=365)
    # The pool (app/routers/pools.py, by its id) the VM goes in. Required from an account that is not an
    # administrator, which creates VMs only in a pool where it holds vm.allocate, as vSphere requires a resource pool:
    # the pool's assignments then give it its rights on the new VM.
    resource_pool: int | None = None
    # A VM made from the cloud image only (no ISO, no imported disk): the account's SSH keys, and the rest of the
    # guest's customisation (host name, domain, a static address, resolvers, a cloud-config of the user's), written on
    # its cloud-init drive before its first boot, as PUT /vms/{name}/cloud-init writes them later.
    ssh_keys: list[str] = Field(default_factory=list, max_length=50)
    customize: CloudInitCustom | None = None


def resolve_luns(conn, pool, disks, erase_luns):
    """([(device path, capacity in bytes)], errors) for a VM on an iSCSI pool: each disk names a free LUN of the
    pool, used whole (see app/core/iscsi.py)."""
    errors = []
    if not erase_luns:
        errors.append("Creating a VM on iSCSI LUNs overwrites them: confirm with erase_luns")
    try:
        pool.refresh(0)
    except libvirt.libvirtError:
        logger.debug("iSCSI pool refresh failed", exc_info=True)
    in_use = get_disk_paths_in_use(conn)
    luns = []
    for i, disk in enumerate(disks, start=1):
        if not disk.lun:
            errors.append(f"Disk {i}: choose a LUN of the iSCSI pool")
            continue
        try:
            vol = pool.storageVolLookupByName(disk.lun)
        except libvirt.libvirtError:
            errors.append(f"Disk {i}: LUN '{disk.lun}' not found in the pool")
            continue
        path = vol.path()
        if path in in_use:
            errors.append(f"Disk {i}: LUN '{disk.lun}' is already used by a VM")
        elif any(path == p for p, _ in luns):
            errors.append(f"Disk {i}: LUN '{disk.lun}' is chosen twice")
        else:
            luns.append((path, vol.info()[1]))
    return luns, errors


def prepare_lun(device, capacity, source=None):
    """Write the system image (Debian cloud image or an imported disk) to a LUN, or wipe its first 16 MB (partition
    tables, boot sector) for an ISO installation or a data disk, so the VM never boots what the LUN held before."""
    if source is None:
        subprocess.run(
            ["dd", "if=/dev/zero", f"of={device}", "bs=1M", "count=16", "conv=fsync"],
            check=True,
            capture_output=True,
            text=True,
        )
        return
    info = subprocess.run(
        ["qemu-img", "info", "--output=json", str(source)], check=True, capture_output=True, text=True
    )
    needed = json.loads(info.stdout)["virtual-size"]
    if capacity and needed > capacity:
        raise ValueError(
            f"The LUN is too small for this system image: {needed / 1024**3:.1f} GB needed, {capacity / 1024**3:.1f} GB available"
        )
    subprocess.run(
        ["qemu-img", "convert", "-O", "raw", str(source), device], check=True, capture_output=True, text=True
    )


# The MAC address of the one interface a VM is created with is chosen once its creation runs: the checks before use this
# one in its place (an entry of customize.network names no MAC address at creation).
PLACEHOLDER_MAC = "52:54:00:00:00:00"


def customisation(payload, mac):
    """(SSH keys, customisation) of a VM made from the cloud image, checked (raises cloudinit_edit.CloudInitError)."""
    custom = payload.customize.model_dump(exclude_none=True) if payload.customize else {}
    if any(entry.get("mac") for entry in custom.get("network", [])):
        raise cloudinit_edit.CloudInitError(
            "customize.network: a VM is created with one interface, whose MAC address is not known yet: leave mac out"
        )
    # The account's name is checked with the other fields of the creation; any valid name stands in for it here.
    keys = cloudinit_edit.validate("user", None, payload.ssh_keys)
    return keys, cloudinit_edit.validate_custom(custom, [mac])


def create(conn, payload, pending=frozenset(), check_only=False):
    """Check the VM's specification, then make it. pending: image names that are not here yet but are being
    brought (they pass the existence check). check_only: run every check, make nothing, return None. Returns what the
    API records: {"needs_account", "import_mode", "install_mode", "customised"}."""
    errors = []
    name_error = validate_name(payload.name)
    if name_error:
        errors.append(name_error)
    errors.extend(validate_vm_resources(payload.vcpu, payload.memory_mb, [d.size_gb for d in payload.disks]))

    iso_path = None
    if payload.iso:
        candidate = safe_child(disk_library.ISOS, payload.iso)
        if not candidate.exists() and payload.iso not in pending:
            errors.append(f"ISO '{payload.iso}' not found")
        else:
            iso_path = candidate

    drivers_iso_path = None
    if payload.drivers_iso:
        if not payload.iso or payload.import_disk:
            raise CreateError("refused", "Driver media requires an installation ISO")
        if (
            Path(payload.drivers_iso).name != payload.drivers_iso
            or "\\" in payload.drivers_iso
            or not payload.drivers_iso.lower().endswith(".iso")
        ):
            raise CreateError("refused", "Invalid driver ISO name")
        candidate = safe_child(disk_library.ISOS, payload.drivers_iso)
        if not candidate.is_file() and payload.drivers_iso not in pending:
            raise CreateError("refused", f"Driver ISO '{payload.drivers_iso}' not found")
        drivers_iso_path = candidate

    extra_imports = {}
    for i, disk in enumerate(payload.disks):
        if not disk.import_disk:
            continue
        if i == 0:
            errors.append("The first disk's imported file is given as import_disk")
            continue
        try:
            candidate = safe_child(disk_library.IMPORTS, disk.import_disk)
        except ValueError:
            errors.append(f"Disk {i + 1}: invalid imported disk name")
            continue
        if not candidate.is_file():
            errors.append(f"Disk {i + 1}: imported disk '{disk.import_disk}' not found")
        else:
            try:
                disk_images.check(candidate)  # it names no other file to read (a backing file...)
            except disk_images.UnsafeImage as e:
                errors.append(f"Disk {i + 1}: {e}")
            extra_imports[i] = candidate

    import_disk_path = None
    if payload.import_disk:
        if payload.iso:
            errors.append("Cannot combine a disk import with an installation ISO")
        try:
            candidate = safe_child(disk_library.IMPORTS, payload.import_disk)
        except ValueError:
            errors.append("Invalid imported disk name")
        else:
            if candidate.exists():
                try:
                    disk_images.check(candidate)  # it names no other file to read (a backing file...)
                except disk_images.UnsafeImage as e:
                    errors.append(str(e))
                import_disk_path = candidate
            else:
                errors.append(f"Imported disk '{payload.import_disk}' not found")

    # "Installation from an ISO" mode: a blank system disk, and the operating system is installed by hand from the
    # console, as on Proxmox VE: the user creates their own account there (no web SSH terminal until access is
    # configured by hand). Hyperlite no longer answers the installers itself (Kickstart, autoinstall): the cloud
    # image below is the one system it prepares. Without an ISO: the preinstalled Debian 12 image + cloud-init.
    install_mode = iso_path is not None
    profile = guest_profile(payload.iso if install_mode else None, payload.guest_os)
    if import_disk_path is not None and payload.guest_os != "auto":
        # An imported disk holds a system Hyperlite does not know (an OVA names it, app/core/ova.py): the family asked
        # for sets its devices. A Windows guest without VirtIO drivers does not boot from a VirtIO disk.
        profile = payload.guest_os
    compatible_devices = profile in {"windows", "other"}
    disk_bus = (
        "sata"
        if payload.disk_controller == "sata" or (payload.disk_controller == "auto" and compatible_devices)
        else "scsi"
    )
    # "Disk import" mode: the disk already has its own OS and accounts, so there is
    # neither cloud-init nor credentials to ask for at creation (see
    # restore_backup(mode='new') in app/core/backups.py, the same principle already
    # used for backup restore).
    import_mode = import_disk_path is not None
    needs_account = not install_mode and not import_mode
    if needs_account:
        username_error = validate_username(payload.username or "")
        if username_error:
            errors.append(username_error)
        if len(payload.password or "") < 4:
            errors.append("The password must contain at least 4 characters")
    if payload.ssh_keys or payload.customize is not None:
        if not needs_account:
            errors.append(
                "ssh_keys and customize apply to a VM made from the cloud image, not to an ISO installation or an "
                "imported disk, which keep their own system's settings"
            )
        else:
            try:
                customisation(payload, PLACEHOLDER_MAC)
            except cloudinit_edit.CloudInitError as e:
                errors.append(str(e))

    try:
        conn.lookupByName(payload.name)
        errors.append(f"A VM named '{payload.name}' already exists")
    except libvirt.libvirtError:
        logger.debug("Ignored exception in create_vm()", exc_info=True)

    try:
        conn.networkLookupByName(payload.network)
    except libvirt.libvirtError:
        errors.append(f"Network '{payload.network}' not found")

    firmware_error = firmware.check_choice(conn, payload.firmware)
    if firmware_error:
        errors.append(firmware_error)

    # Resolution of the chosen storage pool: None/absent = the 'default' pool, made here when a fresh node has
    # not defined it yet (the disks go to its directory, and are read back as its volumes). Restricted to the 'dir'/'netfs'
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
    iscsi_pool = None
    lvm_pool = None
    if not payload.storage_pool or payload.storage_pool == "default":
        try:
            ensure_default_pool(conn)
        except libvirt.libvirtError as e:
            errors.append(f"The 'default' storage pool cannot be made: {describe_exception(e)}")
    elif zfs_storage.pool_exists(payload.storage_pool):
        zfs_pool_name = payload.storage_pool
    else:
        try:
            pool = conn.storagePoolLookupByName(payload.storage_pool)
        except libvirt.libvirtError:
            errors.append(f"Storage pool '{payload.storage_pool}' not found")
            pool = None
        if pool is not None:
            if not pool.isActive():
                errors.append(f"Storage pool '{payload.storage_pool}' is not active")
            else:
                pool_type, pool_path = pool_type_and_target_path(pool)
                if pool_type == "iscsi":
                    iscsi_pool = pool
                elif pool_type == "logical":
                    # LVM: each disk a logical volume, attached as a raw block device as zvols are.
                    lvm_pool = pool
                elif pool_type not in ("dir", "netfs"):
                    errors.append(
                        f"Storage pool '{payload.storage_pool}' of type '{pool_type}' is not supported for VM creation (dir/netfs/iscsi/lvm only)"
                    )
                elif not pool_path:
                    errors.append(f"Storage pool '{payload.storage_pool}': its path cannot be read")
                else:
                    target_dir = Path(pool_path)

    luns = []
    if iscsi_pool is not None:
        luns, lun_errors = resolve_luns(conn, iscsi_pool, payload.disks, payload.erase_luns)
        errors.extend(lun_errors)

    if zfs_pool_name is None and iscsi_pool is None and lvm_pool is None:
        for i in range(len(payload.disks)):
            path = disk_file(target_dir or IMAGES_DIR, payload.name, i)
            if path.exists():  # another VM's disk, maybe: never written over (GHSA-vfmj-3v3j-48fq)
                errors.append(f"A disk file '{path.name}' already exists in this storage pool: choose another VM name")

    if errors:
        raise CreateError("refused", errors)
    if check_only:
        return None

    # Fixed IP per VM (see app/core/network_alloc.py): a DHCP reservation on the
    # libvirt network for a MAC known in advance, with no change to the
    # guest's configuration (still plain DHCP on the guest side).
    # Best-effort: a failure here must not prevent the VM from being created, it only
    # deprives it of a fixed IP (the usual DHCP behaviour as a fallback).
    mac = generate_mac(conn)
    try:
        allocate_static_ip(conn, payload.network, mac)
    except libvirt.libvirtError:
        logger.debug("Ignored exception in create_vm()", exc_info=True)

    try:
        if luns and extra_imports:
            raise ValueError("Imported data disks are not written to iSCSI LUNs: choose another storage pool")
        if luns:
            # iSCSI: each disk is a whole LUN, attached as a raw block device like a zvol. Disk 0 receives the
            # system image unless an ISO installs one; the others are blank data disks.
            if import_mode:
                system_source = import_disk_path
            elif install_mode:
                system_source = None
            else:
                ensure_base_image()
                system_source = BASE_IMAGE
            for i, (device, capacity) in enumerate(luns):
                prepare_lun(device, capacity, system_source if i == 0 else None)
            disk_paths = [(device, "block") for device, _ in luns]
        elif zfs_pool_name:
            # Raw zvols: each path is a /dev/zvol/... device marked 'block' for
            # build_domain_xml (see create_zvol_disk in vm_builder.py), never a qcow2 file
            # path.
            if import_mode:
                disk_paths = [
                    (create_zvol_disk(zfs_pool_name, payload.name, 0, 1, import_source=import_disk_path), "block")
                ] + [
                    (
                        create_zvol_disk(
                            zfs_pool_name, payload.name, i, disk.size_gb, import_source=extra_imports.get(i)
                        ),
                        "block",
                    )
                    for i, disk in enumerate(payload.disks[1:], start=1)
                ]
            else:
                disk_paths = [
                    (
                        create_zvol_disk(
                            zfs_pool_name,
                            payload.name,
                            i,
                            disk.size_gb,
                            blank=(install_mode and i == 0),
                            import_source=extra_imports.get(i),
                        ),
                        "block",
                    )
                    for i, disk in enumerate(payload.disks)
                ]
        elif lvm_pool is not None:
            # Thick logical volumes made through libvirt, or thin ones in the pool's thin pool, the image written
            # raw into disk 0 (app/core/lvm_storage.py).
            thin = lvm_storage.thin_pool_of(lvm_storage.group_of(lvm_pool))
            if import_mode:
                system_source = import_disk_path
            elif install_mode:
                system_source = None
            else:
                ensure_base_image()
                system_source = BASE_IMAGE
            disk_paths = [
                (
                    lvm_storage.create_vm_disk(
                        lvm_pool,
                        payload.name,
                        i,
                        disk.size_gb,
                        base_image=system_source if i == 0 and not import_mode else None,
                        import_source=import_disk_path if i == 0 and import_mode else extra_imports.get(i),
                        thin_pool=thin["name"] if thin else None,
                    ),
                    "block",
                )
                for i, disk in enumerate(payload.disks)
            ]
        elif import_mode:
            # Disk 0 = conversion of the imported file (qemu-img detects the source format by
            # itself); any additional disks are always blank as usual.
            disk_paths = [create_disk_from_import(payload.name, import_disk_path, target_dir=target_dir)] + [
                create_disk_from_import(payload.name, extra_imports[i], target_dir=target_dir, index=i)
                if i in extra_imports
                else create_disk(payload.name, disk.size_gb, index=i, target_dir=target_dir)
                for i, disk in enumerate(payload.disks[1:], start=1)
            ]
        else:
            disk_paths = [
                create_disk_from_import(payload.name, extra_imports[i], target_dir=target_dir, index=i)
                if i in extra_imports
                else create_disk(
                    payload.name, disk.size_gb, index=i, blank=(install_mode and i == 0), target_dir=target_dir
                )
                for i, disk in enumerate(payload.disks)
            ]
        cloudinit_path = None
        customised = None
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
            if payload.ssh_keys or payload.customize is not None:
                # The drive just made, rewritten with the rest before the VM ever boots (the same writer as PUT
                # /vms/{name}/cloud-init, so that route reads it back).
                keys, custom = customisation(payload, mac)
                cloudinit_edit.write_drive(
                    payload.name, Path(cloudinit_path), payload.username, payload.password, keys, custom, [mac]
                )
                customised = {"keys": keys, "custom": custom}
    except subprocess.CalledProcessError as e:
        raise CreateError("failed", f"Error while preparing the disk/cloud-init: {e.stderr or e}") from e
    except zfs_storage.ZfsError as e:
        raise CreateError("failed", f"ZFS error while preparing the disk: {e.message}") from e
    except (lvm_storage.LvmError, libvirt.libvirtError) as e:
        # LVM: a full volume group, or a volume name already taken in it.
        msg = describe_exception(e) if isinstance(e, libvirt.libvirtError) else str(e)
        raise CreateError("failed", f"Error while preparing the disk: {msg}") from e
    except ValueError as e:
        raise CreateError("refused", str(e)) from e

    xml = build_domain_xml(
        payload.name,
        payload.vcpu,
        payload.memory_mb,
        disk_paths,
        cloudinit_path,
        payload.network,
        iso_path=iso_path,
        drivers_iso_path=drivers_iso_path,
        mac=mac,
        disk_bus=disk_bus,
        interface_model="e1000e" if compatible_devices else "virtio",
        firmware=payload.firmware,
    )
    conn.defineXML(xml)
    # The disks and ISOs were written by qemu-img/cloud-localds, not through libvirt:
    # without a refresh their pool does not list them.
    refresh_pools_for_paths(conn, [*disk_paths, cloudinit_path])
    return {
        "needs_account": needs_account,
        "import_mode": import_mode,
        "install_mode": install_mode,
        # The drive's customisation, for the API to keep in its database (cloudinit_edit.save_state).
        "customised": customised,
    }
