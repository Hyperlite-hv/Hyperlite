"""A new libvirt network's definition, from the fields of POST /networks: checked here and written here, for the API
(its answers) and for hyperlite-daemon, which defines the network as root (app/daemon/ops/network.py,
docs/design/privilege-separation.md section 13) and never takes libvirt XML from the API."""

import hashlib
import ipaddress
import re

# The definition is written with f-strings: every value that goes into it is checked against these first (an
# interface name of the kernel's IFNAMSIZ, an IPv4 address), or a quote in it would rewrite the XML.
IFACE_NAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{1,15}$")
IPV4_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")
MODES = ("nat", "isolated", "bridge")


class SpecError(ValueError):
    """A field of the network refused, with the message the API answers (422)."""


def valid_ipv4(addr):
    m = IPV4_RE.match(addr or "")
    return bool(m) and all(0 <= int(g) <= 255 for g in m.groups())


def bridge_dev(name):
    """The bridge of a routed network, derived from its name: a short prefix (4 characters, for a bit of readability)
    plus a SHA-1 hash of the FULL name (5 hex characters). Kernel interface names are limited to 15 usable characters
    (IFNAMSIZ=16 including the terminating NUL), so a plain truncation of the name either overflows the limit (libvirt
    then fails with "Numerical result out of range") or makes two networks with a common prefix collide (e.g.
    "guest-wifi-1" and "guest-wifi-2" would generate the same bridge). Two networks can only collide if their full
    names are identical, which is rejected earlier ("already exists")."""
    return f"virbr-{name[:4]}{hashlib.sha1(name.encode(), usedforsecurity=False).hexdigest()[:5]}"


def subnet_of(subnet_address, subnet_netmask):
    """The IPv4 network of a routed network's gateway address and netmask. Raises SpecError."""
    if not subnet_address or not valid_ipv4(subnet_address):
        raise SpecError("Invalid subnet_address (an IPv4 address is expected, e.g. '192.168.150.1')")
    try:
        return ipaddress.ip_network(f"{subnet_address}/{subnet_netmask}", strict=False)
    except ValueError:
        raise SpecError("Invalid subnet_netmask (e.g. '255.255.255.0')") from None


def network_xml(name, mode, bridge_name=None, subnet_address=None, subnet_netmask="255.255.255.0", dhcp_start=None,
                dhcp_end=None):  # fmt: skip
    """The libvirt definition of the network these fields describe. Raises SpecError. The name is checked by the
    caller (app/core/vm_builder.validate_name); a bridge-mode interface must exist on this host and not be Wi-Fi."""
    if mode not in MODES:
        raise SpecError("mode must be 'nat', 'isolated' or 'bridge'")
    if mode == "bridge":
        if not bridge_name or not IFACE_NAME_RE.match(bridge_name):
            raise SpecError(
                "Invalid bridge_name (a Linux interface name is expected: letters/digits/-/_/. , 15 characters max)"
            )
        from app.core.host_interfaces import find as find_interface

        iface = find_interface(bridge_name)
        if iface is None:
            raise SpecError(f"No interface '{bridge_name}' on this host")
        if not iface["usable"]:
            raise SpecError(f"'{bridge_name}' is a Wi-Fi card: a Wi-Fi link cannot carry bridged VMs")
        if iface["type"] == "bridge":
            # An existing Linux bridge: VMs are plugged into it, and the host reaches them.
            return f"<network><name>{name}</name><forward mode='bridge'/><bridge name='{bridge_name}'/></network>"
        # A NIC, bond or VLAN interface: macvtap puts the VMs straight on it, with no change to the host's network
        # configuration. The host itself cannot reach them through it (macvtap limitation).
        return (
            f"<network><name>{name}</name><forward mode='bridge'><interface dev='{bridge_name}'/></forward></network>"
        )
    subnet = subnet_of(subnet_address, subnet_netmask)
    dhcp_xml = ""
    if dhcp_start and dhcp_end:
        if not (valid_ipv4(dhcp_start) and valid_ipv4(dhcp_end)):
            raise SpecError("Invalid dhcp_start/dhcp_end")
        start, end = ipaddress.ip_address(dhcp_start), ipaddress.ip_address(dhcp_end)
        if start not in subnet or end not in subnet or start > end:
            raise SpecError(f"The DHCP range must lie inside {subnet}, its start before its end")
        dhcp_xml = f"<dhcp><range start='{dhcp_start}' end='{dhcp_end}'/></dhcp>"
    forward_xml = "<forward mode='nat'/>" if mode == "nat" else ""
    return (
        f"<network><name>{name}</name>{forward_xml}<bridge name='{bridge_dev(name)}' stp='on' delay='0'/>"
        f"<ip address='{subnet_address}' netmask='{subnet_netmask}'>{dhcp_xml}</ip></network>"
    )
