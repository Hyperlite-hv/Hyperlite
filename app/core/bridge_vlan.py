"""VLANs for VMs on a VLAN-aware Linux bridge (docs/design/network.md, 3.1 A), as Proxmox VE's `tag` and `trunks`.

libvirt applies an interface's <vlan> only on Open vSwitch and SR-IOV, and refuses it on a Linux bridge even when the
bridge filters VLANs ("vlan tag not supported for this connection type", checked on libvirt 10). So the VLAN of an
interface lives in the VM's own metadata (METADATA_NS, one <port> per MAC address), and Hyperlite sets it on the VM's
port of the bridge itself:
- when the VM starts, from libvirt's qemu hook (scripts/libvirt-hook-vlan, `started`), which also covers a VM
  arriving by migration: the metadata travels with the definition;
- when an interface is added or changed on a running VM;
- when the service starts, for the VMs already running.

A port is set as Proxmox VE's tap script does: the access VLAN as its PVID, untagged (VLAN 1 without one), the trunk
VLANs tagged. If setting it fails, the port is left with no VLAN at all rather than on the bridge's default one.

A bridge is made VLAN-aware as Proxmox VE's `bridge-vlan-aware yes` with `bridge-vids 2-4094` does: vlan_filtering on,
VLANs 2-4094 tagged on its uplinks (a NIC, a bond, a VLAN interface) and on the bridge itself; what is untagged (the
host's own address, the VMs and containers) stays on VLAN 1. The change is a trial until the dashboard confirms it from
a new request (start_trial, confirm_trial), and undone otherwise; confirmed, the bridge is listed in AWARE_FILE and made
VLAN-aware again at boot, before libvirt (`python -m app.core.bridge_vlan boot`, installer/hyperlite-vlan-bridges.service).

What needs root (`ip link`, `bridge vlan`, the list of bridges kept VLAN-aware) is run by hyperlite-daemon
(app/daemon/ops/network.py, docs/design/privilege-separation.md section 13), from a network's or a VM's name: it reads
the bridge and the VM's ports itself, so the API cannot point it at another interface. Reading the bridges' state
needs no root and stays in the API; the libvirt hook and the boot unit are root already.
"""

import json
import logging
import re
import secrets
import shlex
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

METADATA_NS = "urn:hyperlite:vlan:1"
METADATA_KEY = "hlvlan"
SYS_NET = Path("/sys/class/net")
BRIDGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,14}$")
MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
# One trunk entry: a VLAN or a range. The list is split on its separators first, so no pattern runs over the whole
# input (a pattern with repeated optional spaces backtracks on long runs of them).
TRUNK_PART_RE = re.compile(r"^(\d{1,4})(?:-(\d{1,4}))?$")
# A port without an access VLAN takes the bridge's default one, as on Proxmox VE.
DEFAULT_PVID = 1
TIMEOUT_S = 15


class VlanError(ValueError):
    """A VLAN setting refused, with a message for the user."""


def _run(args, node=None):
    if node:
        from app.core.cluster import get_node, node_ssh_options

        target = get_node(node)
        if target is None:
            raise VlanError(f"Node '{node}' not found")
        args = [
            "ssh",
            *node_ssh_options(),
            "-p",
            str(target["ssh_port"]),
            f"{target['ssh_user']}@{target['hostname']}",
            shlex.join(args),
        ]
    r = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT_S)
    if r.returncode != 0:
        raise VlanError((r.stderr or r.stdout).strip()[-400:] or f"{args[0]}: exit status {r.returncode}")
    return r.stdout


def filters_vlans(bridge, node=None):
    """Whether the host bridge `bridge` is VLAN-aware (vlan_filtering on)."""
    if not bridge or not BRIDGE_NAME_RE.fullmatch(bridge):
        return False
    path = SYS_NET / bridge / "bridge" / "vlan_filtering"
    if node:
        try:
            return _run(["cat", str(path)], node).strip() == "1"
        except (VlanError, OSError, subprocess.TimeoutExpired):
            return False
    try:
        return path.read_text().strip() == "1"
    except OSError:
        return False


