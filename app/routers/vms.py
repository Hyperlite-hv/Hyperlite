from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
import libvirt
import re
import time
from pathlib import Path
from app.routers.isos import ISOS_DIR
import subprocess
import xml.etree.ElementTree as ET

from app.core.libvirt_utils import open_conn
from app.core.security import get_current_user, require_role
from app.core.audit import log_action
from app.core.vm_builder import validate_name, create_disk, create_cloudinit_iso, build_domain_xml, IMAGES_DIR
from xml.sax.saxutils import escape

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

        try:
            conn.networkLookupByName(payload.network)
        except libvirt.libvirtError:
            errors.append(f"Reseau '{payload.network}' introuvable")

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
        except ValueError as e:
            log_action(user["username"], "create_vm", payload.name, "echec", str(e))
            raise HTTPException(status_code=422, detail=str(e))

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
def delete_vm(name: str, confirm: bool = False, user: dict = Depends(require_role("admin"))):
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
        if not confirm:
            log_action(user["username"], "delete_vm", name, "echec", "Confirmation manquante")
            raise HTTPException(status_code=400, detail="Action irreversible : ajoutez ?confirm=true pour confirmer la suppression")
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
    if not TARGET_DEV_RE.match(payload.target_dev):
        log_action(user["username"], "attach_disk", name, "echec", "target_dev invalide")
        raise HTTPException(status_code=422, detail="target_dev invalide (attendu par ex. vda, vdb, sdb)")
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
    if not TARGET_DEV_RE.match(target_dev):
        log_action(user["username"], "detach_disk", name, "echec", "target_dev invalide")
        raise HTTPException(status_code=422, detail="target_dev invalide (attendu par ex. vda, vdb, sdb)")
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


@router.get("/{name}/disks")
def get_vm_disks(name: str, user: dict = Depends(get_current_user)):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "get_vm_disks", name, "echec", "VM introuvable")
            raise HTTPException(status_code=404, detail=f"VM '{name}' introuvable")
        xml_desc = domain.XMLDesc(0)
        root = ET.fromstring(xml_desc)
        disks = []
        for disk in root.findall(".//devices/disk"):
            target = disk.find("target")
            source = disk.find("source")
            disks.append({
                "cible": target.get("dev") if target is not None else None,
                "bus": target.get("bus") if target is not None else None,
                "type": disk.get("device"),
                "source": (source.get("file") if source is not None else None),
            })
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


# --- Snapshots (10.8) ---
# Un snapshot capture l'etat d'une VM (disque, et memoire si elle tourne) a un
# instant T, stocke DANS le fichier qcow2 lui-meme : c'est rapide a creer/restaurer
# mais ce n'est PAS une sauvegarde independante (si le disque qcow2 est perdu/corrompu,
# tous ses snapshots le sont aussi). Une vraie sauvegarde (backup) est une copie
# complete et autonome des donnees, stockee ailleurs, qui survit a la perte du disque
# source - c'est plus lent et plus lourd, mais c'est la seule protection contre une
# panne de stockage. Le snapshot sert a revenir en arriere rapidement (avant une mise
# a jour risquee, par exemple) ; le backup sert a la reprise apres sinistre.

def _snapshot_summary(snap):
    xml_desc = snap.getXMLDesc()
    root = ET.fromstring(xml_desc)
    desc_elem = root.find("description")
    creation_elem = root.find("creationTime")
    state_elem = root.find("state")
    return {
        "nom": snap.getName(),
        "description": desc_elem.text if desc_elem is not None else None,
        "date_creation": creation_elem.text if creation_elem is not None else None,
        "etat_vm": state_elem.text if state_elem is not None else None,
        "actuel": snap.isCurrent() == 1,
    }


@router.get("/{name}/snapshots")
def list_snapshots(name: str, user: dict = Depends(get_current_user)):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "list_snapshots", name, "echec", "VM introuvable")
            raise HTTPException(status_code=404, detail=f"VM '{name}' introuvable")
        snaps = domain.listAllSnapshots()
        result = [_snapshot_summary(s) for s in snaps]
        log_action(user["username"], "list_snapshots", name, "succes")
        return result
    finally:
        conn.close()


class SnapshotCreate(BaseModel):
    name: str
    description: str | None = None


