"""Editing a NAT or isolated libvirt network after its creation, and its IP address management.

The subnet, the DHCP range and the NAT/isolated mode are written to the network's persistent definition. On a
running network libvirt cannot move the bridge to a new subnet in place, so those changes wait for the network's
next restart (reported as pending, and the page offers the restart); a DHCP range change alone is applied live.

Reservations are `<host mac ip name>` entries of the DHCP server, the same ones Hyperlite writes for a VM's fixed
address (app/core/network_alloc.py): added and removed live and in the persistent definition at once.
"""

import ipaddress
import re
import xml.etree.ElementTree as ET

import libvirt

MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")


class EditError(ValueError):
    """A change refused with a message for the user."""


def _ipv4(root):
    for ip in root.findall("ip"):
        if ip.get("family", "ipv4") == "ipv4" and ip.get("address"):
            return ip
    return None


def _subnet(ip_el):
    if ip_el.get("prefix"):
        return ipaddress.IPv4Interface(f"{ip_el.get('address')}/{ip_el.get('prefix')}")
    return ipaddress.IPv4Interface(f"{ip_el.get('address')}/{ip_el.get('netmask') or '255.255.255.0'}")


def describe_xml(xml):
    """The editable part of a network definition: mode, gateway, mask, DHCP range and reservations. `modifiable` is
    false for a network with no IPv4 subnet of its own (a bridge onto the host's LAN)."""
    root = ET.fromstring(xml)
    forward = root.find("forward")
    mode = forward.get("mode", "nat") if forward is not None else None
    ip_el = _ipv4(root)
    if ip_el is None or mode not in (None, "nat"):
        return {"modifiable": False, "mode": mode, "adresse": None, "masque": None, "dhcp": None, "reservations": []}
    iface = _subnet(ip_el)
    rng = ip_el.find("dhcp/range")
    return {
        "modifiable": True,
        "mode": "nat" if mode == "nat" else "isole",
        "adresse": str(iface.ip),
        "masque": str(iface.netmask),
        "dhcp": {"debut": rng.get("start"), "fin": rng.get("end")} if rng is not None else None,
        "reservations": [
            {"mac": (h.get("mac") or "").lower(), "ip": h.get("ip"), "nom": h.get("name")}
            for h in ip_el.findall("dhcp/host")
        ],
    }


def _addr(value, label):
    try:
        return ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, ValueError):
        raise EditError(f"Invalid {label}: an IPv4 address is expected") from None


def _usable(net, ip, label):
    if ip not in net or ip in (net.network_address, net.broadcast_address):
        raise EditError(f"{label} {ip} is not a usable address of {net}")


def edit_xml(xml, *, mode, address, netmask, dhcp, other_subnets=()):
    """New definition with the subnet, DHCP range (None to turn DHCP off) and mode changed. The reservations are
    kept and must stay inside the new subnet and outside the new DHCP range's conflicts: they are named otherwise."""
    if mode not in ("nat", "isole"):
        raise EditError("mode must be 'nat' or 'isole'")
    root = ET.fromstring(xml)
    ip_el = _ipv4(root)
    current = describe_xml(xml)
    if not current["modifiable"]:
        raise EditError("This network has no subnet of its own (it bridges onto the host's network): nothing to edit")
    gateway = _addr(address, "gateway")
    try:
        mask = ipaddress.IPv4Network(f"0.0.0.0/{netmask}").netmask
        net = ipaddress.IPv4Interface(f"{gateway}/{mask}").network
    except (ipaddress.NetmaskValueError, ValueError):
        raise EditError("Invalid netmask") from None
    if net.prefixlen > 30:
        raise EditError("The subnet is too small: a /30 at most")
    _usable(net, gateway, "The gateway")
    for other_name, other in other_subnets:
        if net.overlaps(other):
            raise EditError(f"{net} overlaps the network '{other_name}' ({other})")

    rng = None
    if dhcp is not None:
        start, end = _addr(dhcp.get("debut"), "DHCP start"), _addr(dhcp.get("fin"), "DHCP end")
        _usable(net, start, "The DHCP start")
        _usable(net, end, "The DHCP end")
        if start > end:
            raise EditError("The DHCP range starts after its end")
        if start <= gateway <= end:
            raise EditError(f"The gateway {gateway} is inside the DHCP range")
        rng = (str(start), str(end))

    outside = [r["ip"] for r in current["reservations"] if r["ip"] and ipaddress.IPv4Address(r["ip"]) not in net]
    if outside:
        raise EditError(f"Reservations outside the new subnet: {', '.join(outside)}; remove them first")
    if dhcp is None and current["reservations"]:
        raise EditError("DHCP cannot be turned off while it has reservations; remove them first")

    forward = root.find("forward")
    if mode == "nat" and forward is None:
        forward = ET.Element("forward", {"mode": "nat"})
        root.insert(list(root).index(root.find("name")) + 1, forward)
    elif mode == "isole" and forward is not None:
        root.remove(forward)

    for key in ("address", "netmask", "prefix"):
        ip_el.attrib.pop(key, None)
    ip_el.set("address", str(gateway))
    ip_el.set("netmask", str(mask))
    dhcp_el = ip_el.find("dhcp")
    if rng is None:
        if dhcp_el is not None:
            ip_el.remove(dhcp_el)
    else:
        if dhcp_el is None:
            dhcp_el = ET.SubElement(ip_el, "dhcp")
        for old in dhcp_el.findall("range"):
            dhcp_el.remove(old)
        dhcp_el.insert(0, ET.Element("range", {"start": rng[0], "end": rng[1]}))
    return ET.tostring(root, encoding="unicode")