def parse_trunks(text):
    """Trunk VLANs as Proxmox VE (`10;20-30`) or vSphere (`10,20-30`) write them: a sorted list of (first, last)."""
    if text is None or str(text).strip() == "":
        return []
    ranges = []
    for part in str(text).replace(";", ",").split(","):
        part = part.strip()
        m = TRUNK_PART_RE.fullmatch(part)
        if m is None:
            raise VlanError("Trunk VLANs: numbers and ranges from 1 to 4094, such as 10,20-30")
        first, last = int(m.group(1)), int(m.group(2) or m.group(1))
        if not 1 <= first <= last <= 4094:
            raise VlanError(f"Trunk VLANs: '{part}' is not a range from 1 to 4094")
        ranges.append((first, last))
    return sorted(ranges)


def format_trunks(ranges):
    return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in ranges) or None


def check_tag(tag):
    if tag is not None and not (isinstance(tag, int) and 1 <= tag <= 4094):
        raise VlanError("A VLAN tag is a number from 1 to 4094")
    return tag


# --- The VM's metadata: <vlans><port mac="…" tag="20" trunks="10,30-40"/></vlans> ---


def _ports_of(vlans_el):
    ports = {}
    if vlans_el is None:
        return ports
    for p in vlans_el:
        if p.tag.rsplit("}", 1)[-1] != "port" or not MAC_RE.fullmatch((p.get("mac") or "").lower()):
            continue
        tag = p.get("tag")
        ports[p.get("mac").lower()] = {
            "tag": int(tag) if tag and tag.isdigit() else None,
            "trunks": p.get("trunks") or None,
        }
    return ports


def ports_of_domain_xml(root):
    """{mac: {"tag", "trunks"}} from a domain's parsed XML (its <metadata>)."""
    return _ports_of(root.find(f"metadata/{{{METADATA_NS}}}vlans"))


def _metadata_xml(ports):
    body = "".join(
        f"<port mac='{mac}'"
        + (f" tag='{p['tag']}'" if p["tag"] is not None else "")
        + (f" trunks='{p['trunks']}'" if p["trunks"] else "")
        + "/>"
        for mac, p in sorted(ports.items())
    )
    return f"<vlans>{body}</vlans>"


def set_port(domain, mac, tag, trunks):
    """Record (or, with neither tag nor trunks, forget) the VLANs of the interface `mac` in the VM's definition, and
    in the running VM's too."""
    import libvirt

    mac = mac.lower()
    ports = ports_of_domain_xml(ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)))
    if tag is None and not trunks:
        ports.pop(mac, None)
    else:
        ports[mac] = {"tag": check_tag(tag), "trunks": format_trunks(parse_trunks(trunks))}
    flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
    if domain.isActive():
        flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
    domain.setMetadata(
        libvirt.VIR_DOMAIN_METADATA_ELEMENT,
        _metadata_xml(ports) if ports else None,
        METADATA_KEY if ports else None,
        METADATA_NS,
        flags,
    )


def remap_mac(root, old, new):
    """A clone's interface got a new MAC address: its VLANs follow it in the clone's metadata (a parsed domain XML)."""
    vlans_el = root.find(f"metadata/{{{METADATA_NS}}}vlans")
    if vlans_el is None:
        return
    for p in vlans_el:
        if (p.get("mac") or "").lower() == old.lower():
            p.set("mac", new.lower())


# --- The VM's ports on the bridge ---


def _port_vlans(tap, node=None):
    out = json.loads(_run(["bridge", "-j", "vlan", "show", "dev", tap], node) or "[]")
    return [v for entry in out for v in entry.get("vlans", [])]


