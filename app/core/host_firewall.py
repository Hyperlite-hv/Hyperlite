"""The firewall of the nodes themselves, at the cluster's and at each node's level, with security groups and IP sets (docs/design/host-firewall.md).

One document, an application setting replicated to every member (`host_firewall`), holds it all:

    {"enabled": bool, "policy": "accept" | "drop",
     "rules": [rule...],                      the cluster's, for every node
     "nodes": {"<node>": {"rules": [rule...]}},
     "groups": [{"name", "comment", "rules": [rule...]}],
     "ipsets": [{"name", "comment", "cidrs": ["10.0.0.0/8", ...]}]}

    rule: {"action": "accept" | "drop" | "reject", "protocol": "tcp" | "udp" | "icmp" | "all",
           "port": "22" | "8000-8010" | "80,443" (tcp and udp), "source": "any" | "<cidr>" | "+<ipset>",
           "comment", "enabled"}  or  {"group": "<name>", "enabled"}

hyperlite-daemon turns it into an nftables table of its own, `inet hyperlite`, on each node: its input chain only,
never libvirt's or iptables' tables. An accept there ends nothing for the other tables; a drop is final. Some rules
are pinned before the administrator's and cannot be removed: loopback, established connections, ICMPv6 (IPv6 needs
it), the cluster's members between them (Corosync, SSH, the API, migrations) and DHCP and DNS from the VMs of
libvirt's networks.

A change is applied first on the node it is made from, with the rollback timer of the host's network
(app/core/rollback_timer.py), and saved for the cluster once confirmed from the browser. Each daemon then applies the
saved document every FOLLOW_S seconds when it changed (`follow`), the members with no timer: the pinned rules keep
The cluster together whatever the rules say.
"""

import contextlib
import fcntl
import hashlib
import ipaddress
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import threading
import time

from app.core import paths, rollback_timer

logger = logging.getLogger(__name__)

SETTING = "host_firewall"
TABLE = "hyperlite"
STATE_DIR = paths.VAR / "host-firewall"  # root's: the change waiting for its confirmation, the ruleset it replaced
ROLLBACK_UNIT = "hyperlite-firewall-rollback"
TIMEOUT_MIN_S, TIMEOUT_MAX_S = 30, 600
FOLLOW_S = 15

ACTIONS = ("accept", "drop", "reject")
PROTOCOLS = ("tcp", "udp", "icmp", "all")
NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,31}$")
PORTS = re.compile(r"^[0-9]{1,5}(-[0-9]{1,5})?(,[0-9]{1,5}(-[0-9]{1,5})?){0,15}$")
MAX_RULES = 200

DEFAULT = {"enabled": False, "policy": "drop", "rules": [], "nodes": {}, "groups": [], "ipsets": []}
# What a first enable starts from, so that turning the firewall on locks nobody out: the dashboard, SSH and ping from
# anywhere, to narrow down afterwards.
STARTER_RULES = [
    {"action": "accept", "protocol": "tcp", "port": "8000", "source": "any", "comment": "Hyperlite (dashboard, API)"},
    {"action": "accept", "protocol": "tcp", "port": "22", "source": "any", "comment": "SSH"},
    {"action": "accept", "protocol": "icmp", "source": "any", "comment": "Ping"},
]


class FirewallError(ValueError):
    """A definition refused or a ruleset nftables did not take, with a message for the administrator."""


# ---- The document, checked ----


def _ports(value, where):
    if value in (None, ""):
        return None
    value = str(value).replace(" ", "")
    if not PORTS.match(value):
        raise FirewallError(f"{where}: « {value} » is not a port, a range (8000-8010) or a list (80,443)")
    for part in value.split(","):
        bounds = [int(p) for p in part.split("-")]
        if not all(1 <= b <= 65535 for b in bounds) or (len(bounds) == 2 and bounds[0] > bounds[1]):
            raise FirewallError(f"{where}: « {part} » is not a port range between 1 and 65535")
    return value


def _source(value, where, ipsets):
    if value in (None, "", "any"):
        return "any"
    if isinstance(value, str) and value.startswith("+"):
        if value[1:] not in ipsets:
            raise FirewallError(f"{where}: the IP set « {value[1:]} » does not exist")
        return value
    try:
        net = ipaddress.ip_network(value, strict=False)
    except (TypeError, ValueError):
        raise FirewallError(f"{where}: « {value} » is not an address, a network or +<IP set>") from None
    if net.version != 4:
        raise FirewallError(f"{where}: only IPv4 sources are filtered here")
    return str(net)


