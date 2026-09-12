"""IP fixe par VM sur les reseaux libvirt geres par Hyperlite (NAT/isole).

La VM continue de faire du DHCP normalement (aucun changement dans le
cloud-init/kickstart/autoinstall existant) : on reserve juste, cote serveur
DHCP du reseau libvirt lui-meme, une correspondance MAC -> IP fixe --
`<host mac='...' ip='...'/>` dans la section <dhcp> du reseau. La VM recoit
donc toujours la meme adresse, connue des la creation plutot que decouverte
apres coup via le bail DHCP (ce que fait `_get_ip` dans vms.py aujourd'hui)."""

import ipaddress
import random
import xml.etree.ElementTree as ET

import libvirt


def generate_mac(conn):
    """MAC aleatoire au prefixe QEMU/libvirt standard (52:54:00), verifiee libre
    parmi les baux actifs de tous les reseaux geres par ce connecteur."""
    for _ in range(20):
        mac = "52:54:00:%02x:%02x:%02x" % (
            random.randint(0, 255), random.randint(0, 255), random.randint(0, 255),
        )
        collision = any(
            lease.get("mac", "").lower() == mac.lower()
            for net in conn.listAllNetworks()
            for lease in net.DHCPLeases()
        )
        if not collision:
            return mac
    raise RuntimeError("Impossible de generer une adresse MAC libre apres 20 tentatives")


def _dhcp_range(network):
    root = ET.fromstring(network.XMLDesc())
    range_el = root.find(".//dhcp/range")
    if range_el is None:
        return None
    return range_el.get("start"), range_el.get("end")


def _used_ips(network):
    used = set()
    root = ET.fromstring(network.XMLDesc())
    for host in root.findall(".//dhcp/host"):
        if host.get("ip"):
            used.add(host.get("ip"))
    for lease in network.DHCPLeases():
        if lease.get("ipaddr"):
            used.add(lease["ipaddr"])
    return used


def allocate_static_ip(conn, network_name, mac):
    """Reserve la premiere IP libre de la plage DHCP du reseau pour ce MAC.
    Retourne l'IP allouee, ou None si le reseau n'a pas de DHCP configure ou
    si la plage est epuisee (la creation de VM continue dans ce cas -- IP
    fixe manquee plutot que creation bloquee)."""
    network = conn.networkLookupByName(network_name)
    rng = _dhcp_range(network)
    if rng is None:
        return None
    start, end = rng
    used = _used_ips(network)

    start_i = int(ipaddress.IPv4Address(start))
    end_i = int(ipaddress.IPv4Address(end))
    for raw in range(start_i, end_i + 1):
        candidate = str(ipaddress.IPv4Address(raw))
        if candidate not in used:
            host_xml = f"<host mac='{mac}' ip='{candidate}'/>"
            network.update(
                libvirt.VIR_NETWORK_UPDATE_COMMAND_ADD_LAST,
                libvirt.VIR_NETWORK_SECTION_IP_DHCP_HOST,
                -1, host_xml,
                libvirt.VIR_NETWORK_UPDATE_AFFECT_LIVE | libvirt.VIR_NETWORK_UPDATE_AFFECT_CONFIG,
            )
            return candidate
    return None


def release_static_ip(conn, network_name, mac):
    """Retire la reservation DHCP de ce MAC (appele a la suppression de la VM,
    pour ne pas epuiser la plage au fil du temps)."""
    try:
        network = conn.networkLookupByName(network_name)
    except libvirt.libvirtError:
        return False
    root = ET.fromstring(network.XMLDesc())
    for host in root.findall(".//dhcp/host"):
        if host.get("mac", "").lower() == mac.lower():
            host_xml = f"<host mac='{mac}' ip='{host.get('ip')}'/>"
            network.update(
                libvirt.VIR_NETWORK_UPDATE_COMMAND_DELETE,
                libvirt.VIR_NETWORK_SECTION_IP_DHCP_HOST,
                -1, host_xml,
                libvirt.VIR_NETWORK_UPDATE_AFFECT_LIVE | libvirt.VIR_NETWORK_UPDATE_AFFECT_CONFIG,
            )
            return True
    return False