def apply_port(tap, tag, trunks, node=None):
    """Set the VLANs of the bridge port `tap`: its access VLAN untagged (the default one without a tag), its trunk
    VLANs tagged. On failure, the port is left with no VLAN (no traffic) and the error raised."""
    ranges = parse_trunks(trunks)
    try:
        for v in _port_vlans(tap, node):
            first, last = v["vlan"], v.get("vlanEnd", v["vlan"])
            _run(["bridge", "vlan", "del", "dev", tap, "vid", f"{first}-{last}" if last != first else str(first)], node)
        _run(["bridge", "vlan", "add", "dev", tap, "vid", str(tag or DEFAULT_PVID), "pvid", "untagged"], node)
        for first, last in ranges:
            if first <= (tag or DEFAULT_PVID) <= last:
                # The access VLAN inside a trunk range: the range is split around it, as it stays untagged.
                pieces = [(first, (tag or DEFAULT_PVID) - 1), ((tag or DEFAULT_PVID) + 1, last)]
            else:
                pieces = [(first, last)]
            for a, b in pieces:
                if a <= b:
                    _run(["bridge", "vlan", "add", "dev", tap, "vid", f"{a}-{b}" if a != b else str(a)], node)
    except (VlanError, OSError, subprocess.TimeoutExpired, ValueError) as e:
        _isolate(tap, node)
        raise VlanError(f"VLANs of port {tap}: {e}") from e


def _isolate(tap, node=None):
    try:
        for v in _port_vlans(tap, node):
            first, last = v["vlan"], v.get("vlanEnd", v["vlan"])
            _run(["bridge", "vlan", "del", "dev", tap, "vid", f"{first}-{last}" if last != first else str(first)], node)
    except (VlanError, OSError, subprocess.TimeoutExpired, ValueError):
        logger.error("Port %s could not be isolated after a VLAN failure", tap, exc_info=True)


def interfaces_to_apply(root):
    """(tap, bridge, tag, trunks) for each interface of a RUNNING domain's parsed XML that has VLANs recorded."""
    ports = ports_of_domain_xml(root)
    found = []
    for iface in root.findall("devices/interface"):
        mac = iface.find("mac")
        target = iface.find("target")
        source = iface.find("source")
        if mac is None or target is None or source is None:
            continue
        port = ports.get((mac.get("address") or "").lower())
        if port is None:
            continue
        found.append((target.get("dev"), source.get("bridge"), port["tag"], port["trunks"]))
    return found


def apply_domain(root, node=None):
    """Set the VLANs of a running domain's ports (its live XML, parsed). Returns the errors, one per port."""
    errors = []
    for tap, bridge, tag, trunks in interfaces_to_apply(root):
        if not filters_vlans(bridge, node):
            # The VLAN cannot be honoured: the port carries nothing rather than the bridge's untagged traffic.
            _isolate(tap, node)
            errors.append(f"{tap}: bridge {bridge} does not filter VLANs, port left without traffic")
            continue
        try:
            apply_port(tap, tag, trunks, node)
        except VlanError as e:
            errors.append(str(e))
    return errors


def apply_running(conn=None, node=None):
    """Every running VM of `conn` (this node's by default): what the service does when it starts, for VMs started
    while it was not running."""
    import libvirt

    if conn is None:
        from app.core import libvirt_utils

        with libvirt_utils.local_conn() as conn:
            return apply_running(conn, node)
    errors = []
    for domain in conn.listAllDomains(libvirt.VIR_CONNECT_LIST_DOMAINS_ACTIVE):
        try:
            errors += apply_domain(ET.fromstring(domain.XMLDesc(0)), node)
        except (libvirt.libvirtError, ET.ParseError) as e:
            errors.append(f"{domain.name()}: {e}")
    for error in errors:
        logger.error("VLAN: %s", error)
    return errors