def _rule(rule, where, ipsets, groups, in_group=False):
    if not isinstance(rule, dict):
        raise FirewallError(f"{where}: a rule is an object")
    enabled = rule.get("enabled", True) is not False
    if "group" in rule:
        if in_group:
            raise FirewallError(f"{where}: a security group cannot include another")
        if rule["group"] not in groups:
            raise FirewallError(f"{where}: the security group « {rule['group']} » does not exist")
        return {"group": rule["group"], "enabled": enabled}
    action, protocol = rule.get("action"), rule.get("protocol", "all")
    if action not in ACTIONS:
        raise FirewallError(f"{where}: the action is accept, drop or reject")
    if protocol not in PROTOCOLS:
        raise FirewallError(f"{where}: the protocol is tcp, udp, icmp or all")
    port = _ports(rule.get("port"), where)
    if port and protocol not in ("tcp", "udp"):
        raise FirewallError(f"{where}: a port goes with tcp or udp")
    comment = str(rule.get("comment") or "")[:120]
    return {
        "action": action,
        "protocol": protocol,
        "port": port,
        "source": _source(rule.get("source"), where, ipsets),
        "comment": comment,
        "enabled": enabled,
    }


def normalize(doc):
    """The whole document checked; raises FirewallError with the first problem."""
    if not isinstance(doc, dict):
        raise FirewallError("The firewall is an object")
    ipsets, names = [], set()
    for s in doc.get("ipsets") or []:
        name = s.get("name") if isinstance(s, dict) else None
        if not isinstance(name, str) or not NAME.match(name) or name in names:
            raise FirewallError(f"« {name} » is not a free IP set name (letters, digits, - and _)")
        cidrs = []
        for c in s.get("cidrs") or []:
            src = _source(c, f"IP set {name}", set())
            if src == "any":
                raise FirewallError(f"IP set {name}: « {c} » is not an address or a network")
            cidrs.append(src)
        names.add(name)
        ipsets.append({"name": name, "comment": str(s.get("comment") or "")[:120], "cidrs": sorted(set(cidrs))})
    group_names = set()
    for g in doc.get("groups") or []:
        name = g.get("name") if isinstance(g, dict) else None
        if not isinstance(name, str) or not NAME.match(name) or name in group_names:
            raise FirewallError(f"« {name} » is not a free security group name (letters, digits, - and _)")
        group_names.add(name)
    groups = []
    for g in doc.get("groups") or []:
        rules = [_rule(r, f"group {g['name']}", names, group_names, in_group=True) for r in g.get("rules") or []]
        groups.append({"name": g["name"], "comment": str(g.get("comment") or "")[:120], "rules": rules})
    rules = [_rule(r, "cluster rule", names, group_names) for r in doc.get("rules") or []]
    nodes = {}
    for node, conf in (doc.get("nodes") or {}).items():
        if not isinstance(node, str) or not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9.-]{0,62}$", node):
            raise FirewallError(f"« {node} » is not a node name")
        node_rules = [_rule(r, f"rule of {node}", names, group_names) for r in (conf or {}).get("rules") or []]
        if node_rules:
            nodes[node] = {"rules": node_rules}
    if sum(len(r) for r in [rules, *[n["rules"] for n in nodes.values()], *[g["rules"] for g in groups]]) > MAX_RULES:
        raise FirewallError(f"At most {MAX_RULES} rules in all")
    policy = doc.get("policy", "drop")
    if policy not in ("accept", "drop"):
        raise FirewallError("The input policy is accept or drop")
    return {
        "enabled": doc.get("enabled") is True,
        "policy": policy,
        "rules": rules,
        "nodes": nodes,
        "groups": groups,
        "ipsets": ipsets,
    }


def used(doc, name, kind):
    """Where a group or an IP set is referenced: ["cluster", "<node>", "group <name>"]."""
    places = []

    def refers(rule):
        return rule.get("group") == name if kind == "group" else rule.get("source") == f"+{name}"

    if any(refers(r) for r in doc["rules"]):
        places.append("cluster")
    places += [node for node, conf in doc["nodes"].items() if any(refers(r) for r in conf["rules"])]
    if kind == "ipset":
        places += [f"group {g['name']}" for g in doc["groups"] if any(refers(r) for r in g["rules"])]
    return places


