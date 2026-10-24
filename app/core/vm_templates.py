"""The templates of this node (app/routers/templates.py): made from a stopped VM, which becomes the template (Proxmox
VE's "Convert to template") or keeps running its course with the template a copy of its disk (vSphere's "Clone to
Template"); deployed as a new VM with a copy of the template's disk or a linked disk over it; renamed and deleted.
Their files (app/core/templates_store.py) are root's and hyperlite-daemon writes them (template.*,
app/daemon/ops/vm.py, docs/design/privilege-separation.md, section 13). The API keeps who may ask, the locks, the
tasks, what it records of a VM and its answers.

A template's disk is the base of its linked clones (app/core/linked_disks.py): removed or moved (a rename moves it),
they no longer start ("Cannot access backing file"), so both are refused while one exists, naming them."""

import logging
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import libvirt

from app.core import disk_space, firmware, linked_disks, templates_store, vm_records
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import refresh_pools_for_paths
from app.core.safe_paths import safe_child
from app.core.vm_builder import IMAGES_DIR, create_cloudinit_reseed_iso, generated_drives, validate_name

logger = logging.getLogger(__name__)


class TemplateError(Exception):
    """A refusal or a failure, with the daemon's kind (in_use: the 409 the routes always answered)."""

    def __init__(self, kind, detail):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def _copy(source, dest):
    """`source` (with its backing chain, flattened) written as the qcow2 image `dest`; nothing left when it fails."""
    try:
        subprocess.run(
            ["qemu-img", "convert", "-O", "qcow2", str(source), str(dest)], check=True, capture_output=True, text=True
        )
    except subprocess.CalledProcessError as e:
        Path(dest).unlink(missing_ok=True)
        logger.warning("Copy of %s to %s failed: %s", source, dest, (e.stderr or "").strip()[:300])
        raise TemplateError("failed", "Disk copy failed") from e


def check_name(name):
    """A template's new name, refused before libvirt is asked anything."""
    error = validate_name(name, "template")
    if error:
        raise TemplateError("refused", error)


def refuse_while_linked(conn, template, action):
    """`action`: "delete" or "rename", said in the refusal."""
    try:
        clones = linked_disks.dependents(templates_store.disk_path(template), conn)
    except libvirt.libvirtError as e:
        # Not known is not none: removing the disk of a template whose clones could not be listed may break them.
        logger.warning("Linked clones of the template %s not listed: %s", template, e)
        raise TemplateError(
            "unavailable", "libvirt does not answer: this template's linked clones cannot be checked"
        ) from e
    if clones:
        raise TemplateError(
            "in_use",
            f"The linked clones of this template read its disk: {', '.join(clones)}. Delete them, or move their disk "
            "to another pool (which gives them a disk of their own), before you "
            + ("delete the template" if action == "delete" else "rename it"),
        )


