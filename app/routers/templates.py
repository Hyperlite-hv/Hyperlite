import logging
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import libvirt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import firmware, maintenance, templates_store, vm_locks
from app.core.audit import log_action
from app.core.libvirt_utils import open_conn, refresh_pools_for_paths
from app.core.safe_paths import safe_child
from app.core.security import get_current_user, require_role
from app.core.vm_builder import IMAGES_DIR, create_cloudinit_reseed_iso, validate_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("")
def list_templates(user: dict = Depends(get_current_user)):
    return templates_store.list_templates()


class ConvertRequest(BaseModel):
    template_name: str | None = None


@router.post("/from-vm/{name}", status_code=201)
def convert_to_template(name: str, payload: ConvertRequest, user: dict = Depends(require_role("admin"))):
    with vm_locks.claim_or_409(name, "a conversion to a template"):
        return _convert_to_template(name, payload, user)


def _convert_to_template(name, payload, user):
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
            log_action(user["username"], "convert_to_template", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        if domain.isActive():
            log_action(user["username"], "convert_to_template", name, "echec", "VM active")
            raise HTTPException(status_code=409, detail="Stop the VM before converting it to a template")

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
            log_action(user["username"], "convert_to_template", name, "echec", "disk not found")
            raise HTTPException(status_code=500, detail="Source disk not found")

        target_disk = templates_store.save_template(tpl_name, xml_desc, vcpu, memory_mb, name, user["username"])

        try:
            # A UEFI VM's NVRAM and TPM state go with it: each deployment gets fresh ones (see deploy below).
            domain.undefineFlags(firmware.undefine_flags(root))
        except libvirt.libvirtError as exc:
            templates_store.delete_template(tpl_name)
            log_action(user["username"], "convert_to_template", name, "echec", str(exc))
            raise HTTPException(status_code=500, detail=f"Undefine failed: {exc}") from exc

        shutil.move(disk_source, str(target_disk))
        # Deploying from a template drops the CD-ROM devices, so the helper ISOs of the source VM
        # (cloud-init seed, unattended installation media) would stay behind as orphans.
        helper_isos = [
            safe_child(IMAGES_DIR, f"{name}{suffix}")
            for suffix in ("-cloudinit.iso", "-oemdrv.iso", "-autoinstall.iso")
        ]
        for iso in helper_isos:
            iso.unlink(missing_ok=True)
        # The disk left its pool behind libvirt's back: without a refresh the pool keeps listing a volume that is gone.
        refresh_pools_for_paths(conn, [disk_source, target_disk, *helper_isos])

        log_action(user["username"], "convert_to_template", name, "succes", f"template -> {tpl_name}")
        return {"template": tpl_name, "vm_source": name}
    finally:
        conn.close()


class DeployRequest(BaseModel):
    new_name: str
    network: str | None = None


@router.post("/{template_name}/deploy", status_code=201)
def deploy_template(template_name: str, payload: DeployRequest, user: dict = Depends(require_role("admin"))):
    maintenance.refuse_if_in_maintenance("local", "Template deployment")
    tpl = templates_store.get_template(template_name)
    if tpl is None:
        raise HTTPException(status_code=404, detail=f"Template '{template_name}' not found")

    name_error = validate_name(payload.new_name)
    if name_error:
        raise HTTPException(status_code=422, detail=name_error)

    conn = open_conn()
    try:
        try:
            conn.lookupByName(payload.new_name)
            log_action(
                user["username"], "deploy_template", template_name, "echec", f"'{payload.new_name}' already exists"
            )
            raise HTTPException(status_code=409, detail=f"A VM '{payload.new_name}' already exists")
        except libvirt.libvirtError:
            pass

        new_disk_path = safe_child(IMAGES_DIR, f"{payload.new_name}.qcow2")
        if new_disk_path.exists():
            raise HTTPException(status_code=409, detail="A disk file with this name already exists")

        try:
            subprocess.run(
                ["qemu-img", "convert", "-O", "qcow2", tpl["disk_path"], str(new_disk_path)],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            log_action(user["username"], "deploy_template", template_name, "echec", f"disk copy: {exc.stderr}")
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
            log_action(user["username"], "deploy_template", template_name, "echec", str(exc))
            raise HTTPException(status_code=500, detail=f"Domain definition failed: {exc}") from exc

        refresh_pools_for_paths(conn, [new_disk_path, *([reseed_iso] if reseed_iso else [])])
        log_action(user["username"], "deploy_template", template_name, "succes", f"-> {payload.new_name}")
        return {"template": template_name, "vm": new_domain.name(), "etat": "arretee"}
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
        log_action(user["username"], "rename_template", template_name, "echec", str(e))
        raise HTTPException(status_code=500, detail=f"Rename failed: {e}") from e
    log_action(user["username"], "rename_template", template_name, "succes", f"-> {new}")
    return templates_store.get_template(new) | {"xml": None}


@router.delete("/{template_name}")
def delete_template_endpoint(template_name: str, confirm: bool = False, user: dict = Depends(require_role("admin"))):
    tpl = templates_store.get_template(template_name)
    if tpl is None:
        raise HTTPException(status_code=404, detail=f"Template '{template_name}' not found")
    if not confirm:
        raise HTTPException(status_code=400, detail="Confirmation required (?confirm=true)")
    templates_store.delete_template(template_name)
    log_action(user["username"], "delete_template", template_name, "succes")
    return {"template": template_name, "supprime": True}
