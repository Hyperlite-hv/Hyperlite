"""Hyperlite Tools: what Hyperlite does through the QEMU guest agent running inside a VM.

"Hyperlite Tools" is only the name shown to the user (like VMware Tools); underneath it is the standard
qemu-guest-agent (Linux: the `qemu-guest-agent` package; Windows: the virtio-win guest tools), talking over the
`org.qemu.guest_agent.0` virtio channel that every VM created by Hyperlite already declares (vm_builder.py).

With the agent:
  - a shutdown or reboot asks the guest OS directly, which works where the ACPI button is ignored (a Windows
    sign-in screen, a guest without acpid);
  - the IP address comes from the guest itself, also with a static IP or on a bridged network where libvirt
    sees no DHCP lease;
  - a hot backup freezes the guest file systems while its snapshot is taken, so the copy is consistent.
Without it, everything keeps working as before (ACPI, DHCP lease, crash-consistent snapshot).

Whether the agent is there is read from libvirt's view of the channel (state='connected' in the live XML), never
by calling the agent: a call to a hung agent could block a request, and the VM list asks for every VM.
"""

import ipaddress
import logging
import xml.etree.ElementTree as ET

import libvirt

logger = logging.getLogger(__name__)

CHANNEL = "org.qemu.guest_agent.0"

# Wire values of `agent_invite` in the VM summary.
CONNECTED = "actif"  # the agent answers on the channel
NOT_RUNNING = "inactif"  # the channel exists, but no agent listens (not installed or not started)
NO_CHANNEL = "non_configure"  # the VM has no agent channel (created outside Hyperlite, for example)


def state(domain):
    """CONNECTED, NOT_RUNNING or NO_CHANNEL for a running VM; None for a stopped one (nothing to know)."""
    try:
        if not domain.isActive():
            return None
        root = ET.fromstring(domain.XMLDesc(0))
    except libvirt.libvirtError:
        logger.debug("Cannot read the agent channel", exc_info=True)
        return None
    return state_of_xml(root)


def state_of_xml(root):
    """state() for a running VM whose live XML is already parsed."""
    for target in root.findall("./devices/channel/target"):
        if target.get("name") == CHANNEL:
            return CONNECTED if target.get("state") == "connected" else NOT_RUNNING
    return NO_CHANNEL


def connected(domain):
    return state(domain) == CONNECTED


def ipv4(domain):
    """The guest's first routable IPv4 address as the agent reports it, or None."""
    if not connected(domain):
        return None
    return ipv4_of_connected(domain)


def ipv4_of_connected(domain):
    """ipv4() for a VM already known to have its agent connected (the VM list read it from the XML it holds)."""
    try:
        ifaces = domain.interfaceAddresses(libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_AGENT)
    except libvirt.libvirtError:
        logger.debug("Agent did not report addresses", exc_info=True)
        return None
    for iface in ifaces.values():
        for addr in iface.get("addrs") or []:
            if addr.get("type") != libvirt.VIR_IP_ADDR_TYPE_IPV4:
                continue
            try:
                ip = ipaddress.ip_address(addr.get("addr"))
            except ValueError:
                continue
            if not (ip.is_loopback or ip.is_link_local):
                return str(ip)
    return None


def shutdown(domain):
    """Ask the guest to shut down: through the agent when it is there, else the ACPI button. Returns the method
    used ("agent" or "acpi"). An agent that fails to act falls back to ACPI rather than failing the request."""
    if connected(domain):
        try:
            domain.shutdownFlags(libvirt.VIR_DOMAIN_SHUTDOWN_GUEST_AGENT)
            return "agent"
        except libvirt.libvirtError:
            logger.warning("Agent shutdown of %s failed, using ACPI", domain.name(), exc_info=True)
    domain.shutdown()
    return "acpi"


def reboot(domain):
    """Same as shutdown(), for a reboot."""
    if connected(domain):
        try:
            domain.reboot(libvirt.VIR_DOMAIN_REBOOT_GUEST_AGENT)
            return "agent"
        except libvirt.libvirtError:
            logger.warning("Agent reboot of %s failed, using ACPI", domain.name(), exc_info=True)
    domain.reboot()
    return "acpi"


def quiesced_snapshot(domain, snap_xml, flags):
    """snapshotCreateXML with the guest file systems frozen (VIR_DOMAIN_SNAPSHOT_CREATE_QUIESCE) when the agent is
    there, so the snapshot is consistent. If the freeze fails (an agent without fsfreeze support, a guest busy
    thawing), the snapshot is retried without it: a crash-consistent backup is better than no backup.
    Returns (snapshot, quiesced)."""
    if connected(domain):
        try:
            return domain.snapshotCreateXML(snap_xml, flags | libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_QUIESCE), True
        except libvirt.libvirtError:
            logger.warning("Quiesced snapshot of %s failed, taking it without freeze", domain.name(), exc_info=True)
    return domain.snapshotCreateXML(snap_xml, flags), False
