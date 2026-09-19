"""Fixed IP per VM on the libvirt networks managed by Hyperlite (NAT/isolated).

The VM keeps using DHCP as usual (no change to the existing cloud-init,
kickstart or autoinstall): only a MAC -> fixed IP mapping is reserved on the
libvirt network's own DHCP server (`<host mac='...' ip='...'/>` in the <dhcp>
section of the network). The VM therefore always gets the same address, known
at creation time instead of being discovered afterwards from the DHCP lease
(which is what `_get_ip` in vms.py does)."""

import ipaddress
import random
import xml.etree.ElementTree as ET

import libvirt


def generate_mac(conn):
    """Random MAC in the standard QEMU/libvirt prefix (52:54:00), checked free among
    the active leases of every network managed by this connector."""
    for _ in range(20):
        # Non-cryptographic randomness is fine: this only avoids MAC collisions.
        mac = "52:54:00:%02x:%02x:%02x" % (  # noqa: UP031
            random.randint(0, 255),  # noqa: S311
            random.randint(0, 255),  # noqa: S311
            random.randint(0, 255),  # noqa: S311
        )
        collision = any(
            lease.get("mac", "").lower() == mac.lower() for net in conn.listAllNetworks() for lease in net.DHCPLeases()
        )
        if not collision:
            return mac
    raise RuntimeError("Unable to generate a free MAC address after 20 attempts")


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
    """Reserve the first free IP of the network's DHCP range for this MAC. Returns
    the allocated IP, or None when the network has no DHCP configured or the
    range is exhausted (VM creation continues in that case: a missing fixed IP
    rather than blocked creation)."""
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
                -1,
                host_xml,
                libvirt.VIR_NETWORK_UPDATE_AFFECT_LIVE | libvirt.VIR_NETWORK_UPDATE_AFFECT_CONFIG,
            )
            return candidate
    return None


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
