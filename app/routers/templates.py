import subprocess
import shutil
import xml.etree.ElementTree as ET

import libvirt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.core.security import get_current_user, require_role
from app.core.audit import log_action
from app.core.libvirt_utils import open_conn
from app.core.vm_builder import IMAGES_DIR, validate_name
from app.core import templates_store

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("")
def list_templates(user: dict = Depends(get_current_user)):
    return templates_store.list_templates()


class ConvertRequest(BaseModel):
    template_name: Optional[str] = None


@router.post("/from-vm/{name}", status_code=201)
def convert_to_template(name: str, payload: ConvertRequest, user: dict = Depends(require_role("admin"))):
    tpl_name = (payload.template_name or name).strip()
    try:
        validate_name(tpl_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if templates_store.exists(tpl_name):
        raise HTTPException(status_code=409, detail=f"Un template '{tpl_name}' existe déjà")

    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "convert_to_template", name, "echec", "VM introuvable")
            raise HTTPException(status_code=404, detail=f"VM '{name}' introuvable")

        if domain.isActive():
            log_action(user["username"], "convert_to_template", name, "echec", "VM active")
            raise HTTPException(status_code=409, detail="Arrêtez la VM avant de la convertir en template")

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
            log_action(user["username"], "convert_to_template", name, "echec", "disque introuvable")
            raise HTTPException(status_code=500, detail="Disque source introuvable")

        target_disk = templates_store.save_template(tpl_name, xml_desc, vcpu, memory_mb, name, user["username"])

        try:
            domain.undefine()
        except libvirt.libvirtError as exc:
            templates_store.delete_template(tpl_name)
            log_action(user["username"], "convert_to_template", name, "echec", str(exc))
            raise HTTPException(status_code=500, detail=f"Échec de l'undefine : {exc}")

        shutil.move(disk_source, str(target_disk))

        log_action(user["username"], "convert_to_template", name, "succes", f"template -> {tpl_name}")
        return {"template": tpl_name, "vm_source": name}
    finally:
        conn.close()


class DeployRequest(BaseModel):
    new_name: str
    network: Optional[str] = None


@router.post("/{template_name}/deploy", status_code=201)
def deploy_template(template_name: str, payload: DeployRequest, user: dict = Depends(require_role("admin"))):
    tpl = templates_store.get_template(template_name)
    if tpl is None:
        raise HTTPException(status_code=404, detail=f"Template '{template_name}' introuvable")

    try:
        validate_name(payload.new_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    conn = open_conn()
    try:
        try:
            conn.lookupByName(payload.new_name)
            log_action(user["username"], "deploy_template", template_name, "echec", f"'{payload.new_name}' existe déjà")
            raise HTTPException(status_code=409, detail=f"Une VM '{payload.new_name}' existe déjà")
        except libvirt.libvirtError:
            pass

        new_disk_path = IMAGES_DIR / f"{payload.new_name}.qcow2"
        if new_disk_path.exists():
            raise HTTPException(status_code=409, detail="Un fichier disque porte déjà ce nom")

        try:
            subprocess.run(
                ["qemu-img", "convert", "-O", "qcow2", tpl["disk_path"], str(new_disk_path)],
                check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError as exc:
            log_action(user["username"], "deploy_template", template_name, "echec", f"copie disque : {exc.stderr}")
            raise HTTPException(status_code=500, detail="Échec de la copie du disque")

        root = ET.fromstring(tpl["xml"])
        name_el = root.find("name")
        if name_el is not None:
            name_el.text = payload.new_name
        uuid_el = root.find("uuid")
        if uuid_el is not None:
            root.remove(uuid_el)

        for disk in root.findall(".//devices/disk"):
            if disk.get("device") == "disk":
                src = disk.find("source")
                if src is not None:
                    src.set("file", str(new_disk_path))

        devices_el = root.find(".//devices")
        if devices_el is not None:
            for disk in list(devices_el.findall("disk")):
                if disk.get("device") == "cdrom":
                    devices_el.remove(disk)

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
            log_action(user["username"], "deploy_template", template_name, "echec", str(exc))
            raise HTTPException(status_code=500, detail=f"Échec de la définition : {exc}")

        log_action(user["username"], "deploy_template", template_name, "succes", f"-> {payload.new_name}")
        return {"template": template_name, "vm": new_domain.name(), "etat": "arretee"}
    finally:
        conn.close()


@router.delete("/{template_name}")
def delete_template_endpoint(template_name: str, confirm: bool = False, user: dict = Depends(require_role("admin"))):
    tpl = templates_store.get_template(template_name)
    if tpl is None:
        raise HTTPException(status_code=404, detail=f"Template '{template_name}' introuvable")
    if not confirm:
        raise HTTPException(status_code=400, detail="Confirmation requise (?confirm=true)")
    templates_store.delete_template(template_name)
    log_action(user["username"], "delete_template", template_name, "succes")
    return {"template": template_name, "supprime": True}
