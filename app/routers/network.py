from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException
import libvirt
import re
import xml.etree.ElementTree as ET

from app.core.libvirt_utils import open_conn, ensure_isolated_network
from app.core.security import get_current_user, require_role
from app.core.audit import log_action
from app.core.vm_builder import validate_name
from app.core.error_messages import describe_exception

router = APIRouter(prefix="/networks", tags=["networks"])

# Un octet d'adresse IPv4 (0-255), reutilise 4x pour valider une adresse
# fournie par l'utilisateur avant de l'inserer dans du XML libvirt.
_IPV4_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")


def _valid_ipv4(addr):
    m = _IPV4_RE.match(addr or "")
    return bool(m) and all(0 <= int(g) <= 255 for g in m.groups())

FORWARD_MODE_LABELS = {
    "nat": "nat",
    "route": "route",
    "bridge": "bridge",
    "open": "ouvert",
    "private": "prive",
    "vepa": "vepa",
    "passthrough": "passthrough",
    "hostdev": "hostdev",
}


def _network_summary(net):
    xml_desc = net.XMLDesc(0)
    root = ET.fromstring(xml_desc)
    forward = root.find("forward")
    mode = forward.get("mode") if forward is not None else None
    bridge = root.find("bridge")
    bridge_name = bridge.get("name") if bridge is not None else None
    ip_elem = root.find("ip")
    subnet = None
    if ip_elem is not None:
        subnet = {"adresse": ip_elem.get("address"), "masque": ip_elem.get("netmask")}
    return {
        "nom": net.name(),
        "uuid": net.UUIDString(),
        "actif": net.isActive() == 1,
        "autostart": bool(net.autostart()),
        "pont": bridge_name,
        "type": FORWARD_MODE_LABELS.get(mode, "isole" if mode is None else mode),
        "reseau": subnet,
    }


@router.get("")
def list_networks(user: dict = Depends(get_current_user)):
    conn = open_conn()
    try:
        ensure_isolated_network(conn)
        nets = conn.listAllNetworks()
        result = [_network_summary(n) for n in nets]
        log_action(user["username"], "list_networks", "networks", "succes")
        return result
    finally:
        conn.close()


@router.get("/{name}")
def get_network(name: str, user: dict = Depends(get_current_user)):
    conn = open_conn()
    try:
        try:
            net = conn.networkLookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "get_network", name, "echec", "Réseau introuvable")
            raise HTTPException(status_code=404, detail=f"Réseau '{name}' introuvable")

        summary = _network_summary(net)
        leases = []
        if net.isActive():
            try:
                for lease in net.DHCPLeases():
                    leases.append({
                        "mac": lease.get("mac"),
                        "ip": lease.get("ipaddr"),
                        "hostname": lease.get("hostname"),
                    })
            except libvirt.libvirtError:
                pass
        summary["baux_dhcp"] = leases
        log_action(user["username"], "get_network", name, "succes")
        return summary
    finally:
        conn.close()


# --- Creation/suppression de reseaux virtuels (chantier 9 de la roadmap
# vSphere/vCenter, 2026-09-13) -- equivalent simplifie des vSwitch/Port
# Groups : un reseau libvirt = l'equivalent d'un port group relie a un
# vSwitch NAT/isole/en pont. Reserve aux admins (creer un reseau touche
# la configuration reseau de l'hote lui-meme, pas seulement une VM). ---

class NetworkCreate(BaseModel):
    name: str
    mode: str = Field(description="'nat' | 'isole' | 'bridge'")
    bridge_name: str | None = Field(None, description="Pont hote existant (obligatoire si mode='bridge', ignoré sinon)")
    subnet_address: str | None = Field(None, description="Adresse de la passerelle, ex '192.168.150.1' (nat/isole)")
    subnet_netmask: str = "255.255.255.0"
    dhcp_start: str | None = None
    dhcp_end: str | None = None


