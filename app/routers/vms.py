from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
import libvirt
import subprocess
import xml.etree.ElementTree as ET

from app.core.libvirt_utils import open_conn
from app.core.security import get_current_user, require_role
from app.core.audit import log_action
from app.core.vm_builder import validate_name, create_disk, create_cloudinit_iso, build_domain_xml, IMAGES_DIR

router = APIRouter(prefix="/vms", tags=["vms"])

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
        pass
    return None


def _domain_summary(domain):
    state, maxmem, mem, nvcpu, cputime = domain.info()
    return {
        "nom": domain.name(),
        "id": domain.ID() if domain.isActive() else None,
        "uuid": domain.UUIDString(),
        "etat": STATE_NAMES.get(state, "inconnu"),
        "vcpu": nvcpu,
        "memoire_mo": round(maxmem / 1024, 1),
        "ip": _get_ip(domain) if domain.isActive() else None,
    }


@router.get("")
def list_vms(user: dict = Depends(get_current_user)):
    conn = open_conn()
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
        log_action(user["username"], "get_vm", name, "echec", "VM introuvable")
        conn.close()
        raise HTTPException(status_code=404, detail=f"VM '{name}' introuvable")
    result = _domain_summary(domain)
    conn.close()
    log_action(user["username"], "get_vm", name, "succes")
    return result


class VMCreate(BaseModel):
    name: str
    vcpu: int = Field(ge=1, le=2)
    memory_mb: int = Field(ge=256, le=2048)
    disk_gb: int = Field(ge=1, le=20)
    network: str = "default"
    password: str | None = None


@router.post("", status_code=201)
def create_vm(payload: VMCreate, user: dict = Depends(require_role("admin"))):
    errors = []
    name_error = validate_name(payload.name)
    if name_error:
        errors.append(name_error)

    conn = open_conn()
    try:
        try:
            conn.lookupByName(payload.name)
            errors.append(f"Une VM nommee '{payload.name}' existe deja")
        except libvirt.libvirtError:
            pass

        if errors:
            log_action(user["username"], "create_vm", payload.name, "echec", "; ".join(errors))
            raise HTTPException(status_code=422, detail=errors)

        try:
            disk_path = create_disk(payload.name, payload.disk_gb)
            cloudinit_path = create_cloudinit_iso(payload.name, password=payload.password)
        except subprocess.CalledProcessError as e:
            msg = f"Erreur lors de la preparation du disque/cloud-init : {e.stderr or e}"
            log_action(user["username"], "create_vm", payload.name, "echec", msg)
            raise HTTPException(status_code=500, detail=msg)

        xml = build_domain_xml(
            payload.name, payload.vcpu, payload.memory_mb,
            disk_path, cloudinit_path, payload.network,
        )
        domain = conn.defineXML(xml)
        log_action(user["username"], "create_vm", payload.name, "succes")
        return _domain_summary(domain)
    finally:
        conn.close()


