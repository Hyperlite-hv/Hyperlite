import logging
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import libvirt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import disk_space, firmware, maintenance, templates_store, vm_locks, vm_meta, vm_records
from app.core.audit import log_action
from app.core.libvirt_utils import open_conn, refresh_pools_for_paths
from app.core.listing import listing
from app.core.safe_paths import safe_child
from app.core.security import get_current_user, require_role
from app.core.tasks import answer_later, create_task
from app.core.vm_builder import IMAGES_DIR, create_cloudinit_reseed_iso, validate_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("")
def list_templates(user: dict = Depends(get_current_user)):
    return listing(templates_store.list_templates())


class ConvertRequest(BaseModel):
    template_name: str | None = None
    # True: the template gets a copy of the VM's disk and the VM stays, as vSphere's "Clone to Template". False (the
    # default, as before): the VM becomes the template and leaves the list, as Proxmox VE's "Convert to template".
    keep_vm: bool = False


@router.post("/from-vm/{name}", status_code=201)
def convert_to_template(
    name: str, payload: ConvertRequest, user: dict = Depends(require_role("admin")), wait: bool = True
):
    """wait=false: every check is made, then the work runs as a task and the answer is 202 with its id."""
    claim = vm_locks.claim_or_409(name, "a copy to a template" if payload.keep_vm else "a conversion to a template")
    try:
        _convert_to_template(name, payload, user, check_only=True)
        # A task as for a clone: a copy to a template takes minutes, and shows in Tasks while it runs.
        action = "clone_to_template" if payload.keep_vm else "convert_to_template"
        task_id = create_task(action, name, node=None, username=user["username"])
    except BaseException:
        claim.release()
        raise
    work = vm_locks.released_after(claim, lambda: _convert_to_template(name, payload, user, task_id=task_id))
    if wait:
        return work()
    return answer_later(task_id, work, name=(payload.template_name or name).strip())


