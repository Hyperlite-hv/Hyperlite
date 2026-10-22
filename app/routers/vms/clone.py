import logging
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import libvirt
from fastapi import Depends, HTTPException
from pydantic import BaseModel

from app.core import disk_space, firmware, iscsi, maintenance, vm_locks
from app.core import permissions as perm
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import open_conn, refresh_pools_for_paths
from app.core.network_alloc import allocate_static_ip, generate_mac, release_static_ip
from app.core.safe_paths import safe_child
from app.core.security import require_vm_privilege
from app.core.tasks import answer_later, create_task
from app.core.vm_builder import (
    IMAGES_DIR,
    create_cloudinit_reseed_iso,
    validate_name,
)
from app.core.vm_meta import (
    rename_vm_os_label,
    rename_vm_ssh_user,
)
from app.routers.vms._shared import router

logger = logging.getLogger(__name__)


class CloneRequest(BaseModel):
    new_name: str


@router.post("/{name}/clone", status_code=201)
def clone_vm(
    name: str, payload: CloneRequest, user: dict = Depends(require_vm_privilege("vm.clone")), wait: bool = True
):
    """wait=false: every check is made, then the copy runs as a task and the answer is 202 with its id."""
    # A clone's disks are copied into the images directory, the "default" storage pool: space taken there, as Proxmox VE
    # checks Datastore.AllocateSpace on a clone's target storage.
    perm.refuse_unless(
        user, "storage", "default", "storage.allocate", "storage pool 'default' (where a clone is copied)"
    )
    maintenance.refuse_if_in_maintenance("local", "Cloning")
    # The source's disks are read: no restore, move or deletion may change them meanwhile.
    source = vm_locks.claim_or_409(name, "a clone")
    try:
        # Two clones to the same new name would write the same disk files (the same name is refused by the checks).
        target = None if payload.new_name == name else vm_locks.claim_or_409(payload.new_name, "a clone")
    except BaseException:
        source.release()
        raise

    def release():
        if target is not None:
            target.release()
        source.release()

    if wait:
        try:
            return _clone_vm(name, payload, user)
        finally:
            release()
    try:
        conn = open_conn()
        try:
            _check_clone(conn, name, payload, user)
        finally:
            conn.close()
        task_id = create_task("clone_vm", name, node=None, username=user["username"])
    except BaseException:
        release()
        raise

    def work():
        try:
            _clone_vm(name, payload, user, task_id)
        finally:
            release()

    return answer_later(task_id, work, name=payload.new_name)


