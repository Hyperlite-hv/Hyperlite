"""A VM's snapshots across a live migration on shared storage.

libvirt refuses to migrate a VM that has snapshots ("cannot migrate domain with 1 snapshots"), although on shared
storage the snapshots themselves (qcow2 internal snapshots) are in the disk files the destination opens. Only
libvirt's record of them stays on the source. So, as oVirt and virt-manager do: their records are set aside
(metadata only, nothing is deleted from the disks), the VM migrates, and the records are defined again on the
destination (VIR_DOMAIN_SNAPSHOT_CREATE_REDEFINE), the current one marked current. If the migration fails, they are
defined again on the source.
"""

import logging
import xml.etree.ElementTree as ET

import libvirt

logger = logging.getLogger(__name__)


def stash(domain):
    """Remove the VM's snapshot records and return what is needed to define them again: their XML, parents first,
    and the name of the current one. The snapshot data in the disks is not touched."""
    snaps = domain.listAllSnapshots(0)
    if not snaps:
        return None
    by_name = {s.getName(): s for s in snaps}
    parents = {}
    for name, snap in by_name.items():
        parent = ET.fromstring(snap.getXMLDesc(0)).findtext("parent/name")
        parents[name] = parent if parent in by_name else None
    ordered = []

    def visit(name):
        if name in ordered:
            return
        if parents[name]:
            visit(parents[name])
        ordered.append(name)

    for name in by_name:
        visit(name)
    xmls = [(name, by_name[name].getXMLDesc(libvirt.VIR_DOMAIN_SNAPSHOT_XML_SECURE)) for name in ordered]
    try:
        current = domain.snapshotCurrent(0).getName()
    except libvirt.libvirtError:
        current = None
    for name in reversed(ordered):  # children first: a parent's record goes last
        by_name[name].delete(libvirt.VIR_DOMAIN_SNAPSHOT_DELETE_METADATA_ONLY)
    return {"snapshots": xmls, "current": current}


def restore(domain, stashed):
    """Define the stashed records again on `domain` (the destination, or the source after a failed migration).
    Returns the names that could not be defined again."""
    if not stashed:
        return []
    failed = []
    for name, xml in stashed["snapshots"]:
        flags = libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_REDEFINE
        if name == stashed["current"]:
            flags |= libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_CURRENT
        try:
            domain.snapshotCreateXML(xml, flags)
        except libvirt.libvirtError:
            logger.warning("Could not define snapshot %s again on %s", name, domain.name(), exc_info=True)
            failed.append(name)
    return failed
