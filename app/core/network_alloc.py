"""Fixed IP per VM on the libvirt networks managed by Hyperlite (NAT/isolated).

The VM keeps using DHCP as usual (no change to the existing cloud-init,
kickstart or autoinstall): only a MAC -> fixed IP mapping is reserved on the
libvirt network's own DHCP server (`<host mac='...' ip='...'/>` in the <dhcp>
section of the network). The VM therefore always gets the same address, known
at creation time instead of being discovered afterwards from the DHCP lease
(which is what `_get_ip` in vms.py does)."""

import ipaddress
import logging
import random
import threading
import xml.etree.ElementTree as ET

import libvirt

logger = logging.getLogger(__name__)


# One allocation at a time on this node: two creations at once read the same free addresses and picked the same one.
_alloc_lock = threading.Lock()


class AddressInUse(Exception):
    """A VM's reserved address is already held by another machine on the destination's network (#410)."""


def _macs_in_use(conn):
    """MACs this node knows: its VMs' interfaces (stopped ones hold no lease), and the leases and reservations of
    its networks."""
    taken = set()
    for domain in conn.listAllDomains():
        try:
            taken |= {
                m.get("address", "").lower() for m in ET.fromstring(domain.XMLDesc(0)).findall(".//interface/mac")
            }
        except libvirt.libvirtError:
            continue  # undefined meanwhile
    for net in conn.listAllNetworks():
        try:
            taken |= {lease.get("mac", "").lower() for lease in net.DHCPLeases()}
            taken |= set(_reservations(net))
        except libvirt.libvirtError:
            continue  # an inactive network has no leases
    return taken


def generate_mac(conn):
    """Random MAC in the standard QEMU/libvirt prefix (52:54:00), free among this node's VMs (running or not) and
    the leases and reservations of its networks."""
    taken = _macs_in_use(conn)
    for _ in range(20):
        # Non-cryptographic randomness is fine: this only avoids MAC collisions.
        mac = "52:54:00:%02x:%02x:%02x" % (  # noqa: UP031
            random.randint(0, 255),  # noqa: S311
            random.randint(0, 255),  # noqa: S311
            random.randint(0, 255),  # noqa: S311
        )
        if mac not in taken:
            return mac
    raise RuntimeError("Unable to generate a free MAC address after 20 attempts")


def _dhcp_range(network):
    """(start, end) of the DHCP range of the network's IPv4 subnet. An IPv6 one is skipped: fixed addresses are
    IPv4, and a network whose IPv6 subnet came first failed every creation on it."""
    for ip_el in ET.fromstring(network.XMLDesc()).findall("ip"):
        if ip_el.get("family", "ipv4") != "ipv4":
            continue
        range_el = ip_el.find("dhcp/range")
        if range_el is not None:
            return range_el.get("start"), range_el.get("end")
    return None


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


def cluster_used_ips(network_name):
    """Addresses reserved or leased on the network of this name on every node: this one and every registered node.

    Each node runs its own NAT network (same name, same subnet, its own dnsmasq), so a VM created on one node could get
    the address of a VM of another node, and the two collided as soon as one was migrated next to the other (#410).
    Best effort: a node that does not answer is skipped, it never blocks a creation."""
    from app.core.cluster import list_nodes
    from app.core.libvirt_utils import open_conn

    used = set()
    try:
        names = [None, *(n["name"] for n in list_nodes())]
    except Exception:
        logger.debug("cluster_used_ips: node list unavailable", exc_info=True)
        names = [None]
    for name in names:
        try:
            conn = open_conn(name)
        except Exception:
            logger.debug("cluster_used_ips: node %s unreachable", name, exc_info=True)
            continue
        try:
            used |= _used_ips(conn.networkLookupByName(network_name))
        except libvirt.libvirtError:
            pass  # no network of that name on that node
        finally:
            conn.close()
    return used


def allocate_static_ip(conn, network_name, mac, cluster=True):
    """Reserve the first free IP of the network's DHCP range for this MAC. Returns
    the allocated IP, or None when the network has no DHCP configured or the
    range is exhausted (VM creation continues in that case: a missing fixed IP
    rather than blocked creation). With `cluster`, the address is also free on the network of the same name of every
    other node, so it stays unique when the VM moves (#410)."""
    network = conn.networkLookupByName(network_name)
    rng = _dhcp_range(network)
    if rng is None:
        return None
    start, end = rng
    others = cluster_used_ips(network_name) if cluster else set()  # read outside the lock: other nodes, over SSH
    with _alloc_lock:
        used = _used_ips(network) | others
        refused = 0
        for raw in range(int(ipaddress.IPv4Address(start)), int(ipaddress.IPv4Address(end)) + 1):
            candidate = str(ipaddress.IPv4Address(raw))
            if candidate in used:
                continue
            try:
                network.update(
                    libvirt.VIR_NETWORK_UPDATE_COMMAND_ADD_LAST,
                    libvirt.VIR_NETWORK_SECTION_IP_DHCP_HOST,
                    -1,
                    f"<host mac='{mac}' ip='{candidate}'/>",
                    libvirt.VIR_NETWORK_UPDATE_AFFECT_LIVE | libvirt.VIR_NETWORK_UPDATE_AFFECT_CONFIG,
                )
            except libvirt.libvirtError:
                # Reserved meanwhile from outside Hyperlite (virsh): the next address. A network that refuses every
                # update is not tried address by address.
                refused += 1
                if refused >= 3:
                    raise
                logger.info("Address %s of %s refused, trying the next one", candidate, network_name, exc_info=True)
                continue
            return candidate
    return None