# ---- The nftables ruleset ----


def _expand(rules, groups):
    """The rules in order, a group's rules where it is referenced, the disabled ones left out."""
    by_name = {g["name"]: g for g in groups}
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        if "group" in rule:
            yield from (r for r in by_name[rule["group"]]["rules"] if r.get("enabled", True))
        else:
            yield rule


def _set_name(ipset):
    return f"ipset_{ipset}"


def _nft_rule(rule):
    words = []
    if rule["source"] != "any":
        words.append(
            f"ip saddr @{_set_name(rule['source'][1:])}"
            if rule["source"].startswith("+")
            else f"ip saddr {rule['source']}"
        )
    if rule["protocol"] == "icmp":
        words.append("meta l4proto icmp")
    elif rule["protocol"] in ("tcp", "udp"):
        if rule["port"]:
            words.append(f"{rule['protocol']} dport {{ {rule['port'].replace(',', ', ')} }}")
        else:
            words.append(f"meta l4proto {rule['protocol']}")
    verdict = {"accept": "accept", "drop": "drop", "reject": "reject"}[rule["action"]]
    comment = re.sub(r"[^\w .,:()/+'-]", "", rule.get("comment") or "")[:120]
    words.append(f'{verdict} comment "{comment}"' if comment else verdict)
    return " ".join(words)


