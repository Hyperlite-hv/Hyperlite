from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
import libvirt
import subprocess

from app.core.libvirt_utils import open_conn
from app.core.security import get_current_user, require_role
from app.core.audit import log_action
from app.core.vm_builder import validate_name, create_disk, create_cloudinit_iso, build_domain_xml

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