def make(conn, vm, template, keep_vm, created_by, ssh_user=None, os_label=None, check_only=False):
    """A template named `template` from the stopped VM `vm`; `ssh_user` and `os_label` are the VM's, which the VMs
    deployed from it take. keep_vm: the template gets a copy of the VM's disk; otherwise the VM becomes the template
    and leaves the list, its DHCP reservations released. check_only: the checks alone, None."""
    check_name(template)
    if templates_store.exists(template):
        raise TemplateError("in_use", f"A template '{template}' already exists")
    try:
        domain = conn.lookupByName(vm)
    except libvirt.libvirtError:
        raise TemplateError("not_found", f"VM '{vm}' not found") from None
    if domain.isActive():
        what = "making a template of it" if keep_vm else "converting it to a template"
        raise TemplateError("in_use", f"Stop the VM before {what}")

    xml = domain.XMLDesc(0)
    root = ET.fromstring(xml)
    vcpu = int((root.findtext("vcpu") or "1").strip())
    memory_mb = int((root.findtext("memory") or "0").strip()) // 1024
    first = root.find("./devices/disk[@device='disk']")
    disk_source = first.find("source").get("file") if first is not None and first.find("source") is not None else None
    if not disk_source:
        raise TemplateError("failed", "Source disk not found")
    # A linked clone made into a template gets a disk of its own: a template that read through another one would stop
    # working when that one is removed, and nothing would protect it.
    linked = linked_disks.backing_file(disk_source) is not None
    if keep_vm or linked:
        try:
            disk_space.ensure_room(
                templates_store.TEMPLATES_DIR, disk_space.allocated_bytes(disk_source), "this template's disk"
            )
        except disk_space.NotEnoughSpace as e:
            raise TemplateError("in_use", str(e)) from None
    if check_only:
        return None
    meta = {"ssh_user": ssh_user, "os_label": os_label}

    if keep_vm:
        # Written aside first and described last: a template being copied never shows in the list, nor one whose copy
        # failed.
        partial = templates_store.partial_disk_path(template)
        _copy(disk_source, partial)
        try:
            os.replace(partial, templates_store.disk_path(template))
            templates_store.save_template(template, xml, vcpu, memory_mb, vm, created_by, **meta)
        except OSError as e:
            partial.unlink(missing_ok=True)
            templates_store.delete_template(template)
            raise TemplateError("failed", f"Template not written: {describe_exception(e)}") from e
        return {"template": template, "vm_kept": True}

    flat = None
    if linked:
        # Written aside before anything changes: a copy that fails leaves the VM as it was.
        flat = templates_store.partial_disk_path(template)
        _copy(disk_source, flat)
    target_disk = templates_store.save_template(template, xml, vcpu, memory_mb, vm, created_by, **meta)
    try:
        # A UEFI VM's NVRAM and TPM state go with it: each deployment gets fresh ones.
        domain.undefineFlags(firmware.undefine_flags(root))
    except libvirt.libvirtError as e:
        templates_store.delete_template(template)
        if flat:
            flat.unlink(missing_ok=True)
        raise TemplateError("failed", f"Undefine failed: {describe_exception(e)}") from e
    if flat:
        os.replace(flat, target_disk)
        Path(disk_source).unlink(missing_ok=True)
    else:
        shutil.move(disk_source, str(target_disk))
    # A deployment drops the CD-ROM drives, so the VM's generated drives (cloud-init, and the unattended installation
    # media of VMs created before 1.0) would stay behind as orphans.
    drives = [path for _disk, path in generated_drives(root, vm)]
    for drive in drives:
        drive.unlink(missing_ok=True)
    # The disk left its pool behind libvirt's back: without a refresh the pool kept listing a volume that is gone.
    refresh_pools_for_paths(conn, [disk_source, target_disk, *drives])
    # Its DHCP reservations go, as after a deletion (a deployed VM gets a new MAC address); the API forgets its records.
    vm_records.release_addresses(conn, vm_records.reservations(root))
    return {"template": template, "vm_kept": False}


