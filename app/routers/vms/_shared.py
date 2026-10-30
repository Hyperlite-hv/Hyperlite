import logging
import re
import xml.etree.ElementTree as ET

import libvirt
from fastapi import APIRouter, Depends

from app.core import cluster_view, firmware, guest_agent, iscsi, lvm_storage, privileged, vm_snapshots
from app.core.libvirt_utils import (
    get_vm_uptime_s,
)
from app.core.vm_meta import (
    all_vm_os_labels,
    all_vm_ssh_users,
    get_vm_os_label,
    get_vm_ssh_user,
)
from app.daemon.registry import DaemonError

logger = logging.getLogger(__name__)

# ?node= on every route: the member that holds the object does the work (app/core/cluster_view.py, on_node).
router = APIRouter(prefix="/vms", tags=["vms"], dependencies=[Depends(cluster_view.on_node)])

TARGET_DEV_RE = re.compile(r"^[a-z]{2,4}[0-9]{0,2}$")

STATE_NAMES = {
    libvirt.VIR_DOMAIN_NOSTATE: "unknown",
    libvirt.VIR_DOMAIN_RUNNING: "active",
    libvirt.VIR_DOMAIN_BLOCKED: "blocked",
    libvirt.VIR_DOMAIN_PAUSED: "paused",
    libvirt.VIR_DOMAIN_SHUTDOWN: "shutting_down",
    libvirt.VIR_DOMAIN_SHUTOFF: "stopped",
    libvirt.VIR_DOMAIN_CRASHED: "crashed",
    libvirt.VIR_DOMAIN_PMSUSPENDED: "suspended",
}


def _agent_ips(domains, node=None):
    """{uuid: address or None} from the guest agents of these running VMs (guest_agent.ipv4_many): asking an agent
    needs a connection that may write, hyperlite-daemon's (app/daemon/ops/vm.py), on this node or on the VMs' node.
    Without an answer, no address: the DHCP lease is read instead."""
    if not domains:
        return {}
    remote = node if node and node != "local" else None
    try:
        return privileged.run(
            "vm.agent_ipv4", vms=[d.name() for d in domains], wait_s=guest_agent.LIST_WAIT_S, node=remote
        )
    except DaemonError as e:
        logger.warning("The guest agents' addresses were not read: %s", e.detail)
        return {}


def _get_ip(domain, node=None):
    # The guest's own answer first (Hyperlite Tools): it also knows a static IP or a bridged address that libvirt's
    # DHCP leases never see.
    agent_ip = _agent_ips([domain], node).get(domain.UUIDString()) if guest_agent.connected(domain) else None
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


def _summary(domain, root, state, leases, ssh_users, os_labels, agent_ips=None):
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
            ip = (agent_ips if agent_ips is not None else _agent_ips([domain])).get(domain.UUIDString())
        if not ip:
            for mac_el in root.findall("./devices/interface/mac"):
                ip = _lease_ipv4(mac_el.get("address"), leases)
                if ip:
                    break
    return {
        "name": name,
        "id": domain.ID() if active else None,
        "uuid": domain.UUIDString(),
        "state": STATE_NAMES.get(state, "unknown"),
        "vcpu": nvcpu,
        "memory_mb": round(_memory_kib(root) / 1024, 1),
        "ip": ip,
        "ssh_user": ssh_users.get(name),
        "uptime_s": get_vm_uptime_s(name) if active else None,
        "os": os_labels.get(name),
        # Exposed directly rather than letting the frontend guess from a derived state: the frontend used to deduce
        # "VM on a ZFS pool" from the list of EXISTING snapshots, which is wrong for a VM's very first snapshot.
        "zfs_storage": bool(vm_snapshots.zvol_disks(root)),
        "iscsi_storage": bool(iscsi.iscsi_disks_of_xml(root)),
        "lvm_storage": lvm_storage.on_lvm(root),
        # Hyperlite Tools (qemu-guest-agent): "active", "inactive", "not_configured", or None when the VM is stopped.
        "guest_agent": agent,
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


def _networks_of(root):
    """The libvirt networks a VM has an interface on, each once."""
    return {src.get("network") for src in root.findall("./devices/interface/source") if src.get("network")}


def _scan(conn, node=None):
    """Every VM of a connection, and the number of VMs on each libvirt network, from one pass: one bulk state call,
    one XML read per VM, one SQLite query per table and one lease read per network, instead of about ten calls per
    VM."""
    stats = conn.getAllDomainStats(libvirt.VIR_DOMAIN_STATS_STATE)
    leases = _dhcp_leases(conn) if any(d.ID() != -1 for d, _ in stats) else {}
    ssh_users = all_vm_ssh_users()
    os_labels = all_vm_os_labels()
    read = []
    for domain, values in stats:
        try:
            read.append((domain, ET.fromstring(domain.XMLDesc(0)), values.get("state.state")))
        except libvirt.libvirtError:
            # Undefined between the two calls: it is simply no longer in the list.
            logger.debug("VM gone while listing", exc_info=True)
    # The agents of all the running VMs are asked together, the list waiting a bounded time (guest_agent.ipv4_many).
    agent_ips = _agent_ips(
        [d for d, root, _ in read if d.ID() != -1 and guest_agent.state_of_xml(root) == guest_agent.CONNECTED], node
    )
    result, per_network = [], {}
    for domain, root, state in read:
        result.append(_summary(domain, root, state, leases, ssh_users, os_labels, agent_ips))
        for net in _networks_of(root):
            per_network[net] = per_network.get(net, 0) + 1
    return {"vms": result, "per_network": per_network}


def inventory(conn, node=None):
    """_scan of a node, shared with the requests of the same moment (app/core/inventory_cache.py). The lists are
    copies: callers may change them."""
    from app.core import inventory_cache

    scan = inventory_cache.get(("vms", node or "local"), lambda: _scan(conn, node))
    return {"vms": [dict(v) for v in scan["vms"]], "per_network": dict(scan["per_network"])}


def _domain_summaries(conn, node=None):
    return inventory(conn, node)["vms"]
