"""What Hyperlite keeps about a VM besides libvirt's definition, dropped once the VM is gone: deleted
(app/routers/vms/lifecycle.py, and the automatic clean-up of inactive VMs) or converted to a template
(app/routers/templates.py). One list for both, so a record added for VMs is never left behind by one of them (a
conversion used to leave the backup schedule, which then failed at every run, the rights, the pools...)."""

import logging
import xml.etree.ElementTree as ET

import libvirt

logger = logging.getLogger(__name__)


def reservations(root):
    """(network, MAC) of each interface of a domain's XML (an Element) that may hold a DHCP reservation."""
    found = []
    for iface in root.findall(".//interface[@type='network']"):
        mac_el, source_el = iface.find("mac"), iface.find("source")
        if mac_el is not None and source_el is not None and source_el.get("network"):
            found.append((source_el.get("network"), mac_el.get("address")))
    return found


def release_addresses(conn, found):
    """Remove the DHCP reservations `found` (reservations()) held, so the range is not exhausted over time. Never
    raises: a network already gone has nothing to release."""
    from app.core.network_alloc import release_static_ip

    for network, mac in found:
        try:
            release_static_ip(conn, network, mac)
        except (libvirt.libvirtError, ET.ParseError):
            logger.debug("Reservation of %s on %s not released", mac, network, exc_info=True)


def forget(name, node=None):
    """Drop every record of the VM `name` (on `node`, None: this one). Its backups are kept: they may be all that is
    left of it; its backup schedule is not, it would fail at every run."""
    from app.core import cloudinit_edit, object_meta, vm_boot
    from app.core.permissions import delete_acl_for_vm, remove_vm_from_all_pools
    from app.core.vm_meta import delete_vm_auto_cleanup, delete_vm_os_label, delete_vm_ssh_user
    from app.services import backup_service

    delete_vm_ssh_user(name)
    delete_vm_os_label(name)
    delete_acl_for_vm(name)
    remove_vm_from_all_pools(name)
    delete_vm_auto_cleanup(name)
    vm_boot.delete_setting(name, node)
    object_meta.delete("vm", name, node)
    if not node:
        cloudinit_edit.delete_state(name)
        backup_service.delete_schedule(name)