def deploy(conn, template, new_name, network=None, linked=False, check_only=False):
    """A new stopped VM `new_name` from `template`: a copy of its disk, or a linked disk over it (Proxmox VE's linked
    clone); `network` replaces its interfaces' network. check_only: the checks alone, None."""
    tpl = templates_store.get_template(template)
    if tpl is None:
        raise TemplateError("not_found", f"Template '{template}' not found")
    name_error = validate_name(new_name)
    if name_error:
        raise TemplateError("refused", name_error)
    try:
        conn.lookupByName(new_name)
    except libvirt.libvirtError:
        pass
    else:
        raise TemplateError("in_use", f"A VM '{new_name}' already exists")
    disk = safe_child(IMAGES_DIR, f"{new_name}.qcow2")
    if disk.exists():
        raise TemplateError("in_use", "A disk file with this name already exists")
    if linked and not linked_disks.is_qcow2(tpl["disk_path"]):
        raise TemplateError("refused", "This template's disk is not a qcow2 image: deploy it as a full copy")
    if check_only:
        return None

    if linked:
        try:
            linked_disks.create_linked(tpl["disk_path"], disk)
        except linked_disks.LinkedDiskError as e:
            disk.unlink(missing_ok=True)
            logger.warning("Linked disk %s over %s not made: %s", disk, tpl["disk_path"], e)
            raise TemplateError("failed", "Linked disk creation failed") from e
    else:
        _copy(tpl["disk_path"], disk)

    reseed_iso = None
    try:
        root = ET.fromstring(tpl["xml"])
        root.find("name").text = new_name
        uuid = root.find("uuid")
        if uuid is not None:
            root.remove(uuid)
        firmware.drop_nvram(root)
        for element in root.findall("./devices/disk[@device='disk']"):
            source = element.find("source")
            if source is not None:
                source.set("file", str(disk))
            # The new disk is a qcow2 image either way (copied with -O qcow2, or linked), whatever the template's
            # source VM had: read as raw, it would not boot.
            driver = element.find("driver")
            if driver is not None:
                driver.set("type", "qcow2")
        devices = root.find("devices")
        cloud_init = False
        for cdrom in devices.findall("disk[@device='cdrom']"):
            source = cdrom.find("source")
            cloud_init |= source is not None and (source.get("file") or "").endswith("-cloudinit.iso")
            devices.remove(cdrom)
        # A template made from a VM set up by cloud-init: the copy gets a drive with a new instance id, as a clone
        # does. Without it, it booted with the network configuration cloud-init had written for the template's MAC
        # address (netplan's `match: macaddress`), so without any address, and with its host name and SSH host keys.
        if cloud_init:
            try:
                reseed_iso = create_cloudinit_reseed_iso(new_name)
            except (OSError, subprocess.CalledProcessError):
                logger.warning("No cloud-init drive for %s deployed from %s", new_name, template, exc_info=True)
            if reseed_iso:
                cdrom = ET.SubElement(devices, "disk", {"type": "file", "device": "cdrom"})
                ET.SubElement(cdrom, "driver", {"name": "qemu", "type": "raw"})
                ET.SubElement(cdrom, "source", {"file": str(reseed_iso)})
                dev, bus = firmware.cdrom_target("hdc", firmware.of_domain(root))
                ET.SubElement(cdrom, "target", {"dev": dev, "bus": bus})
                ET.SubElement(cdrom, "readonly")
        for iface in devices.findall("interface"):
            mac = iface.find("mac")
            if mac is not None:
                iface.remove(mac)
            source = iface.find("source")
            if network and source is not None:
                source.set("network", network)
        try:
            made = conn.defineXML(ET.tostring(root, encoding="unicode"))
        except libvirt.libvirtError as e:
            raise TemplateError("failed", f"Domain definition failed: {describe_exception(e)}") from e
    except BaseException:
        disk.unlink(missing_ok=True)
        if reseed_iso:
            Path(reseed_iso).unlink(missing_ok=True)
        raise
    refresh_pools_for_paths(conn, [disk, *([reseed_iso] if reseed_iso else [])])
    return {"vm": made.name()}


def rename(conn, old, new):
    check_name(new)
    if new == old:
        raise TemplateError("refused", "The new name is the current one")
    if templates_store.exists(old):
        refuse_while_linked(conn, old, "rename")
    try:
        templates_store.rename_template(old, new)
    except FileNotFoundError:
        raise TemplateError("not_found", f"Template '{old}' not found") from None
    except FileExistsError:
        raise TemplateError("in_use", f"A template named '{new}' already exists") from None
    except OSError as e:
        raise TemplateError("failed", f"Rename failed: {describe_exception(e)}") from e


def delete(conn, name):
    if not templates_store.exists(name):
        raise TemplateError("not_found", f"Template '{name}' not found")
    refuse_while_linked(conn, name, "delete")
    templates_store.delete_template(name)
