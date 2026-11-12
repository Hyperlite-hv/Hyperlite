"""Numeric guest identifiers (VMID), unique in the cluster (docs/design/vmid.md).

Every VM and container gets one, from 100 up, never handed out twice: a counter in hyperlite-cfs (COUNTER), moved on
with a compare-and-set, so two nodes numbering at the same moment get different numbers. hyperlite-cfs runs on a
single node too, so the same counter serves it.

The number lives in the guest's own libvirt definition (its <metadata>), with the UUID it was given to: it follows the
guest through a migration, a rename and a restore in place. A copy (a clone, a template's deployment, an import of
another node's definition) carries the metadata with a new UUID, so it is numbered again rather than sharing the
number. hyperlite-daemon numbers every guest that has none at its start and then every NUMBER_S seconds, whatever
made it (the API, virsh, an import); the API reads the number with the definition it already parses.

Names stay identifiers: the API takes a VMID where it takes a name (app/main.py, resolve).
"""

import logging
import threading
import time
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

METADATA_NS = "urn:hyperlite:vmid:1"
METADATA_KEY = "hlvmid"
COUNTER = "/cluster/vmid-next"
FIRST = 100
NUMBER_S = 30


def of_root(root):
    """The guest's VMID from its parsed definition, or None: none recorded, or one recorded for another UUID (a copy
    of another guest's definition)."""
    el = root.find(f"metadata/{{{METADATA_NS}}}vmid")
    if el is None or not (el.text or "").strip().isdigit():
        return None
    if el.get("uuid") and el.get("uuid") != (root.findtext("uuid") or "").strip():
        return None
    return int(el.text.strip())


def allocate(client=None):
    """The next free VMID of the cluster (compare-and-set on the counter, tried again on a concurrent change)."""
    from app.core.cfs_client import MUST_NOT_EXIST, Conflict, NotFound

    if client is None:
        from app.repositories.cfs import shadow

        client = shadow._get_client()
    for _ in range(50):
        try:
            entry = client.get(COUNTER)
        except NotFound:
            try:
                client.put(COUNTER, str(FIRST + 1).encode(), MUST_NOT_EXIST)
                return FIRST
            except Conflict:
                continue
        value = int(entry.data.decode() or FIRST)
        try:
            client.put(COUNTER, str(value + 1).encode(), entry.version)
            return value
        except Conflict:
            time.sleep(0.05)
    raise RuntimeError("No VMID could be reserved: the counter kept changing")


def _metadata_xml(vmid, uuid):
    el = ET.Element("vmid", uuid=uuid)
    el.text = str(vmid)
    return ET.tostring(el, encoding="unicode")


def assign(domain, vmid):
    """Record `vmid` in the guest's definition, and in the running guest's too."""
    import libvirt

    flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
    if domain.isActive():
        flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
    domain.setMetadata(
        libvirt.VIR_DOMAIN_METADATA_ELEMENT,
        _metadata_xml(vmid, domain.UUIDString()),
        METADATA_KEY,
        METADATA_NS,
        flags,
    )


def number(conn, allocate_fn=allocate):
    """Give a VMID to every guest of `conn` that has none: [(name, vmid)]."""
    import libvirt

    numbered = []
    for domain in conn.listAllDomains():
        try:
            root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        except libvirt.libvirtError:
            continue  # gone meanwhile
        if of_root(root) is not None:
            continue
        vmid = allocate_fn()
        try:
            assign(domain, vmid)
        except libvirt.libvirtError as e:  # the number is lost, never handed out again: the next pass tries anew
            logger.warning("VMID %s not recorded for %s: %s", vmid, domain.name(), e)
            continue
        numbered.append((domain.name(), vmid))
    return numbered


def number_local():
    """Every VM and container of this node, numbered (hyperlite-daemon, root)."""
    import libvirt

    from app.core import libvirt_utils

    numbered = []
    with libvirt_utils.local_conn() as conn:
        numbered += number(conn)
    try:
        lxc = libvirt.open(libvirt_utils.LXC_URI)
    except libvirt.libvirtError:
        lxc = None  # no LXC driver on this node: no container to number
    if lxc is not None:
        try:
            numbered += number(lxc)
        finally:
            lxc.close()
    if numbered:
        logger.info("VMIDs given: %s", ", ".join(f"{name}={vmid}" for name, vmid in numbered))
        from app.core import inventory_cache

        inventory_cache.invalidate()
    return numbered


def start_numbering_loop():
    def loop():
        while True:
            try:
                number_local()
            except Exception:  # the next pass tries again; a guest without a VMID is still reached by its name
                logger.exception("Guests could not be numbered")
            time.sleep(NUMBER_S)

    threading.Thread(target=loop, name="vmid", daemon=True).start()


# ---- The API: a VMID where a name is taken ----


def _local_match(conn, number):
    """(name, None) of this node's guest numbered `number` on `conn`, or None."""
    import libvirt

    for domain in conn.listAllDomains():
        try:
            xml = domain.XMLDesc(0)
        except libvirt.libvirtError:
            logger.debug("A guest went while VMID %s was looked for", number)
            continue
        if of_root(ET.fromstring(xml)) == number:
            return domain.name()
    return None


def resolve(kind, text):
    """(name, node or None) of the guest `text` stands for: None when `text` is a name already (a guest of this node
    is called so), or no guest has that number. node: the member it runs on, None for this node. VMs are found on
    every member through the cluster's status (app/core/statd.py); containers on this node."""
    import libvirt

    from app.core import libvirt_utils

    number = int(text)
    if kind == "vm":
        conn = libvirt_utils.open_conn(readonly=True)
    else:
        conn = libvirt_utils.open_lxc_conn_if_any(readonly=True)
        if conn is None:
            return None
    try:
        try:
            conn.lookupByName(text)
            return None  # a guest of this node has that name: the name wins
        except libvirt.libvirtError:
            pass
        name = _local_match(conn, number)
        if name:
            return name, None
    finally:
        conn.close()
    if kind != "vm":
        return None
    from app.core import self_node, statd

    for node, status in statd.table().items():
        if node == self_node.name() or status.get("stale"):
            continue
        for vm in status.get("vms", []):
            if vm.get("vmid") == number:
                return vm["name"], node
    return None