def hook(argv, stdin):
    """libvirt's qemu hook: `<domain> started begin -`, the live domain XML on stdin. It must not call libvirt."""
    if argv[1:3] != ["started", "begin"]:
        return 0
    try:
        errors = apply_domain(ET.fromstring(stdin.read()))
    except ET.ParseError as e:
        errors = [f"domain XML: {e}"]
    # libvirt keeps a hook's output only when it fails, and this one never fails the VM: the reason goes to the
    # system journal (`journalctl -t hyperlite-vlan`), a port that failed carrying no traffic.
    import syslog

    syslog.openlog("hyperlite-vlan")
    for error in errors:
        print(f"hyperlite-vlan {argv[0]}: {error}", file=sys.stderr)
        syslog.syslog(syslog.LOG_ERR, f"{argv[0]}: {error}")
    return 0


# --- Making a bridge VLAN-aware ---

UPLINK_VIDS = "2-4094"
TRIAL_S = 60
_trials = {}  # bridge -> (token, timer)
_trials_lock = threading.Lock()


def _aware_file():
    from app.core import paths

    return paths.ETC / "local" / "vlan-aware-bridges"


def is_linux_bridge(bridge):
    return bool(bridge) and BRIDGE_NAME_RE.fullmatch(bridge) is not None and (SYS_NET / bridge / "bridge").is_dir()


def linux_bridge_of_network(root):
    """The host Linux bridge a bridge network (its parsed XML) sits on, or None (NAT, isolated, macvtap, Open vSwitch)."""
    from app.core import network_edit

    forward = root.find("forward")
    bridge = root.find("bridge")
    if forward is None or forward.get("mode") != "bridge" or bridge is None or network_edit.carries_vlan_tags(root):
        return None
    name = bridge.get("name")
    return name if is_linux_bridge(name) else None


def network_bridge(network):
    """The Linux bridge of this node's libvirt network `network`, read here: what the daemon acts on."""
    import libvirt

    from app.core import libvirt_utils

    with libvirt_utils.local_conn() as conn:
        try:
            root = ET.fromstring(conn.networkLookupByName(network).XMLDesc(0))
        except libvirt.libvirtError:
            raise VlanError(f"Network '{network}' not found") from None
    bridge = linux_bridge_of_network(root)
    if bridge is None:
        raise VlanError(f"Network '{network}' is not on a Linux bridge of this host")
    return bridge


def apply_vm(vm):
    """The VLANs of the running VM `vm` of this node set on its bridge ports, from its live definition read here.
    Returns the errors, one per port; a stopped VM gets its VLANs from the libvirt hook when it starts."""
    import libvirt

    from app.core import libvirt_utils

    with libvirt_utils.local_conn() as conn:
        try:
            domain = conn.lookupByName(vm)
        except libvirt.libvirtError:
            raise VlanError(f"VM '{vm}' not found") from None
        if not domain.isActive():
            return []
        return apply_domain(ET.fromstring(domain.XMLDesc(0)))


def uplinks(bridge):
    """The bridge's ports that lead off the host: NICs, bonds and VLAN interfaces (not VM taps nor container veths)."""
    from app.core.host_interfaces import _kind

    brif = SYS_NET / bridge / "brif"
    try:
        names = sorted(p.name for p in brif.iterdir())
    except OSError:
        return []
    return [n for n in names if _kind(SYS_NET / n) in ("physical", "bond", "vlan")]


def enable(bridge):
    """vlan_filtering on, VLANs 2-4094 on the uplinks and the bridge itself. A failure undoes what was done."""
    if not is_linux_bridge(bridge):
        raise VlanError(f"'{bridge}' is not a Linux bridge of this host")
    try:
        _run(["ip", "link", "set", "dev", bridge, "type", "bridge", "vlan_filtering", "1"])
        for port in uplinks(bridge):
            _run(["bridge", "vlan", "add", "dev", port, "vid", UPLINK_VIDS])
        _run(["bridge", "vlan", "add", "dev", bridge, "vid", UPLINK_VIDS, "self"])
    except (VlanError, OSError, subprocess.TimeoutExpired) as e:
        disable(bridge)
        raise VlanError(f"Bridge {bridge} could not be made VLAN-aware: {e}") from e