def _convert_to_template(name, payload, user, check_only=False, task_id=None):
    """check_only: run every check, change nothing, return None. task_id: the task the outcome is recorded on."""
    tpl_name = (payload.template_name or name).strip()
    name_error = validate_name(tpl_name, "template")
    if name_error:
        raise HTTPException(status_code=422, detail=name_error)
    if templates_store.exists(tpl_name):
        raise HTTPException(status_code=409, detail=f"A template '{tpl_name}' already exists")

    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "convert_to_template", name, "failed", "VM not found", task_id=task_id)
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        action = "clone_to_template" if payload.keep_vm else "convert_to_template"
        if domain.isActive():
            log_action(user["username"], action, name, "failed", "VM active", task_id=task_id)
            what = "making a template of it" if payload.keep_vm else "converting it to a template"
            raise HTTPException(status_code=409, detail=f"Stop the VM before {what}")

        xml_desc = domain.XMLDesc(0)
        root = ET.fromstring(xml_desc)
        vcpu = int((root.findtext("vcpu") or "1").strip())
        memory_kib = int((root.findtext("memory") or "0").strip())
        memory_mb = memory_kib // 1024

        disk_source = None
        for disk in root.findall(".//devices/disk"):
            if disk.get("device") == "disk":
                src = disk.find("source")
                if src is not None:
                    disk_source = src.get("file")
                break
        if not disk_source:
            log_action(user["username"], action, name, "failed", "disk not found", task_id=task_id)
            raise HTTPException(status_code=500, detail="Source disk not found")

        # The account and the system of the VM go with its disk: a VM deployed from the template needs them for the
        # web terminal (seen on a real node: an ISO-installed RHEL deployed from its template had no SSH user).
        meta = {"ssh_user": vm_meta.get_vm_ssh_user(name), "os_label": vm_meta.get_vm_os_label(name)}
        if payload.keep_vm:
            try:
                disk_space.ensure_room(
                    templates_store.TEMPLATES_DIR, disk_space.allocated_bytes(disk_source), "this template's disk"
                )
            except disk_space.NotEnoughSpace as e:
                log_action(user["username"], action, name, "failed", str(e), task_id=task_id)
                raise HTTPException(status_code=409, detail=str(e)) from None
        if check_only:
            return None
        if payload.keep_vm:
            return _copy_to_template(name, tpl_name, xml_desc, vcpu, memory_mb, disk_source, user, meta, task_id)

        target_disk = templates_store.save_template(tpl_name, xml_desc, vcpu, memory_mb, name, user["username"], **meta)

        try:
            # A UEFI VM's NVRAM and TPM state go with it: each deployment gets fresh ones (see deploy below).
            domain.undefineFlags(firmware.undefine_flags(root))
        except libvirt.libvirtError as exc:
            templates_store.delete_template(tpl_name)
            log_action(user["username"], "convert_to_template", name, "failed", str(exc), task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Undefine failed: {exc}") from exc

        shutil.move(disk_source, str(target_disk))
        # Deploying from a template drops the CD-ROM devices, so the helper ISOs of the source VM
        # (cloud-init seed, and the unattended installation media of VMs created before 1.0) would stay behind
        # as orphans.
        helper_isos = [
            safe_child(IMAGES_DIR, f"{name}{suffix}")
            for suffix in ("-cloudinit.iso", "-oemdrv.iso", "-autoinstall.iso")
        ]
        for iso in helper_isos:
            iso.unlink(missing_ok=True)
        # The disk left its pool behind libvirt's back: without a refresh the pool keeps listing a volume that is gone.
        refresh_pools_for_paths(conn, [disk_source, target_disk, *helper_isos])
        # The VM is gone, as after a deletion: its DHCP reservations (a deployed VM gets a new MAC address) and every
        # record of it (rights, pools, backup schedule, which would fail at every run...) go; the template keeps its
        # SSH user and system.
        vm_records.release_addresses(conn, vm_records.reservations(root))
        vm_records.forget(name)

        log_action(user["username"], "convert_to_template", name, "success", f"template -> {tpl_name}", task_id=task_id)
        return {"template": tpl_name, "vm_source": name, "vm_kept": False}
    finally:
        conn.close()


def _copy_to_template(name, tpl_name, xml_desc, vcpu, memory_mb, disk_source, user, meta, task_id=None):
    """The template gets a copy of the VM's disk, the VM stays as it is. The VM is stopped, so the copy is consistent.
    The copy is written aside first and the template's description last: a template being copied never shows in the
    list, nor one whose copy failed. The room for the copy was checked by the caller."""
    partial = templates_store.partial_disk_path(tpl_name)
    try:
        subprocess.run(
            ["qemu-img", "convert", "-O", "qcow2", disk_source, str(partial)],
            check=True,
            capture_output=True,
            text=True,
        )
        os.replace(partial, templates_store.disk_path(tpl_name))
    except (subprocess.CalledProcessError, OSError) as exc:
        partial.unlink(missing_ok=True)
        detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        log_action(user["username"], "clone_to_template", name, "failed", f"disk copy: {detail}", task_id=task_id)
        raise HTTPException(status_code=500, detail="Disk copy failed") from exc
    try:
        templates_store.save_template(tpl_name, xml_desc, vcpu, memory_mb, name, user["username"], **meta)
    except OSError:
        templates_store.delete_template(tpl_name)
        raise
    log_action(
        user["username"], "clone_to_template", name, "success", f"template -> {tpl_name}, VM kept", task_id=task_id
    )
    return {"template": tpl_name, "vm_source": name, "vm_kept": True}


class DeployRequest(BaseModel):
    new_name: str
    network: str | None = None


@router.post("/{template_name}/deploy", status_code=201)
def deploy_template(
    template_name: str, payload: DeployRequest, user: dict = Depends(require_role("admin")), wait: bool = True
):
    """wait=false: every check is made, then the copy runs as a task and the answer is 202 with its id."""
    maintenance.refuse_if_in_maintenance("local", "Template deployment")
    tpl = templates_store.get_template(template_name)
    if tpl is None:
        raise HTTPException(status_code=404, detail=f"Template '{template_name}' not found")

    name_error = validate_name(payload.new_name)
    if name_error:
        raise HTTPException(status_code=422, detail=name_error)

    # Two deployments under the same name would write the same disk file, as two clones would.
    claim = vm_locks.claim_or_409(payload.new_name, "a deployment")
    try:
        conn = open_conn()
        try:
            try:
                conn.lookupByName(payload.new_name)
                log_action(
                    user["username"], "deploy_template", template_name, "failed", f"'{payload.new_name}' already exists"
                )
                raise HTTPException(status_code=409, detail=f"A VM '{payload.new_name}' already exists")
            except libvirt.libvirtError:
                pass
        finally:
            conn.close()
        if safe_child(IMAGES_DIR, f"{payload.new_name}.qcow2").exists():
            raise HTTPException(status_code=409, detail="A disk file with this name already exists")
        # A task as for a clone: the copy of a large disk takes minutes, and shows in Tasks while it runs.
        task_id = create_task("deploy_template", template_name, node=None, username=user["username"])
    except BaseException:
        claim.release()
        raise
    work = vm_locks.released_after(claim, lambda: _deploy(template_name, tpl, payload, user, task_id))
    if wait:
        return work()
    return answer_later(task_id, work, name=payload.new_name)


def _deploy(template_name, tpl, payload, user, task_id):
    conn = open_conn()
    try:
        new_disk_path = safe_child(IMAGES_DIR, f"{payload.new_name}.qcow2")
        try:
            subprocess.run(
                ["qemu-img", "convert", "-O", "qcow2", tpl["disk_path"], str(new_disk_path)],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            new_disk_path.unlink(missing_ok=True)
            log_action(
                user["username"],
                "deploy_template",
                template_name,
                "failed",
                f"disk copy: {exc.stderr}",
                task_id=task_id,
            )
            raise HTTPException(status_code=500, detail="Disk copy failed") from exc

        root = ET.fromstring(tpl["xml"])
        name_el = root.find("name")
        if name_el is not None:
            name_el.text = payload.new_name
        uuid_el = root.find("uuid")
        if uuid_el is not None:
            root.remove(uuid_el)
        firmware.drop_nvram(root)

        for disk in root.findall(".//devices/disk"):
            if disk.get("device") == "disk":
                src = disk.find("source")
                if src is not None:
                    src.set("file", str(new_disk_path))

        devices_el = root.find(".//devices")
        cloud_init = False
        if devices_el is not None:
            for disk in list(devices_el.findall("disk")):
                if disk.get("device") == "cdrom":
                    src = disk.find("source")
                    cloud_init |= src is not None and (src.get("file") or "").endswith("-cloudinit.iso")
                    devices_el.remove(disk)
        # A template made from a VM set up by cloud-init: the copy gets a cloud-init drive with a new instance id, as
        # a clone does. Without it, it booted with the network configuration cloud-init had written for the
        # template's MAC address (netplan's `match: macaddress`), so without any address, and with its host name and
        # SSH host keys.
        reseed_iso = None
        if cloud_init and devices_el is not None:
            try:
                reseed_iso = create_cloudinit_reseed_iso(payload.new_name)
            except (OSError, subprocess.CalledProcessError):
                logger.warning("No cloud-init drive for %s deployed from %s", payload.new_name, template_name)
            if reseed_iso:
                cdrom_el = ET.SubElement(devices_el, "disk", {"type": "file", "device": "cdrom"})
                ET.SubElement(cdrom_el, "driver", {"name": "qemu", "type": "raw"})
                ET.SubElement(cdrom_el, "source", {"file": str(reseed_iso)})
                dev, bus = firmware.cdrom_target("hdc", firmware.of_domain(root))
                ET.SubElement(cdrom_el, "target", {"dev": dev, "bus": bus})
                ET.SubElement(cdrom_el, "readonly")

        for iface in root.findall(".//devices/interface"):
            mac = iface.find("mac")
            if mac is not None:
                iface.remove(mac)
            if payload.network:
                src = iface.find("source")
                if src is not None:
                    src.set("network", payload.network)

        new_xml = ET.tostring(root, encoding="unicode")

        try:
            new_domain = conn.defineXML(new_xml)
        except libvirt.libvirtError as exc:
            new_disk_path.unlink(missing_ok=True)
            if reseed_iso:
                Path(reseed_iso).unlink(missing_ok=True)
            log_action(user["username"], "deploy_template", template_name, "failed", str(exc), task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Domain definition failed: {exc}") from exc

        refresh_pools_for_paths(conn, [new_disk_path, *([reseed_iso] if reseed_iso else [])])
        # The source VM's account and system, which the copied disk holds (a template made before they were kept has
        # none: set them on the VM).
        if tpl.get("ssh_user"):
            vm_meta.set_vm_ssh_user(payload.new_name, tpl["ssh_user"])
        if tpl.get("os_label"):
            vm_meta.set_vm_os_label(payload.new_name, tpl["os_label"])
        log_action(
            user["username"], "deploy_template", template_name, "success", f"-> {payload.new_name}", task_id=task_id
        )
        return {"template": template_name, "vm": new_domain.name(), "state": "stopped"}
    finally:
        conn.close()


class TemplateRename(BaseModel):
    new_name: str


@router.post("/{template_name}/rename")
def rename_template_endpoint(template_name: str, payload: TemplateRename, user: dict = Depends(require_role("admin"))):
    new = payload.new_name
    error = validate_name(new, "template")
    if error:
        raise HTTPException(status_code=422, detail=error)
    if new == template_name:
        raise HTTPException(status_code=422, detail="The new name is the current one")
    try:
        templates_store.rename_template(template_name, new)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Template '{template_name}' not found") from None
    except FileExistsError:
        raise HTTPException(status_code=409, detail=f"A template named '{new}' already exists") from None
    except OSError as e:
        log_action(user["username"], "rename_template", template_name, "failed", str(e))
        raise HTTPException(status_code=500, detail=f"Rename failed: {e}") from e
    log_action(user["username"], "rename_template", template_name, "success", f"-> {new}")
    return templates_store.get_template(new) | {"xml": None}


@router.delete("/{template_name}")
def delete_template_endpoint(template_name: str, confirm: bool = False, user: dict = Depends(require_role("admin"))):
    tpl = templates_store.get_template(template_name)
    if tpl is None:
        raise HTTPException(status_code=404, detail=f"Template '{template_name}' not found")
    if not confirm:
        raise HTTPException(status_code=400, detail="Confirmation required (?confirm=true)")
    templates_store.delete_template(template_name)
    log_action(user["username"], "delete_template", template_name, "success")
    return {"template": template_name, "deleted": True}
