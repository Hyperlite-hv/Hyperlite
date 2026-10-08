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

import concurrent.futures
import ipaddress
import json
import logging
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import libvirt
import libvirt_qemu

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


# Asking an agent for its addresses. libvirt waits for an agent's answer without any limit by default: one guest whose
# agent hung (its channel still "connected") held the VM list for more than 13 minutes on a test node, and every
# later agent call on that VM waited 30 s for libvirt's lock. So the question is asked with a timeout, in a few
# worker threads, and a VM list waits LIST_WAIT_S for all of them together: an agent that answers later serves the
# next list, and meanwhile the VM keeps the address it last reported.
AGENT_TIMEOUT_S = 2
LIST_WAIT_S = 0.5
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="agent-ip")
_lock = threading.Lock()
_known = {}  # VM uuid -> the last address its agent reported (None: it reported none)
_pending = {}  # VM uuid -> the question in flight, never asked twice at once
_silent_until = {}  # VM uuid -> monotonic time before which an agent that did not answer is not asked again
RETRY_AFTER_S = 30


def _query_ipv4(domain):
    answer = libvirt_qemu.qemuAgentCommand(
        domain, json.dumps({"execute": "guest-network-get-interfaces"}), AGENT_TIMEOUT_S, 0
    )
    for iface in json.loads(answer).get("return") or []:
        for addr in iface.get("ip-addresses") or []:
            if addr.get("ip-address-type") != "ipv4":
                continue
            try:
                ip = ipaddress.ip_address(addr.get("ip-address"))
            except ValueError:
                continue
            if not (ip.is_loopback or ip.is_link_local):
                return str(ip)
    return None


def _ask(uuid, domain):
    try:
        ip = _query_ipv4(domain)
    except (libvirt.libvirtError, ValueError, AttributeError, TypeError):
        logger.debug("Agent did not report addresses", exc_info=True)
        with _lock:
            _silent_until[uuid] = time.monotonic() + RETRY_AFTER_S
        return  # the last known address stays
    with _lock:
        _known[uuid] = ip
        _silent_until.pop(uuid, None)


def ipv4_many(domains, wait=LIST_WAIT_S):
    """{uuid: address or None} for running VMs whose agent is connected, waiting at most `wait` for them all."""
    asked, uuids = [], []
    with _lock:
        for domain in domains:
            uuid = domain.UUIDString()
            uuids.append(uuid)
            future = _pending.get(uuid)
            # A question still in flight from an earlier list is a slow agent: it is not waited for again. An agent
            # that did not answer is left alone for RETRY_AFTER_S.
            if (future is None or future.done()) and time.monotonic() >= _silent_until.get(uuid, 0):
                _pending[uuid] = _pool.submit(_ask, uuid, domain)
                asked.append(_pending[uuid])
    if asked:
        concurrent.futures.wait(asked, timeout=wait)
    with _lock:
        return {uuid: _known.get(uuid) for uuid in uuids}


def forget():
    """Drop the addresses known so far (tests)."""
    with _lock:
        _known.clear()
        _pending.clear()
        _silent_until.clear()


def ipv4_of_connected(domain):
    """ipv4() for a VM already known to have its agent connected (the VM list read it from the XML it holds)."""
    return ipv4_many([domain]).get(domain.UUIDString())


# What an agent operation (shutdown, reboot, freeze for a consistent snapshot) may wait for the guest's answer before
# libvirt gives up and the caller falls back (ACPI, a crash-consistent snapshot). libvirt's own default is to wait
# forever: a hung agent held the request, and every later agent call on that VM.
AGENT_OPERATION_TIMEOUT_S = 10


def bound(domain):
    """Limit how long libvirt waits for this running VM's agent (it holds until the VM stops). False when libvirt
    could not even do that: an agent call is already stuck on this VM, and asking the agent again would wait for it
    (libvirt's lock, 30 s) before failing anyway."""
    try:
        domain.agentSetResponseTimeout(AGENT_OPERATION_TIMEOUT_S, 0)
        return True
    except AttributeError:
        return True  # a libvirt too old to set it: the agent is asked as before
    except libvirt.libvirtError:
        logger.warning("The guest agent of %s is busy: not asking it", domain.name(), exc_info=True)
        return False


def shutdown(domain):
    """Ask the guest to shut down: through the agent when it is there, else the ACPI button. Returns the method
    used ("agent" or "acpi"). An agent that fails to act falls back to ACPI rather than failing the request."""
    if connected(domain) and bound(domain):
        try:
            domain.shutdownFlags(libvirt.VIR_DOMAIN_SHUTDOWN_GUEST_AGENT)
            return "agent"
        except libvirt.libvirtError:
            logger.warning("Agent shutdown of %s failed, using ACPI", domain.name(), exc_info=True)
    domain.shutdown()
    return "acpi"


def reboot(domain):
    """Same as shutdown(), for a reboot."""
    if connected(domain) and bound(domain):
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
    if connected(domain) and bound(domain):
        try:
            return domain.snapshotCreateXML(snap_xml, flags | libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_QUIESCE), True
        except libvirt.libvirtError:
            logger.warning("Quiesced snapshot of %s failed, taking it without freeze", domain.name(), exc_info=True)
    return domain.snapshotCreateXML(snap_xml, flags), False