def disable(bridge):
    """Back to a bridge that does not filter VLANs (every frame passes again, whatever its tag)."""
    for args in [["bridge", "vlan", "del", "dev", port, "vid", UPLINK_VIDS] for port in uplinks(bridge)] + [
        ["bridge", "vlan", "del", "dev", bridge, "vid", UPLINK_VIDS, "self"],
        ["ip", "link", "set", "dev", bridge, "type", "bridge", "vlan_filtering", "0"],
    ]:
        try:
            _run(args)
        except (VlanError, OSError, subprocess.TimeoutExpired):
            logger.warning("While undoing VLAN filtering on %s: %s failed", bridge, " ".join(args), exc_info=True)


def aware_bridges():
    """The bridges this node keeps VLAN-aware (confirmed from the dashboard)."""
    try:
        lines = _aware_file().read_text().splitlines()
    except FileNotFoundError:
        return []
    return [line.strip() for line in lines if line.strip() and BRIDGE_NAME_RE.fullmatch(line.strip())]


def _write_aware(bridges):
    path = _aware_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(f"{b}\n" for b in sorted(set(bridges))))
    tmp.replace(path)


def start_trial(bridge, seconds=TRIAL_S):
    """Make the bridge VLAN-aware now, undone after `seconds` unless confirm_trial gets the token back."""
    with _trials_lock:
        if bridge in _trials:
            raise VlanError(f"A change of bridge {bridge} is waiting for its confirmation")
        if filters_vlans(bridge):
            raise VlanError(f"Bridge {bridge} is VLAN-aware already")
        enable(bridge)
        token = secrets.token_urlsafe(24)
        timer = threading.Timer(seconds, _expire, args=(bridge, token))
        timer.daemon = True
        _trials[bridge] = (token, timer)
        timer.start()
    return token


def _expire(bridge, token):
    with _trials_lock:
        if _trials.get(bridge, (None,))[0] != token:
            return
        del _trials[bridge]
    logger.warning("Bridge %s: VLAN-aware change not confirmed in time, undone", bridge)
    disable(bridge)


def confirm_trial(bridge, token):
    """Keep the change: from now on the bridge is made VLAN-aware again at every boot."""
    with _trials_lock:
        current = _trials.get(bridge)
        if current is None or not secrets.compare_digest(current[0], str(token)):
            raise VlanError(f"No change of bridge {bridge} is waiting for this confirmation (it may have been undone)")
        current[1].cancel()
        del _trials[bridge]
    _write_aware([*aware_bridges(), bridge])


def turn_off(bridge):
    """Stop filtering VLANs on a bridge Hyperlite made VLAN-aware, and forget it."""
    if bridge not in aware_bridges():
        raise VlanError(f"Bridge {bridge} was not made VLAN-aware by Hyperlite")
    disable(bridge)
    _write_aware([b for b in aware_bridges() if b != bridge])


def boot():
    """At boot, before libvirt: the bridges kept VLAN-aware are made so again. Returns the errors."""
    errors = []
    for bridge in aware_bridges():
        if not is_linux_bridge(bridge):
            errors.append(f"bridge {bridge} is missing")
            continue
        if filters_vlans(bridge):
            continue
        try:
            enable(bridge)
        except VlanError as e:
            errors.append(str(e))
    for error in errors:
        logger.error("VLAN-aware bridges: %s", error)
    return errors


if __name__ == "__main__":
    if sys.argv[1:2] == ["hook"]:
        sys.exit(hook(sys.argv[2:], sys.stdin))
    if sys.argv[1:2] == ["boot"]:
        logging.basicConfig(level=logging.INFO)
        sys.exit(1 if boot() else 0)
    sys.exit(2)