def _clone_vm(name, payload, user, task_id=None):
    # Real bugs fixed in the cloning logic after an audit:
    # 1. SECURITY: there was no permission check (just get_current_user), so any
    #    account, even an "viewer" (read-only everywhere else), could clone and
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
    #    an ISO, no guest customization is possible,
    #    the same limit as a hypervisor without a guest agent, documented in the
    #    response rather than silently ignored.
    conn = open_conn()
    if task_id is None:
        task_id = create_task("clone_vm", name, node=None, username=user["username"])
    new_disk_paths = []
    reseed_iso = None
    new_ip_reservations = []
    defined = False
    try:
        root, disk_els = _check_clone(conn, name, payload, user, task_id)

        # Clone ALL the disks (not only the first one, see the note above).
        for i, disk_el in enumerate(disk_els):
            source_el = disk_el.find("source")
            source_path = source_el.get("file") if source_el is not None else None
            if not source_path:
                for p in new_disk_paths:
                    Path(p).unlink(missing_ok=True)
                log_action(user["username"], "clone_vm", name, "failed", "source disk path not found", task_id=task_id)
                raise HTTPException(status_code=500, detail="Source disk path not found")

            suffix = "" if i == 0 else f"-{i + 1}"
            new_disk_path = safe_child(IMAGES_DIR, f"{payload.new_name}{suffix}.qcow2")
            if new_disk_path.exists():
                for p in new_disk_paths:
                    Path(p).unlink(missing_ok=True)
                log_action(
                    user["username"],
                    "clone_vm",
                    name,
                    "failed",
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
                # The file being written too: a copy that stopped on a full disk left it there, filling it.
                for p in [*new_disk_paths, new_disk_path]:
                    Path(p).unlink(missing_ok=True)
                msg = f"disk copy: {exc.stderr or exc}"
                log_action(user["username"], "clone_vm", name, "failed", msg, task_id=task_id)
                raise HTTPException(status_code=500, detail="Disk copy failed") from exc

            new_disk_paths.append(str(new_disk_path))
            source_el.set("file", str(new_disk_path))
            # The copy is qcow2 whatever the source's format: a raw source (a VM defined before Hyperlite managed the
            # node) kept its raw driver, and the clone read the qcow2 file as a raw disk.
            driver_el = disk_el.find("driver")
            if driver_el is None:
                driver_el = ET.SubElement(disk_el, "driver", {"name": "qemu"})
            driver_el.set("type", "qcow2")

        name_el = root.find("name")
        if name_el is not None:
            name_el.text = payload.new_name

        uuid_el = root.find("uuid")
        if uuid_el is not None:
            root.remove(uuid_el)  # libvirt generates a new, distinct one at defineXML
        # A UEFI source: the clone gets its own NVRAM (and, with its new UUID, its own TPM state).
        firmware.drop_nvram(root)

        devices_el = root.find(".//devices")
        if devices_el is not None:
            for disk in list(devices_el.findall("disk")):
                if disk.get("device") == "cdrom":
                    devices_el.remove(disk)

        # Explicit MAC (rather than letting libvirt pick one at random): it allows
        # reserving a fixed IP for the clone right away, as when a VM is created (see
        # create_vm / network_alloc.py). Best-effort: a failed reservation does not block
        # the cloning, only the fixed IP.
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
        if (safe_child(IMAGES_DIR, f"{name}-cloudinit.iso")).exists():
            try:
                reseed_iso = create_cloudinit_reseed_iso(payload.new_name)
                cdrom_el = ET.SubElement(devices_el, "disk", {"type": "file", "device": "cdrom"})
                ET.SubElement(cdrom_el, "driver", {"name": "qemu", "type": "raw"})
                ET.SubElement(cdrom_el, "source", {"file": str(reseed_iso)})
                dev, bus = firmware.cdrom_target("hdc", firmware.of_domain(root))
                ET.SubElement(cdrom_el, "target", {"dev": dev, "bus": bus})
                ET.SubElement(cdrom_el, "readonly")
            except subprocess.CalledProcessError:
                reseed_iso = None  # too bad for the customization, the clone remains functional

        new_xml = ET.tostring(root, encoding="unicode")

        try:
            new_domain = conn.defineXML(new_xml)
        except libvirt.libvirtError as exc:
            _undo(conn, new_disk_paths, reseed_iso, new_ip_reservations)
            msg = describe_exception(exc)
            log_action(user["username"], "clone_vm", name, "failed", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Clone definition failed: {msg}") from exc
        defined = True  # from here its disks are the clone's

        refresh_pools_for_paths(conn, [*new_disk_paths, reseed_iso])
        rename_vm_ssh_user(name, payload.new_name)
        rename_vm_os_label(name, payload.new_name)
        log_action(user["username"], "clone_vm", name, "success", f"clone -> {payload.new_name}", task_id=task_id)
        return {
            "source": name,
            "clone": new_domain.name(),
            "state": "stopped",
            "guest_customization": reseed_iso is not None,
        }
    except HTTPException:
        raise
    except Exception as exc:  # never a running task, nor copied disks left behind
        if not defined:
            _undo(conn, new_disk_paths, reseed_iso, new_ip_reservations)
        log_action(user["username"], "clone_vm", name, "failed", describe_exception(exc), task_id=task_id)
        raise
    finally:
        conn.close()


def _check_clone(conn, name, payload, user, task_id=None):
    """Every refusal a clone can meet before its first byte is copied, recorded on its task when it has one:
    (its parsed XML, its disk elements)."""
    try:
        domain = conn.lookupByName(name)
    except libvirt.libvirtError:
        log_action(user["username"], "clone_vm", name, "failed", "Source VM not found", task_id=task_id)
        raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
    iscsi.refuse_if_iscsi(domain, "Cloning")

    name_error = validate_name(payload.new_name)
    if name_error:
        log_action(user["username"], "clone_vm", name, "failed", f"invalid name: {payload.new_name}", task_id=task_id)
        raise HTTPException(status_code=422, detail=name_error)

    try:
        conn.lookupByName(payload.new_name)
        log_action(
            user["username"], "clone_vm", name, "failed", f"'{payload.new_name}' already exists", task_id=task_id
        )
        raise HTTPException(status_code=409, detail=f"A VM '{payload.new_name}' already exists")
    except libvirt.libvirtError:
        logger.debug("Ignored exception in clone_vm()", exc_info=True)

    if domain.isActive():
        log_action(user["username"], "clone_vm", name, "failed", "VM active", task_id=task_id)
        raise HTTPException(status_code=409, detail="Stop the VM before cloning it")

    root = ET.fromstring(domain.XMLDesc(0))

    disk_els = [d for d in root.findall(".//devices/disk") if d.get("device") == "disk"]
    if not disk_els:
        log_action(user["username"], "clone_vm", name, "failed", "source disk not found", task_id=task_id)
        raise HTTPException(status_code=500, detail="Source disk not found")

    # Every copy lands in the images directory, on the system disk by default: refused before the first byte when
    # it would not fit (app/core/disk_space.py), rather than filling it hours later.
    sources = [d.find("source").get("file") for d in disk_els if d.find("source") is not None]
    try:
        disk_space.ensure_room(IMAGES_DIR, sum(disk_space.allocated_bytes(p) for p in sources if p), "this clone")
    except disk_space.NotEnoughSpace as e:
        log_action(user["username"], "clone_vm", name, "failed", str(e), task_id=task_id)
        raise HTTPException(status_code=409, detail=str(e)) from None
    return root, disk_els


def _undo(conn, disk_paths, reseed_iso, ip_reservations):
    """What a failed clone made: its disks, its cloud-init ISO and its address reservations."""
    for p in disk_paths:
        Path(p).unlink(missing_ok=True)
    if reseed_iso:
        Path(reseed_iso).unlink(missing_ok=True)
    for iface_network, mac in ip_reservations:
        try:
            release_static_ip(conn, iface_network, mac)
        except libvirt.libvirtError:
            logger.debug("Ignored exception in clone_vm()", exc_info=True)