def render(doc, node, members=(), vm_bridges=()):
    """The nftables script for `node`: the table removed, then made again when the firewall is on. One transaction
    (nft -f), so the node never runs half of it."""
    lines = [f"table inet {TABLE} {{}}", f"delete table inet {TABLE}"]
    if not doc["enabled"]:
        return "\n".join(lines) + "\n"
    lines.append(f"table inet {TABLE} {{")
    for s in doc["ipsets"]:
        elements = f" elements = {{ {', '.join(s['cidrs'])} }}" if s["cidrs"] else ""
        lines.append(f"  set {_set_name(s['name'])} {{ type ipv4_addr; flags interval;{elements} }}")
    lines.append(
        "  set members { type ipv4_addr; flags interval;"
        + (f" elements = {{ {', '.join(sorted(set(members)))} }}" if members else "")
        + " }"
    )
    lines.append("  chain input {")
    lines.append(f"    type filter hook input priority filter; policy {doc['policy']};")
    lines.append('    iif "lo" accept comment "pinned: loopback"')
    lines.append('    ct state established,related accept comment "pinned: established connections"')
    lines.append("    ct state invalid drop")
    lines.append('    meta l4proto ipv6-icmp accept comment "pinned: IPv6 needs ICMPv6"')
    lines.append('    ip saddr @members accept comment "pinned: the cluster\'s members"')
    if vm_bridges:
        names = ", ".join(f'"{b}"' for b in sorted(set(vm_bridges)))
        lines.append(
            f'    iifname {{ {names} }} udp dport {{ 53, 67 }} accept comment "pinned: DHCP and DNS for the VMs"'
        )
        lines.append(f'    iifname {{ {names} }} tcp dport 53 accept comment "pinned: DNS for the VMs"')
    own = doc["nodes"].get(node, {}).get("rules", [])
    for rule in _expand(doc["rules"] + own, doc["groups"]):
        lines.append(f"    {_nft_rule(rule)}")
    lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _nft(script, check=False):
    argv = ["nft", "-c", "-f", "-"] if check else ["nft", "-f", "-"]
    r = subprocess.run(argv, input=script, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise FirewallError(f"nftables refused the ruleset: {(r.stderr or r.stdout).strip()[-400:]}")


def _current_script():
    """What is in place now, as a script that puts it back."""
    r = subprocess.run(["nft", "list", "table", "inet", TABLE], capture_output=True, text=True, timeout=30)
    body = r.stdout if r.returncode == 0 else ""
    return f"table inet {TABLE} {{}}\ndelete table inet {TABLE}\n{body}"


# ---- What this node knows: its name, the members, the bridges of libvirt's networks ----


def _node():
    from app.core import self_node

    return self_node.name()


def _members():
    try:
        from app.core import cluster_lead, cluster_setup

        if cluster_lead.mode() != "cluster":
            return []
        conf = cluster_setup.shared_conf() or {}
    except Exception as e:  # nothing known: no member is let in by the pinned rule, the rules still apply
        logger.warning("Cluster members unknown for the firewall: %s", e)
        return []
    out = []
    for m in conf.get("members", []):
        with contextlib.suppress(ValueError, TypeError):
            out.append(str(ipaddress.ip_address(m["address"])))
    return out


def _vm_bridges():
    """The bridges of libvirt's NAT and routed networks, whose dnsmasq runs on the host."""
    import libvirt

    from app.core import libvirt_utils

    bridges = []
    try:
        with libvirt_utils.local_conn() as conn:
            for net in conn.listAllNetworks():
                xml = net.XMLDesc(0)
                forward = re.search(r"<forward mode='([^']+)'", xml)
                if forward and forward.group(1) not in ("nat", "route"):
                    continue  # a bridged or macvtap network: no dnsmasq of libvirt on the host
                found = re.search(r"<bridge name='([^']+)'", xml)
                if found:
                    bridges.append(found.group(1))
    except libvirt.libvirtError as e:  # no libvirt here: no VM network to serve
        logger.info("libvirt not read for the firewall: %s", e)
    return bridges


def ruleset(doc, node=None):
    return render(doc, node or _node(), _members(), _vm_bridges())


# ---- Security groups for the VMs and the networks: each group, as the traffic that enters a VM (its rules have no
# direction; for a VM, their source is where the traffic comes from) ----

GROUP_FILTER_PREFIX = "hyperlite-group-"
# A VM's own rules start at 300 (app/core/vm_firewall.py) and its default policy is at 999: the groups' rules, merged
# into the VM's filter by priority, come first.
GROUP_PRIORITY = 100
GROUP_MAX_EXPANDED = 199


def group_filter_name(name):
    return f"{GROUP_FILTER_PREFIX}{name}"


def _sources(rule, ipsets):
    """The networks a rule's source stands for: [None] for anywhere."""
    if rule["source"] == "any":
        return [None]
    if rule["source"].startswith("+"):
        return [ipaddress.ip_network(c, strict=False) for c in ipsets.get(rule["source"][1:], [])]
    return [ipaddress.ip_network(rule["source"], strict=False)]


def _port_parts(rule):
    """[(start, end)] for each port or range of a tcp or udp rule, [None] for all ports."""
    if not rule.get("port") or rule["protocol"] not in ("tcp", "udp"):
        return [None]
    return [tuple(int(b) for b in (part.split("-") * 2)[:2]) for part in rule["port"].split(",")]


def expand_group(doc, name):
    """The group's enabled rules, one per source network and port range: [{action, protocol, net, ports}]."""
    ipsets = {s["name"]: s["cidrs"] for s in doc["ipsets"]}
    group = next((g for g in doc["groups"] if g["name"] == name), None)
    out = []
    for rule in group["rules"] if group else []:
        if not rule.get("enabled", True):
            continue
        for net in _sources(rule, ipsets):
            for ports in _port_parts(rule):
                out.append({"action": rule["action"], "protocol": rule["protocol"], "net": net, "ports": ports})
    return out


def group_filter_xml(doc, name):
    """The libvirt nwfilter of a group, referenced from a VM's own filter (<filterref>)."""
    import xml.etree.ElementTree as ET

    root = ET.Element("filter", name=group_filter_name(name), chain="root")
    for i, r in enumerate(expand_group(doc, name)[:GROUP_MAX_EXPANDED]):
        el = ET.SubElement(root, "rule", action=r["action"], direction="in", priority=str(GROUP_PRIORITY + i))
        match = ET.SubElement(el, r["protocol"])
        if r["net"] is not None:
            match.set("srcipaddr", str(r["net"].network_address))
            match.set("srcipmask", str(r["net"].prefixlen))
        if r["ports"] is not None:
            match.set("dstportstart", str(r["ports"][0]))
            match.set("dstportend", str(r["ports"][1]))
    return ET.tostring(root, encoding="unicode")


def group_iptables_specs(doc, name, bridge):
    """The group's rules for a network's chain (app/core/network_firewall.py): traffic entering its VMs (-o bridge)."""
    specs = []
    for r in expand_group(doc, name):
        spec = ["-o", bridge]
        if r["net"] is not None:
            spec += ["-s", str(r["net"])]
        if r["protocol"] != "all":
            spec += ["-p", r["protocol"]]
        if r["ports"] is not None:
            start, end = r["ports"]
            spec += ["--dport", str(start) if start == end else f"{start}:{end}"]
        spec += ["-j", {"accept": "ACCEPT", "drop": "DROP", "reject": "REJECT"}[r["action"]]]
        specs.append(spec)
    return specs


def sync_groups(doc):
    """Every group defined as an nwfilter on this node (libvirt applies a changed one to the running VMs at once), the
    ones the document no longer has undefined unless a VM still references them, and the networks' chains rebuilt."""
    import libvirt

    from app.core import libvirt_utils, network_firewall

    wanted = {group_filter_name(g["name"]) for g in doc["groups"]}
    with libvirt_utils.local_conn() as conn:
        for g in doc["groups"]:
            conn.nwfilterDefineXML(group_filter_xml(doc, g["name"]))
        filters = conn.listAllNWFilters()
        # libvirt lets a filter another one references go (only a running VM's interface holds it): a stopped VM
        # whose group filter is gone would not start again. A group some VM's filter still calls stays as it was.
        referenced = set()
        for nwf in filters:
            if not nwf.name().startswith(GROUP_FILTER_PREFIX):
                referenced.update(re.findall(r"<filterref filter='([^']+)'", nwf.XMLDesc(0)))
        for nwf in filters:
            name = nwf.name()
            if not name.startswith(GROUP_FILTER_PREFIX) or name in wanted:
                continue
            if name in referenced:
                logger.warning("Security group filter %s kept: a VM's firewall still references it", name)
                continue
            try:
                nwf.undefine()
            except libvirt.libvirtError as e:  # held by a running VM's interface
                logger.warning("Security group filter %s kept: %s", name, e)
    network_firewall.reapply_local()


def unknown_groups(names):
    """The names among `names` the saved firewall has no security group for."""
    doc = saved() or DEFAULT
    known = {g["name"] for g in doc["groups"]}
    return [n for n in names if n not in known]


def _groups_digest(doc):
    return hashlib.sha256(json.dumps([doc["groups"], doc["ipsets"]], sort_keys=True).encode()).hexdigest()


# ---- Applying, confirming, rolling back, following the cluster ----


def _lock():
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(STATE_DIR / "lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _pending_path():
    return STATE_DIR / "pending.json"


def pending():
    try:
        data = json.loads(_pending_path().read_text())
    except (FileNotFoundError, ValueError):
        return None
    return {"id": data["id"], "deadline": data["deadline"], "document": data["document"]}


def _applied_path():
    return STATE_DIR / "applied"


def _mark_applied(script):
    _applied_path().write_text(hashlib.sha256(script.encode()).hexdigest())


def preview(doc):
    """The ruleset this node would run with `doc`, checked by nftables without applying it."""
    doc = normalize(doc)
    script = ruleset(doc)
    _nft(script, check=True)
    return {"ruleset": script}


def apply(doc, timeout_s):
    """Apply `doc` on this node, the rollback timer armed first: {id, deadline}. Saved for the cluster only once
    confirmed (the API saves it after `confirm`)."""
    if not TIMEOUT_MIN_S <= int(timeout_s) <= TIMEOUT_MAX_S:
        raise FirewallError(f"The confirmation delay is between {TIMEOUT_MIN_S} and {TIMEOUT_MAX_S} seconds")
    doc = normalize(doc)
    fd = _lock()
    try:
        if pending():
            raise FirewallError("A change is waiting for its confirmation: confirm it or roll it back first")
        script = ruleset(doc)
        _nft(script, check=True)
        change_id = secrets.token_hex(8)
        deadline = time.time() + int(timeout_s)
        data = {"id": change_id, "deadline": deadline, "document": doc, "before": _current_script()}
        tmp = STATE_DIR / ".pending.json.new"
        tmp.write_text(json.dumps(data))
        os.chmod(tmp, 0o600)
        os.replace(tmp, _pending_path())
        try:
            rollback_timer.start(ROLLBACK_UNIT, "app.core.host_firewall", change_id, timeout_s)
        except rollback_timer.TimerError as e:
            _pending_path().unlink(missing_ok=True)
            raise FirewallError(str(e)) from e
        try:
            _nft(script)
        except FirewallError:
            _rollback_locked(change_id)
            raise
        _mark_applied(script)
        logger.info("Host firewall change %s applied, to confirm within %ss", change_id, timeout_s)
        return {"id": change_id, "deadline": deadline}
    finally:
        os.close(fd)


def confirm(change_id):
    """Keep the change: {document} for the API to save for the cluster."""
    fd = _lock()
    try:
        waiting = pending()
        if waiting is None or waiting["id"] != change_id:
            raise FirewallError("No change of this id waits for its confirmation (it may have been rolled back)")
        rollback_timer.stop(ROLLBACK_UNIT)
        _pending_path().unlink(missing_ok=True)
        logger.info("Host firewall change %s confirmed", change_id)
        return {"confirmed": change_id, "document": waiting["document"]}
    finally:
        os.close(fd)


def rollback(change_id=None):
    fd = _lock()
    try:
        return _rollback_locked(change_id)
    finally:
        os.close(fd)


def _rollback_locked(change_id):
    try:
        data = json.loads(_pending_path().read_text())
    except (FileNotFoundError, ValueError):
        return {"rolled_back": None}
    if change_id is not None and data["id"] != change_id:
        return {"rolled_back": None}
    rollback_timer.stop(ROLLBACK_UNIT)
    try:
        _nft(data["before"])
        _mark_applied(data["before"])
    finally:
        _pending_path().unlink(missing_ok=True)
    logger.warning("Host firewall change %s rolled back", data["id"])
    return {"rolled_back": data["id"]}


def saved():
    """The document the cluster agreed on (the replicated setting), or the default."""
    from app.repositories import registry

    raw = registry.settings().sync.app_setting(SETTING)
    if not raw:
        return dict(DEFAULT)
    try:
        return normalize(json.loads(raw))
    except (ValueError, FirewallError) as e:
        logger.error("The saved host firewall is not valid, kept as it is on this node: %s", e)
        return None


def follow_once():
    """Apply the saved document when it differs from what this node runs; nothing while a change waits here."""
    if pending():
        return False
    doc = saved()
    if doc is None:
        return False
    groups_path = STATE_DIR / "groups"
    digest = _groups_digest(doc)
    try:
        current_groups = groups_path.read_text()
    except FileNotFoundError:
        current_groups = None
    if current_groups != digest:
        sync_groups(doc)
        STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        groups_path.write_text(digest)
        logger.info("Security groups defined for the VMs and the networks (%d)", len(doc["groups"]))
    script = ruleset(doc)
    digest = hashlib.sha256(script.encode()).hexdigest()
    try:
        if _applied_path().read_text() == digest:
            return False
    except FileNotFoundError:
        if not doc["enabled"]:
            _applied_path().parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _mark_applied(script)
            return False  # never on, nothing to remove
    fd = _lock()
    try:
        if pending():
            return False
        _nft(script)
        _mark_applied(script)
        logger.info("Host firewall applied from the cluster's settings (%s)", "on" if doc["enabled"] else "off")
        return True
    finally:
        os.close(fd)


def finish_interrupted():
    """At the daemon's start: a change past its deadline (the node rebooted before the timer fired) is rolled back;
    at boot the table is empty anyway, and `follow` brings back the saved document."""
    waiting = pending()
    if waiting and waiting["deadline"] < time.time():
        logger.warning("Host firewall change %s was not confirmed before the restart: dropped", waiting["id"])
        rollback(waiting["id"])


def start_follow_loop():
    def loop():
        while True:
            try:
                follow_once()
            except Exception:  # the next pass tries again; the rules in place stay meanwhile
                logger.exception("The host firewall could not follow the cluster's settings")
            time.sleep(FOLLOW_S)

    threading.Thread(target=loop, name="host-firewall", daemon=True).start()


def state():
    """What the Firewall pages show: the saved document, this node's name, the change waiting here, the ruleset in
    place."""
    r = subprocess.run(["nft", "list", "table", "inet", TABLE], capture_output=True, text=True, timeout=30)
    return {
        "document": saved() or dict(DEFAULT),
        "node": _node(),
        "pending": pending(),
        "active": r.returncode == 0,
        "starter_rules": STARTER_RULES,
    }


def main(argv):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if len(argv) == 2 and argv[0] == "rollback":
        print(json.dumps(rollback(argv[1])))
        return 0
    print("usage: python -m app.core.host_firewall rollback <id>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
