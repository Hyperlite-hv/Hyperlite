from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException
import hashlib
import libvirt
import re
import xml.etree.ElementTree as ET

from app.core.libvirt_utils import open_conn, ensure_isolated_network
from app.core.security import get_current_user, require_role
from app.core.audit import log_action
from app.core.vm_builder import validate_name
from app.core.error_messages import describe_exception
from app.routers.vms import FirewallConfig, _FIREWALL_ACTIONS, _FIREWALL_DIRECTIONS, _FIREWALL_PROTOCOLS
from app.core.network_firewall import apply_network_firewall, get_network_firewall, remove_network_firewall

router = APIRouter(prefix="/networks", tags=["networks"])

# Un octet d'adresse IPv4 (0-255), reutilise 4x pour valider une adresse
# fournie par l'utilisateur avant de l'inserer dans du XML libvirt.
_IPV4_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")

# Nom d'interface Linux valide (alphanumerique/tiret/underscore/point, max
# 15 caracteres -- limite IFNAMSIZ du noyau). Trouve a l'audit (chantier 11) :
# bridge_name partait tel quel dans du XML libvirt construit par f-string
# (<bridge name='{bridge_name}'/>) sans validation, une injection XML
# possible pour qui peut atteindre cet endpoint (admin uniquement
# aujourd'hui, donc pas exploitable par un tiers pour l'instant -- corrige
# quand meme, ce n'est pas une bonne pratique a laisser trainer).
_IFACE_NAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{1,15}$")


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


# --- Pare-feu au niveau RESEAU (chantier 21, 2026-09-17) -- distinct du
# pare-feu PAR VM (app/routers/vms.py, chantier 9, sous-systeme nwfilter) :
# celui-ci s'applique au PONT du reseau entier (chaine FORWARD du noyau,
# voir app/core/network_firewall.py pour le detail et pourquoi nwfilter ne
# peut pas etre utilise a ce niveau -- verifie contre les schemas RNG de
# libvirt). Reutilise volontairement le meme FirewallConfig/FirewallRule
# que le pare-feu par VM : meme UI, meme validation, seule la cible
# differe. Reserve aux admins (touche iptables au niveau de l'hote, pas
# une VM individuelle).

@router.get("/{name}/firewall")
def get_network_firewall_route(name: str, user: dict = Depends(get_current_user)):
    conn = open_conn()
    try:
        try:
            conn.networkLookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"Réseau '{name}' introuvable")
        return get_network_firewall(name)
    finally:
        conn.close()


@router.put("/{name}/firewall")
def set_network_firewall(name: str, payload: FirewallConfig, user: dict = Depends(require_role("admin"))):
    if payload.default_policy not in _FIREWALL_ACTIONS:
        raise HTTPException(status_code=422, detail="default_policy doit être 'accept' ou 'drop'")
    for rule in payload.rules:
        if rule.action not in _FIREWALL_ACTIONS or rule.direction not in _FIREWALL_DIRECTIONS or rule.protocol not in _FIREWALL_PROTOCOLS:
            raise HTTPException(status_code=422, detail=f"Règle invalide : {rule}")

    conn = open_conn()
    try:
        try:
            conn.networkLookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "set_network_firewall", name, "echec", "Réseau introuvable")
            raise HTTPException(status_code=404, detail=f"Réseau '{name}' introuvable")

        try:
            result = apply_network_firewall(conn, name, payload.model_dump())
        except ValueError as e:
            log_action(user["username"], "set_network_firewall", name, "echec", str(e))
            raise HTTPException(status_code=422, detail=str(e))
        except RuntimeError as e:
            log_action(user["username"], "set_network_firewall", name, "echec", str(e))
            raise HTTPException(status_code=500, detail=str(e))

        log_action(user["username"], "set_network_firewall", name, "succes", f"{len(payload.rules)} règle(s), pont {result['pont']}")
        return {"message": f"Pare-feu appliqué au réseau '{name}' (pont {result['pont']})", **payload.model_dump()}
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
            if not payload.bridge_name or not _IFACE_NAME_RE.match(payload.bridge_name):
                raise HTTPException(status_code=422, detail="bridge_name invalide (attendu un nom d'interface Linux : lettres/chiffres/-/_/. , 15 caractères max)")
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
            # BUG REEL trouve en testant le chantier 21 (2026-09-17) :
            # "virbr-" (6) + name[:10] (10) = jusqu'a 16 caracteres, un de
            # plus que la limite reelle du noyau pour un nom d'interface
            # Linux (IFNAMSIZ=16 OCTETS INCLUANT LE NUL, donc 15 caracteres
            # utilisables) -- tout nom de reseau de 10+ caracteres faisait
            # echouer la creation avec "error creating bridge interface...
            # Numerical result out of range" (ENAMETOOLONG traduit par
            # libvirt), reproduit avec "hltest-uifw" (11 caracteres).
            # Premier correctif (name[:9], 6+9=15) laissait une collision
            # residuelle documentee dans CLAUDE.md : deux noms de reseau
            # partageant leurs 9 premiers caracteres (ex. "guest-wifi-1" et
            # "guest-wifi-2") generaient le MEME nom de pont, la creation
            # du second echouant avec "existe deja" -- corrige ici en
            # remplacant la simple troncature par un prefixe court (4
            # caracteres, garde un peu de lisibilite) + un hash SHA-1 du
            # nom COMPLET (5 caracteres hex) : deux reseaux ne collisionnent
            # que si leurs noms complets sont strictement identiques, deja
            # rejete plus haut ("existe deja") avant d'arriver ici.
            bridge_dev = f"virbr-{payload.name[:4]}{hashlib.sha1(payload.name.encode()).hexdigest()[:5]}"
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

        # Nettoie le pare-feu reseau (chantier 21) AVANT de detruire le
        # reseau -- remove_network_firewall a besoin de relire le pont
        # depuis le XML libvirt encore en place pour retirer proprement le
        # saut depuis HYPERLITENETFW.
        try:
            remove_network_firewall(conn, name)
        except Exception as e:
            print(f"[network_firewall] nettoyage échoué pour '{name}' (suppression poursuivie) : {e!r}", flush=True)

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
