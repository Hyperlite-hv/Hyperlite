from fastapi import APIRouter, Depends, HTTPException
import libvirt
import xml.etree.ElementTree as ET

from app.core.libvirt_utils import open_conn, ensure_isolated_network
from app.core.security import get_current_user
from app.core.audit import log_action

router = APIRouter(prefix="/networks", tags=["networks"])

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
            log_action(user["username"], "get_network", name, "echec", "Reseau introuvable")
            raise HTTPException(status_code=404, detail=f"Reseau '{name}' introuvable")

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