@router.post("/{name}/start")
def start_vm(name: str, user: dict = Depends(require_role("admin"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "start_vm", name, "echec", "VM introuvable")
            raise HTTPException(status_code=404, detail=f"VM '{name}' introuvable")
        if domain.isActive():
            log_action(user["username"], "start_vm", name, "echec", "VM deja active")
            raise HTTPException(status_code=409, detail=f"VM '{name}' est deja active")
        try:
            domain.create()
        except libvirt.libvirtError as e:
            log_action(user["username"], "start_vm", name, "echec", str(e))
            raise HTTPException(status_code=500, detail=f"Impossible de demarrer la VM : {e}")
        log_action(user["username"], "start_vm", name, "succes")
        return _domain_summary(domain)
    finally:
        conn.close()


@router.post("/{name}/stop")
def stop_vm(name: str, force: bool = False, user: dict = Depends(require_role("admin"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "stop_vm", name, "echec", "VM introuvable")
            raise HTTPException(status_code=404, detail=f"VM '{name}' introuvable")
        if not domain.isActive():
            log_action(user["username"], "stop_vm", name, "echec", "VM deja arretee")
            raise HTTPException(status_code=409, detail=f"VM '{name}' est deja arretee")
        action_name = "force_stop_vm" if force else "stop_vm"
        try:
            if force:
                domain.destroy()
            else:
                domain.shutdown()
        except libvirt.libvirtError as e:
            log_action(user["username"], action_name, name, "echec", str(e))
            raise HTTPException(status_code=500, detail=f"Impossible d'arreter la VM : {e}")
        log_action(user["username"], action_name, name, "succes")
        return _domain_summary(domain)
    finally:
        conn.close()


@router.post("/{name}/restart")
def restart_vm(name: str, force: bool = False, user: dict = Depends(require_role("admin"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "restart_vm", name, "echec", "VM introuvable")
            raise HTTPException(status_code=404, detail=f"VM '{name}' introuvable")
        if not domain.isActive():
            log_action(user["username"], "restart_vm", name, "echec", "VM arretee")
            raise HTTPException(status_code=409, detail=f"VM '{name}' est arretee, demarrez-la d'abord")
        try:
            if force:
                domain.destroy()
                domain.create()
            else:
                domain.reboot()
        except libvirt.libvirtError as e:
            log_action(user["username"], "restart_vm", name, "echec", str(e))
            raise HTTPException(status_code=500, detail=f"Impossible de redemarrer la VM : {e}")
        log_action(user["username"], "restart_vm", name, "succes")
        return _domain_summary(domain)
    finally:
        conn.close()


@router.delete("/{name}")
def delete_vm(name: str, user: dict = Depends(require_role("admin"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "delete_vm", name, "echec", "VM introuvable")
            raise HTTPException(status_code=404, detail=f"VM '{name}' introuvable")
        if domain.isActive():
            log_action(user["username"], "delete_vm", name, "echec", "VM active, arret requis")
            raise HTTPException(status_code=409, detail=f"VM '{name}' est active. Arretez-la avant de la supprimer")
        try:
            domain.undefine()
        except libvirt.libvirtError as e:
            log_action(user["username"], "delete_vm", name, "echec", str(e))
            raise HTTPException(status_code=500, detail=f"Impossible de supprimer la VM : {e}")

        disk_path = IMAGES_DIR / f"{name}.qcow2"
        cloudinit_path = IMAGES_DIR / f"{name}-cloudinit.iso"
        disk_path.unlink(missing_ok=True)
        cloudinit_path.unlink(missing_ok=True)

        log_action(user["username"], "delete_vm", name, "succes")
        return {"message": f"VM '{name}' supprimee"}
    finally:
        conn.close()


class DiskAttach(BaseModel):
    volume_name: str
    pool: str = "default"
    target_dev: str = "vdb"


@router.post("/{name}/disks", status_code=201)
def attach_disk(name: str, payload: DiskAttach, user: dict = Depends(require_role("admin"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "attach_disk", name, "echec", "VM introuvable")
            raise HTTPException(status_code=404, detail=f"VM '{name}' introuvable")

        try:
            pool = conn.storagePoolLookupByName(payload.pool)
            vol = pool.storageVolLookupByName(payload.volume_name)
        except libvirt.libvirtError:
            log_action(user["username"], "attach_disk", name, "echec", "Volume introuvable")
            raise HTTPException(status_code=404, detail=f"Volume '{payload.volume_name}' introuvable dans le pool '{payload.pool}'")

        disk_xml = f"""
        <disk type='file' device='disk'>
          <driver name='qemu' type='qcow2'/>
          <source file='{vol.path()}'/>
          <target dev='{payload.target_dev}' bus='virtio'/>
        </disk>
        """
        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        if domain.isActive():
            flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
        try:
            domain.attachDeviceFlags(disk_xml, flags)
        except libvirt.libvirtError as e:
            log_action(user["username"], "attach_disk", name, "echec", str(e))
            raise HTTPException(status_code=500, detail=f"Erreur d'attachement du disque : {e}")

        log_action(user["username"], "attach_disk", name, "succes")
        return {"message": f"Volume '{payload.volume_name}' attache a '{name}' en tant que {payload.target_dev}"}
    finally:
        conn.close()


@router.delete("/{name}/disks/{target_dev}")
def detach_disk(name: str, target_dev: str, user: dict = Depends(require_role("admin"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "detach_disk", name, "echec", "VM introuvable")
            raise HTTPException(status_code=404, detail=f"VM '{name}' introuvable")

        xml_desc = domain.XMLDesc(0)
        root = ET.fromstring(xml_desc)
        disk_elem = None
        for disk in root.findall(".//devices/disk"):
            target = disk.find("target")
            if target is not None and target.get("dev") == target_dev:
                disk_elem = disk
                break
        if disk_elem is None:
            log_action(user["username"], "detach_disk", name, "echec", f"Disque {target_dev} introuvable")
            raise HTTPException(status_code=404, detail=f"Disque '{target_dev}' introuvable sur la VM '{name}'")

        disk_xml = ET.tostring(disk_elem, encoding="unicode")
        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        if domain.isActive():
            flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
        try:
            domain.detachDeviceFlags(disk_xml, flags)
        except libvirt.libvirtError as e:
            log_action(user["username"], "detach_disk", name, "echec", str(e))
            raise HTTPException(status_code=500, detail=f"Erreur de detachement : {e}")

        log_action(user["username"], "detach_disk", name, "succes")
        return {"message": f"Disque '{target_dev}' detache de '{name}'"}
    finally:
        conn.close()


def _get_interfaces(domain):
    xml_desc = domain.XMLDesc(0)
    root = ET.fromstring(xml_desc)
    result = []
    for iface in root.findall(".//devices/interface"):
        mac_elem = iface.find("mac")
        source_elem = iface.find("source")
        result.append({
            "mac": mac_elem.get("address") if mac_elem is not None else None,
            "reseau": source_elem.get("network") if source_elem is not None else None,
            "type_source": iface.get("type"),
        })
    return result


@router.get("/{name}/network")
def get_vm_network(name: str, user: dict = Depends(get_current_user)):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "get_vm_network", name, "echec", "VM introuvable")
            raise HTTPException(status_code=404, detail=f"VM '{name}' introuvable")
        interfaces = _get_interfaces(domain)
        ip = _get_ip(domain) if domain.isActive() else None
        log_action(user["username"], "get_vm_network", name, "succes")
        return {"interfaces": interfaces, "ip": ip}
    finally:
        conn.close()


class NetworkUpdate(BaseModel):
    network: str


@router.put("/{name}/network")
def set_vm_network(name: str, payload: NetworkUpdate, user: dict = Depends(require_role("admin"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "set_vm_network", name, "echec", "VM introuvable")
            raise HTTPException(status_code=404, detail=f"VM '{name}' introuvable")

        try:
            conn.networkLookupByName(payload.network)
        except libvirt.libvirtError:
            log_action(user["username"], "set_vm_network", name, "echec", "Reseau introuvable")
            raise HTTPException(status_code=404, detail=f"Reseau '{payload.network}' introuvable")

        xml_desc = domain.XMLDesc(0)
        root = ET.fromstring(xml_desc)
        iface = root.find(".//devices/interface")
        if iface is None:
            log_action(user["username"], "set_vm_network", name, "echec", "Aucune interface")
            raise HTTPException(status_code=404, detail="Aucune interface reseau trouvee sur cette VM")

        source = iface.find("source")
        if source is None:
            source = ET.SubElement(iface, "source")
        for k in list(source.attrib):
            del source.attrib[k]
        source.set("network", payload.network)

        iface_xml = ET.tostring(iface, encoding="unicode")
        flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
        if domain.isActive():
            flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
        try:
            domain.updateDeviceFlags(iface_xml, flags)
        except libvirt.libvirtError as e:
            log_action(user["username"], "set_vm_network", name, "echec", str(e))
            raise HTTPException(status_code=500, detail=f"Erreur de mise a jour du reseau : {e}")

        log_action(user["username"], "set_vm_network", name, "succes")
        return {"message": f"VM '{name}' associee au reseau '{payload.network}'"}
    finally:
        conn.close()