def _reservations(network):
    """{mac: ip} of the network's DHCP reservations."""
    root = ET.fromstring(network.XMLDesc())
    return {h.get("mac", "").lower(): h.get("ip") for h in root.findall(".//dhcp/host") if h.get("mac") and h.get("ip")}


def _domain_interfaces(domain_xml):
    """(network name, mac) of each of a domain's interfaces on a libvirt network."""
    pairs = []
    for iface in ET.fromstring(domain_xml).findall(".//devices/interface"):
        source, mac = iface.find("source"), iface.find("mac")
        if source is not None and source.get("network") and mac is not None and mac.get("address"):
            pairs.append((source.get("network"), mac.get("address").lower()))
    return pairs


def carry_reservations(src_conn, dest_conn, domain_xml):
    """Before a migration: reserve, on the destination's networks, the addresses the VM holds on the source's, so it
    keeps them after the move (#410). Raises AddressInUse, before anything is changed, when another machine already
    holds one of them there. Returns (moved, added): the (network, mac) reserved on the source, to release there once
    the VM has left, and those added on the destination, to take back if the migration fails."""
    plan = []
    for network_name, mac in _domain_interfaces(domain_xml):
        try:
            ip = _reservations(src_conn.networkLookupByName(network_name)).get(mac)
            dest_net = dest_conn.networkLookupByName(network_name)
        except libvirt.libvirtError:
            continue  # not a network with reservations, or missing on the destination (the migration says so)
        if not ip or _dhcp_range(dest_net) is None:
            continue
        dest = _reservations(dest_net)
        holder = next((m for m, a in dest.items() if a == ip and m != mac), None)
        leased = next(
            (
                le.get("mac", "").lower()
                for le in dest_net.DHCPLeases()
                if le.get("ipaddr") == ip and le.get("mac", "").lower() != mac
            ),
            None,
        )
        if holder or leased:
            raise AddressInUse(
                f"{ip} is already used on network '{network_name}' of the destination by {holder or leased}"
            )
        plan.append((dest_net, network_name, mac, ip, dest.get(mac)))

    moved, added = [], []
    for dest_net, network_name, mac, ip, current in plan:
        moved.append((network_name, mac))
        if current == ip:
            continue
        if current:
            _update_host(dest_net, libvirt.VIR_NETWORK_UPDATE_COMMAND_DELETE, mac, current)
        _update_host(dest_net, libvirt.VIR_NETWORK_UPDATE_COMMAND_ADD_LAST, mac, ip)
        added.append((network_name, mac))
    return moved, added


def _update_host(network, command, mac, ip):
    network.update(
        command,
        libvirt.VIR_NETWORK_SECTION_IP_DHCP_HOST,
        -1,
        f"<host mac='{mac}' ip='{ip}'/>",
        libvirt.VIR_NETWORK_UPDATE_AFFECT_LIVE | libvirt.VIR_NETWORK_UPDATE_AFFECT_CONFIG,
    )


def release_reservations(conn, pairs):
    """Release (network, mac) reservations on `conn`, best effort: after a migration on the source, or on the
    destination when the migration failed."""
    for network_name, mac in pairs:
        try:
            release_static_ip(conn, network_name, mac)
        except libvirt.libvirtError:
            logger.warning("Reservation of %s on %s not released", mac, network_name, exc_info=True)


def release_static_ip(conn, network_name, mac):
    """Remove this MAC's DHCP reservation (called when the VM is deleted, so the
    range is not exhausted over time)."""
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
                -1,
                host_xml,
                libvirt.VIR_NETWORK_UPDATE_AFFECT_LIVE | libvirt.VIR_NETWORK_UPDATE_AFFECT_CONFIG,
            )
            return True
    return False


def network_gateway(conn, network_name):
    """(gateway address, prefix length) of a libvirt network's IPv4 subnet, or None when it has none (a bridge onto
    the host's LAN: its addresses belong to the LAN's own DHCP server)."""
    network = conn.networkLookupByName(network_name)
    for ip in ET.fromstring(network.XMLDesc()).findall("ip"):
        if ip.get("family", "ipv4") != "ipv4" or not ip.get("address"):
            continue
        if ip.get("prefix"):
            return ip.get("address"), int(ip.get("prefix"))
        if ip.get("netmask"):
            return ip.get("address"), ipaddress.IPv4Network(f"0.0.0.0/{ip.get('netmask')}").prefixlen
    return None