def _essentials(xml):
    # What a running network only takes at its restart; the DHCP range itself can change live.
    d = describe_xml(xml)
    return (d["mode"], d["adresse"], d["masque"], d["dhcp"] is not None)


def pending(net):
    """True when the running network differs from its definition in a way only a restart applies."""
    if not net.isActive():
        return False
    try:
        return _essentials(net.XMLDesc(0)) != _essentials(net.XMLDesc(libvirt.VIR_NETWORK_XML_INACTIVE))
    except (libvirt.libvirtError, ET.ParseError):
        return False


def _other_subnets(conn, name):
    found = []
    for other in conn.listAllNetworks():
        if other.name() == name:
            continue
        try:
            ip_el = _ipv4(ET.fromstring(other.XMLDesc(libvirt.VIR_NETWORK_XML_INACTIVE)))
        except (libvirt.libvirtError, ET.ParseError):
            continue
        if ip_el is not None:
            found.append((other.name(), _subnet(ip_el).network))
    return found


def apply_edit(conn, net, *, mode, address, netmask, dhcp):
    """Writes the new definition; a DHCP range change on a running network is also applied live. Returns whether
    the rest waits for a restart of the network."""
    new_xml = edit_xml(
        net.XMLDesc(libvirt.VIR_NETWORK_XML_INACTIVE),
        mode=mode,
        address=address,
        netmask=netmask,
        dhcp=dhcp,
        other_subnets=_other_subnets(conn, net.name()),
    )
    conn.networkDefineXML(new_xml)
    if net.isActive():
        live, new = describe_xml(net.XMLDesc(0)), describe_xml(new_xml)
        if _essentials(net.XMLDesc(0)) == _essentials(new_xml) and live["dhcp"] and live["dhcp"] != new["dhcp"]:
            flags, section = libvirt.VIR_NETWORK_UPDATE_AFFECT_LIVE, libvirt.VIR_NETWORK_SECTION_IP_DHCP_RANGE
            net.update(libvirt.VIR_NETWORK_UPDATE_COMMAND_ADD_LAST, section, -1, _range_xml(new["dhcp"]), flags)
            net.update(libvirt.VIR_NETWORK_UPDATE_COMMAND_DELETE, section, -1, _range_xml(live["dhcp"]), flags)
    return pending(net)


def _range_xml(rng):
    return ET.tostring(ET.Element("range", {"start": rng["debut"], "end": rng["fin"]}), encoding="unicode")


def _host_xml(mac, ip, name):
    attrs = {"mac": mac, "ip": ip}
    if name:
        attrs["name"] = name
    return ET.tostring(ET.Element("host", attrs), encoding="unicode")


def _flags(net):
    flags = libvirt.VIR_NETWORK_UPDATE_AFFECT_CONFIG
    if net.isActive():
        flags |= libvirt.VIR_NETWORK_UPDATE_AFFECT_LIVE
    return flags


def add_reservation(net, *, mac, ip, name=None):
    mac = (mac or "").strip().lower()
    if not MAC_RE.match(mac):
        raise EditError("Invalid MAC address (expected like 52:54:00:12:34:56)")
    if name and not HOSTNAME_RE.match(name):
        raise EditError("Invalid name: letters, digits and dashes, 63 characters at most")
    xml = net.XMLDesc(libvirt.VIR_NETWORK_XML_INACTIVE)
    info = describe_xml(xml)
    if not info["modifiable"]:
        raise EditError("This network has no DHCP server of its own: its addresses come from the host's network")
    if info["dhcp"] is None:
        raise EditError("Turn DHCP on for this network before reserving addresses")
    subnet = _subnet(_ipv4(ET.fromstring(xml)))
    addr = _addr(ip, "address")
    _usable(subnet.network, addr, "The address")
    if addr == subnet.ip:
        raise EditError(f"{addr} is the network's gateway")
    for r in info["reservations"]:
        if r["mac"] == mac:
            raise EditError(f"{mac} already has a reservation ({r['ip']})")
        if r["ip"] == str(addr):
            raise EditError(f"{addr} is already reserved for {r['mac']}")
    net.update(
        libvirt.VIR_NETWORK_UPDATE_COMMAND_ADD_LAST,
        libvirt.VIR_NETWORK_SECTION_IP_DHCP_HOST,
        -1,
        _host_xml(mac, str(addr), name),
        _flags(net),
    )


def delete_reservation(net, mac):
    mac = (mac or "").strip().lower()
    for r in describe_xml(net.XMLDesc(libvirt.VIR_NETWORK_XML_INACTIVE))["reservations"]:
        if r["mac"] == mac:
            net.update(
                libvirt.VIR_NETWORK_UPDATE_COMMAND_DELETE,
                libvirt.VIR_NETWORK_SECTION_IP_DHCP_HOST,
                -1,
                _host_xml(r["mac"], r["ip"], r["nom"]),
                _flags(net),
            )
            return True
    return False