@router.post("/{name}/snapshots", status_code=201)
def create_snapshot(name: str, payload: SnapshotCreate, user: dict = Depends(require_role("admin"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "create_snapshot", name, "echec", "VM introuvable")
            raise HTTPException(status_code=404, detail=f"VM '{name}' introuvable")

        name_error = validate_name(payload.name)
        if name_error:
            log_action(user["username"], "create_snapshot", payload.name, "echec", name_error)
            raise HTTPException(status_code=422, detail=name_error)

        try:
            domain.snapshotLookupByName(payload.name)
            log_action(user["username"], "create_snapshot", payload.name, "echec", "Snapshot deja existant")
            raise HTTPException(status_code=422, detail=f"Un snapshot '{payload.name}' existe deja pour cette VM")
        except libvirt.libvirtError:
            pass

        desc_xml = f"<description>{escape(payload.description)}</description>" if payload.description else ""
        snap_xml = f"""
        <domainsnapshot>
          <name>{escape(payload.name)}</name>
          {desc_xml}
        </domainsnapshot>
        """
        try:
            snap = domain.snapshotCreateXML(snap_xml, 0)
        except libvirt.libvirtError as e:
            log_action(user["username"], "create_snapshot", payload.name, "echec", str(e))
            raise HTTPException(status_code=500, detail=f"Erreur de creation du snapshot : {e}")

        log_action(user["username"], "create_snapshot", payload.name, "succes")
        return _snapshot_summary(snap)
    finally:
        conn.close()


@router.post("/{name}/snapshots/{snapshot_name}/restore")
def restore_snapshot(name: str, snapshot_name: str, confirm: bool = False, user: dict = Depends(require_role("admin"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "restore_snapshot", name, "echec", "VM introuvable")
            raise HTTPException(status_code=404, detail=f"VM '{name}' introuvable")

        try:
            snap = domain.snapshotLookupByName(snapshot_name)
        except libvirt.libvirtError:
            log_action(user["username"], "restore_snapshot", snapshot_name, "echec", "Snapshot introuvable")
            raise HTTPException(status_code=404, detail=f"Snapshot '{snapshot_name}' introuvable")

        if not confirm:
            log_action(user["username"], "restore_snapshot", snapshot_name, "echec", "Confirmation manquante")
            raise HTTPException(status_code=400, detail="Action irreversible : ajoutez ?confirm=true pour confirmer la restauration")

        try:
            domain.revertToSnapshot(snap, 0)
        except libvirt.libvirtError as e:
            log_action(user["username"], "restore_snapshot", snapshot_name, "echec", str(e))
            raise HTTPException(status_code=500, detail=f"Erreur de restauration : {e}")

        log_action(user["username"], "restore_snapshot", snapshot_name, "succes")
        return {"message": f"VM '{name}' restauree a l'etat du snapshot '{snapshot_name}'"}
    finally:
        conn.close()


@router.delete("/{name}/snapshots/{snapshot_name}")
def delete_snapshot(name: str, snapshot_name: str, user: dict = Depends(require_role("admin"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "delete_snapshot", name, "echec", "VM introuvable")
            raise HTTPException(status_code=404, detail=f"VM '{name}' introuvable")

        try:
            snap = domain.snapshotLookupByName(snapshot_name)
        except libvirt.libvirtError:
            log_action(user["username"], "delete_snapshot", snapshot_name, "echec", "Snapshot introuvable")
            raise HTTPException(status_code=404, detail=f"Snapshot '{snapshot_name}' introuvable")

        try:
            snap.delete(0)
        except libvirt.libvirtError as e:
            log_action(user["username"], "delete_snapshot", snapshot_name, "echec", str(e))
            raise HTTPException(status_code=500, detail=f"Erreur de suppression : {e}")

        log_action(user["username"], "delete_snapshot", snapshot_name, "succes")
        return {"message": f"Snapshot '{snapshot_name}' supprime"}
    finally:
        conn.close()


class CloneRequest(BaseModel):
    new_name: str


@router.post("/{name}/clone", status_code=201)
def clone_vm(name: str, payload: CloneRequest, user: dict = Depends(get_current_user)):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "clone_vm", name, "echec", "VM source introuvable")
            raise HTTPException(status_code=404, detail=f"VM '{name}' introuvable")

        try:
            validate_name(payload.new_name)
        except ValueError as exc:
            log_action(user["username"], "clone_vm", name, "echec", f"nom invalide : {payload.new_name}")
            raise HTTPException(status_code=422, detail=str(exc))

        try:
            conn.lookupByName(payload.new_name)
            log_action(user["username"], "clone_vm", name, "echec", f"'{payload.new_name}' existe deja")
            raise HTTPException(status_code=409, detail=f"Une VM '{payload.new_name}' existe deja")
        except libvirt.libvirtError:
            pass

        if domain.isActive():
            log_action(user["username"], "clone_vm", name, "echec", "VM active")
            raise HTTPException(status_code=409, detail="Arretez la VM avant de la cloner")

        root = ET.fromstring(domain.XMLDesc(0))

        disk_el = None
        for disk in root.findall(".//devices/disk"):
            if disk.get("device") == "disk":
                disk_el = disk
                break
        if disk_el is None:
            log_action(user["username"], "clone_vm", name, "echec", "disque source introuvable")
            raise HTTPException(status_code=500, detail="Disque source introuvable")
        source_el = disk_el.find("source")
        source_path = source_el.get("file") if source_el is not None else None
        if not source_path:
            raise HTTPException(status_code=500, detail="Chemin du disque source introuvable")

        new_disk_path = IMAGES_DIR / f"{payload.new_name}.qcow2"
        if new_disk_path.exists():
            raise HTTPException(status_code=409, detail="Un fichier disque porte deja ce nom")

        try:
            subprocess.run(
                ["qemu-img", "convert", "-O", "qcow2", source_path, str(new_disk_path)],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            log_action(user["username"], "clone_vm", name, "echec", f"copie disque : {exc.stderr}")
            raise HTTPException(status_code=500, detail="Echec de la copie du disque")

        name_el = root.find("name")
        if name_el is not None:
            name_el.text = payload.new_name

        uuid_el = root.find("uuid")
        if uuid_el is not None:
            root.remove(uuid_el)

        source_el.set("file", str(new_disk_path))

        devices_el = root.find(".//devices")
        if devices_el is not None:
            for disk in list(devices_el.findall("disk")):
                if disk.get("device") == "cdrom":
                    devices_el.remove(disk)

        for iface in root.findall(".//devices/interface"):
            mac = iface.find("mac")
            if mac is not None:
                iface.remove(mac)

        new_xml = ET.tostring(root, encoding="unicode")

        try:
            new_domain = conn.defineXML(new_xml)
        except libvirt.libvirtError as exc:
            new_disk_path.unlink(missing_ok=True)
            log_action(user["username"], "clone_vm", name, "echec", str(exc))
            raise HTTPException(status_code=500, detail=f"Echec de la definition du clone : {exc}")

        log_action(user["username"], "clone_vm", name, "succes", f"clone -> {payload.new_name}")
        return {"source": name, "clone": new_domain.name(), "etat": "arretee"}
    finally:
        conn.close()


class CdromRequest(BaseModel):
    iso: str


@router.put("/{name}/cdrom")
def set_vm_cdrom(name: str, payload: CdromRequest, user: dict = Depends(get_current_user)):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "set_vm_cdrom", name, "echec", "VM introuvable")
            raise HTTPException(status_code=404, detail=f"VM '{name}' introuvable")

        iso_filename = Path(payload.iso).name
        if not iso_filename.lower().endswith(".iso"):
            raise HTTPException(status_code=422, detail="Nom d'ISO invalide")
        iso_path = ISOS_DIR / iso_filename
        if not iso_path.exists():
            raise HTTPException(status_code=404, detail=f"ISO '{iso_filename}' introuvable")

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
                    '<readonly/>'
                    '</disk>'
                )
                domain.attachDeviceFlags(new_cdrom_xml, flags)
        except libvirt.libvirtError as exc:
            log_action(user["username"], "set_vm_cdrom", name, "echec", str(exc))
            raise HTTPException(status_code=500, detail=f"Echec du montage : {exc}")

        log_action(user["username"], "set_vm_cdrom", name, "succes", iso_filename)
        return {"vm": name, "iso": iso_filename}
    finally:
        conn.close()


@router.delete("/{name}/cdrom")
def eject_vm_cdrom(name: str, user: dict = Depends(get_current_user)):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "eject_vm_cdrom", name, "echec", "VM introuvable")
            raise HTTPException(status_code=404, detail=f"VM '{name}' introuvable")

        root = ET.fromstring(domain.XMLDesc(0))
        devices_el = root.find(".//devices")
        cdrom = None
        if devices_el is not None:
            for disk in devices_el.findall("disk"):
                if disk.get("device") == "cdrom":
                    cdrom = disk
                    break
        if cdrom is None:
            raise HTTPException(status_code=404, detail="Aucun lecteur CD sur cette VM")

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
            raise HTTPException(status_code=500, detail=f"Echec de l'ejection : {exc}")

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
            log_action(user["username"], "get_vm_metrics", name, "echec", "VM introuvable")
            raise HTTPException(status_code=404, detail=f"VM '{name}' introuvable")

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
                    pass
            net_samples = {}
            for dev in iface_devs:
                try:
                    net_samples[dev] = domain.interfaceStats(dev)
                except libvirt.libvirtError:
                    pass
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
            pass

        disques = []
        for dev in disk_devs:
            if dev in disk1 and dev in disk2:
                rd_rate = max(0, (disk2[dev][1] - disk1[dev][1]) / elapsed)
                wr_rate = max(0, (disk2[dev][3] - disk1[dev][3]) / elapsed)
                disques.append({
                    "cible": dev,
                    "lecture_ko_s": round(rd_rate / 1024, 1),
                    "ecriture_ko_s": round(wr_rate / 1024, 1),
                })

        reseaux = []
        for dev in iface_devs:
            if dev in net1 and dev in net2:
                rx_rate = max(0, (net2[dev][0] - net1[dev][0]) / elapsed)
                tx_rate = max(0, (net2[dev][4] - net1[dev][4]) / elapsed)
                reseaux.append({
                    "interface": dev,
                    "reception_ko_s": round(rx_rate / 1024, 1),
                    "emission_ko_s": round(tx_rate / 1024, 1),
                })

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
