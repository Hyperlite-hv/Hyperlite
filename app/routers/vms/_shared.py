import logging
import re
import xml.etree.ElementTree as ET

import libvirt
from fastapi import APIRouter

from app.core import firmware, guest_agent, iscsi
from app.core.libvirt_utils import (
    get_vm_uptime_s,
)
from app.core.vm_meta import (
    all_vm_os_labels,
    all_vm_ssh_users,
    get_vm_os_label,
    get_vm_ssh_user,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vms", tags=["vms"])

TARGET_DEV_RE = re.compile(r"^[a-z]{2,4}[0-9]{0,2}$")

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
    # The guest's own answer first (Hyperlite Tools): it also knows a static IP or a bridged address that libvirt's
    # DHCP leases never see.
    agent_ip = guest_agent.ipv4(domain)
    if agent_ip:
        return agent_ip
    try:
        ifaces = domain.interfaceAddresses(libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_LEASE)
        for iface in ifaces.values():
            for addr in iface.get("addrs", []):
                if addr.get("type") == 0:
                    return addr.get("addr")
    except libvirt.libvirtError:
        logger.debug("Ignored exception in _get_ip()", exc_info=True)
    return None


def _lease_ipv4(mac, leases):
    for lease in leases.get((mac or "").lower(), ()):
        if lease.get("type") == libvirt.VIR_IP_ADDR_TYPE_IPV4 and lease.get("ipaddr"):
            return lease["ipaddr"]
    return None


def _dhcp_leases(conn):
    """{mac: [lease, ...]} over every active libvirt network: one call per network for the whole VM list, where a
    per-VM lease lookup costs one call per VM (an SSH round trip each on a remote node)."""
    leases = {}
    try:
        networks = conn.listAllNetworks(libvirt.VIR_CONNECT_LIST_NETWORKS_ACTIVE)
    except libvirt.libvirtError:
        logger.debug("Cannot list the networks for their DHCP leases", exc_info=True)
        return leases
    for net in networks:
        try:
            for lease in net.DHCPLeases():
                leases.setdefault((lease.get("mac") or "").lower(), []).append(lease)
        except libvirt.libvirtError:
            logger.debug("Cannot read the DHCP leases of %s", net.name(), exc_info=True)
    return leases


def _summary(domain, root, state, leases, ssh_users, os_labels):
    """The VM summary from one parsed XML: the list used to read each domain's XML three times and to run two
    SQLite queries per VM, about 4 s for 1,000 VMs."""
    name = domain.name()
    active = domain.ID() != -1
    vcpu_el = root.find("vcpu")
    nvcpu = int(vcpu_el.get("current") or vcpu_el.text) if vcpu_el is not None and vcpu_el.text else 0
    ip = None
    agent = None
    if active:
        agent = guest_agent.state_of_xml(root)
        if agent == guest_agent.CONNECTED:
            ip = guest_agent.ipv4_of_connected(domain)
        if not ip:
            for mac_el in root.findall("./devices/interface/mac"):
                ip = _lease_ipv4(mac_el.get("address"), leases)
                if ip:
                    break
    return {
        "nom": name,
        "id": domain.ID() if active else None,
        "uuid": domain.UUIDString(),
        "etat": STATE_NAMES.get(state, "inconnu"),
        "vcpu": nvcpu,
        "memoire_mo": round(_memory_kib(root) / 1024, 1),
        "ip": ip,
        "utilisateur_ssh": ssh_users.get(name),
        "uptime_s": get_vm_uptime_s(name) if active else None,
        "os": os_labels.get(name),
        # Exposed directly rather than letting the frontend guess from a derived state: the frontend used to deduce
        # "VM on a ZFS pool" from the list of EXISTING snapshots, which is wrong for a VM's very first snapshot.
        "stockage_zfs": bool(_zvol_disks_of_xml(root)),
        "stockage_iscsi": bool(iscsi.iscsi_disks_of_xml(root)),
        # Hyperlite Tools (qemu-guest-agent): "actif", "inactif", "non_configure", or None when the VM is stopped.
        "agent_invite": agent,
        # "bios", "uefi" or "uefi_secure" (see app/core/firmware.py): the UI explains why a running UEFI VM's
        # snapshot is refused.
        "firmware": firmware.of_domain(root),
    }


def _memory_kib(root):
    """The domain's maximum memory in KiB, what virDomainGetInfo reports as maxMem. libvirt always writes it in KiB
    in the XML it returns, whatever unit the definition used."""
    el = root.find("memory")
    return int(el.text) if el is not None and el.text else 0


def _domain_summary(domain):
    root = ET.fromstring(domain.XMLDesc(0))
    state = domain.state()[0]
    name = domain.name()
    leases = _dhcp_leases(domain.connect()) if domain.isActive() else {}
    return _summary(domain, root, state, leases, {name: get_vm_ssh_user(name)}, {name: get_vm_os_label(name)})


def _domain_summaries(conn):
    """Every VM of a connection: one bulk state call, one XML read per VM, one SQLite query per table and one
    lease read per network, instead of about ten calls per VM."""
    stats = conn.getAllDomainStats(libvirt.VIR_DOMAIN_STATS_STATE)
    leases = _dhcp_leases(conn) if any(d.ID() != -1 for d, _ in stats) else {}
    ssh_users = all_vm_ssh_users()
    os_labels = all_vm_os_labels()
    result = []
    for domain, values in stats:
        try:
            root = ET.fromstring(domain.XMLDesc(0))
        except libvirt.libvirtError:
            # Undefined between the two calls: it is simply no longer in the list.
            logger.debug("VM gone while listing", exc_info=True)
            continue
        result.append(_summary(domain, root, values.get("state.state"), leases, ssh_users, os_labels))
    return result


def _zvol_disks_of_domain(domain):
    """List of (pool, zvol_name) for all the BLOCK disks (ZFS zvols) of a domain. A
    VM created on a ZFS pool (see create_vm) has ALL its disks as zvols in the
    SAME pool, never mixed with qcow2 files in this project. Used to route
    snapshots to the native ZFS mechanism rather than the qcow2 internal
    snapshot (which only applies to FILE disks)."""
    return _zvol_disks_of_xml(ET.fromstring(domain.XMLDesc()))


def _zvol_disks_of_xml(root):
    specs = []
    for disk_el in root.findall(".//devices/disk"):
        if disk_el.get("type") != "block" or disk_el.get("device") != "disk":
            continue
        source_el = disk_el.find("source")
        dev = source_el.get("dev") if source_el is not None else None
        if not dev:
            continue
        parts = dev.strip("/").split("/")
        if len(parts) >= 4 and parts[0] == "dev" and parts[1] == "zvol":
            specs.append((parts[2], "/".join(parts[3:])))
    return specs
