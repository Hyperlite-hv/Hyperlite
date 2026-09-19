import logging
import re
import xml.etree.ElementTree as ET

import libvirt
from fastapi import APIRouter

from app.core.libvirt_utils import (
    get_vm_uptime_s,
)
from app.core.vm_meta import (
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
    try:
        ifaces = domain.interfaceAddresses(libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_LEASE)
        for iface in ifaces.values():
            for addr in iface.get("addrs", []):
                if addr.get("type") == 0:
                    return addr.get("addr")
    except libvirt.libvirtError:
        logger.debug("Ignored exception in _get_ip()", exc_info=True)
    return None


def _domain_summary(domain):
    state, maxmem, _mem, nvcpu, _cputime = domain.info()
    active = domain.isActive()
    return {
        "nom": domain.name(),
        "id": domain.ID() if active else None,
        "uuid": domain.UUIDString(),
        "etat": STATE_NAMES.get(state, "inconnu"),
        "vcpu": nvcpu,
        "memoire_mo": round(maxmem / 1024, 1),
        "ip": _get_ip(domain) if active else None,
        "utilisateur_ssh": get_vm_ssh_user(domain.name()),
        "uptime_s": get_vm_uptime_s(domain.name()) if active else None,
        "os": get_vm_os_label(domain.name()),
        # Exposed directly here rather than letting the frontend guess from a derived
        # state: the frontend used to deduce "VM on a ZFS pool" from the list of EXISTING
        # snapshots (etat_vm=='disque_seul'), which is wrong for the VERY FIRST snapshot
        # of a VM (an empty list, nothing to deduce), so the qcow2 wording ("memory
        # included automatically") was wrongly shown while it was being created.
        "stockage_zfs": bool(_zvol_disks_of_domain(domain)),
    }


def _zvol_disks_of_domain(domain):
    """List of (pool, zvol_name) for all the BLOCK disks (ZFS zvols) of a domain. A
    VM created on a ZFS pool (see create_vm) has ALL its disks as zvols in the
    SAME pool, never mixed with qcow2 files in this project. Used to route
    snapshots to the native ZFS mechanism rather than the qcow2 internal
    snapshot (which only applies to FILE disks)."""
    root = ET.fromstring(domain.XMLDesc())
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