@router.post("", status_code=201)
def create_network(payload: NetworkCreate, user: dict = Depends(require_role("admin"))):
    name_error = validate_name(payload.name)
    if name_error:
        log_action(user["username"], "create_network", payload.name, "echec", name_error)
        raise HTTPException(status_code=422, detail=name_error)

    if payload.mode not in ("nat", "isole", "bridge"):
        raise HTTPException(status_code=422, detail="mode doit être 'nat', 'isole' ou 'bridge'")

    conn = open_conn()
    try:
        try:
            conn.networkLookupByName(payload.name)
            log_action(user["username"], "create_network", payload.name, "echec", "existe déjà")
            raise HTTPException(status_code=409, detail=f"Un réseau '{payload.name}' existe déjà")
        except libvirt.libvirtError:
            pass

        if payload.mode == "bridge":
            if not payload.bridge_name:
                raise HTTPException(status_code=422, detail="bridge_name est requis pour le mode 'bridge'")
            net_xml = f"""
            <network>
              <name>{payload.name}</name>
              <forward mode='bridge'/>
              <bridge name='{payload.bridge_name}'/>
            </network>
            """
        else:
            if not payload.subnet_address or not _valid_ipv4(payload.subnet_address):
                raise HTTPException(status_code=422, detail="subnet_address invalide (attendu une adresse IPv4, ex '192.168.150.1')")
            forward_xml = "<forward mode='nat'/>" if payload.mode == "nat" else ""
            dhcp_xml = ""
            if payload.dhcp_start and payload.dhcp_end:
                if not (_valid_ipv4(payload.dhcp_start) and _valid_ipv4(payload.dhcp_end)):
                    raise HTTPException(status_code=422, detail="dhcp_start/dhcp_end invalides")
                dhcp_xml = f"<dhcp><range start='{payload.dhcp_start}' end='{payload.dhcp_end}'/></dhcp>"
            bridge_dev = f"virbr-{payload.name[:10]}"
            net_xml = f"""
            <network>
              <name>{payload.name}</name>
              {forward_xml}
              <bridge name='{bridge_dev}' stp='on' delay='0'/>
              <ip address='{payload.subnet_address}' netmask='{payload.subnet_netmask}'>
                {dhcp_xml}
              </ip>
            </network>
            """

        try:
            net = conn.networkDefineXML(net_xml)
            net.create()
            net.setAutostart(True)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "create_network", payload.name, "echec", msg)
            raise HTTPException(status_code=500, detail=f"Erreur de création du réseau : {msg}")

        log_action(user["username"], "create_network", payload.name, "succes")
        return _network_summary(net)
    finally:
        conn.close()


@router.delete("/{name}")
def delete_network(name: str, confirm: bool = False, user: dict = Depends(require_role("admin"))):
    if name in ("default", "hyperlite-isolated"):
        raise HTTPException(status_code=403, detail=f"Le réseau '{name}' est un réseau système, il ne peut pas être supprimé")

    conn = open_conn()
    try:
        try:
            net = conn.networkLookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "delete_network", name, "echec", "Réseau introuvable")
            raise HTTPException(status_code=404, detail=f"Réseau '{name}' introuvable")

        # Refuse si une VM (active ou non) a encore une interface sur ce
        # reseau -- la supprimer sous ses pieds casserait sa connectivite
        # au prochain demarrage sans aucun message d'erreur clair pour
        # l'utilisateur.
        attached_vms = []
        for domain in conn.listAllDomains():
            try:
                root = ET.fromstring(domain.XMLDesc(0))
            except libvirt.libvirtError:
                continue
            for source in root.findall(".//devices/interface[@type='network']/source"):
                if source.get("network") == name:
                    attached_vms.append(domain.name())
                    break
        if attached_vms:
            log_action(user["username"], "delete_network", name, "echec", f"utilisé par {attached_vms}")
            raise HTTPException(status_code=409, detail=f"Réseau utilisé par : {', '.join(attached_vms)} — détachez ces interfaces avant de le supprimer")

        if not confirm:
            log_action(user["username"], "delete_network", name, "echec", "Confirmation manquante")
            raise HTTPException(status_code=400, detail="Ajoutez ?confirm=true pour confirmer la suppression")

        try:
            if net.isActive():
                net.destroy()
            net.undefine()
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "delete_network", name, "echec", msg)
            raise HTTPException(status_code=500, detail=f"Erreur de suppression : {msg}")

        log_action(user["username"], "delete_network", name, "succes")
        return {"message": f"Réseau '{name}' supprimé"}
    finally:
        conn.close()
