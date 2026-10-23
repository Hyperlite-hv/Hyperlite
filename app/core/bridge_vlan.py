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
"""

import json
import logging
import re
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

METADATA_NS = "urn:hyperlite:vlan:1"
METADATA_KEY = "hlvlan"
SYS_NET = Path("/sys/class/net")
BRIDGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,14}$")
MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
TRUNKS_RE = re.compile(r"^\d{1,4}(-\d{1,4})?(\s*[,;]\s*\d{1,4}(-\d{1,4})?)*$")
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
    if text is None or text == "":
        return []
    text = str(text).strip()
    if not TRUNKS_RE.fullmatch(text):
        raise VlanError("Trunk VLANs: numbers and ranges from 1 to 4094, such as 10,20-30")
    ranges = []
    for part in re.split(r"\s*[,;]\s*", text):
        first, _, last = part.partition("-")
        first, last = int(first), int(last or first)
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


def apply_running(conn, node=None):
    """Every running VM of `conn`: what the service does when it starts, for VMs started while it was not running."""
    import libvirt

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


if __name__ == "__main__":
    if sys.argv[1:2] == ["hook"]:
        sys.exit(hook(sys.argv[2:], sys.stdin))
    sys.exit(2)
