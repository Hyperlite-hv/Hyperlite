"""The host's own bridges, bonds and VLAN interfaces, set from the dashboard (docs/design/network.md, section 8).

Hyperlite writes one file, /etc/network/interfaces.d/hyperlite, sourced by Debian's /etc/network/interfaces, and
never the administrator's own files: an interface configured there is listed, but Hyperlite neither changes it nor
takes it as a bridge port or a bond slave. The syntax is the one ifupdown and ifupdown2 both read.

A change is applied interface by interface (ifdown with the old file, ifup with the new one), only for the interfaces
whose definition changed, and then has to be confirmed: a systemd timer, started before anything changes, puts the
previous file back and brings the previous interfaces up unless the confirmation stops it in time. The confirmation
comes from the browser, over the network as it is after the change, so a change that cuts the administrator off is
undone by itself. The timer runs this module (`python -m app.core.host_network rollback <id>`), so it works without
the API or hyperlite-daemon; the daemon also finishes, at its start, a rollback a reboot interrupted.

Run as root, by hyperlite-daemon (app/daemon/ops/host.py).
"""

import difflib
import fcntl
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

from app.core import host_interfaces, paths, rollback_timer

logger = logging.getLogger(__name__)

ETC_NETWORK = Path(os.environ.get("HYPERLITE_NETWORK_DIR") or "/etc/network")
MANAGED_NAME = "hyperlite"
STATE_DIR = paths.VAR / "host-network"  # root's: the pending change and the file it replaced
ROLLBACK_UNIT = "hyperlite-network-rollback"
TIMEOUT_MIN_S, TIMEOUT_MAX_S, TIMEOUT_DEFAULT_S = 30, 600, 60
HEADER = (
    "# Written by Hyperlite (the node's Network page). Changes made here by hand are replaced at the next change made\n"
    "# from the dashboard; the interfaces of /etc/network/interfaces stay the administrator's.\n"
)

KINDS = ("bridge", "bond", "vlan")
BOND_MODES = ("balance-rr", "active-backup", "balance-xor", "broadcast", "802.3ad", "balance-tlb", "balance-alb")
HASH_POLICIES = ("layer2", "layer2+3", "layer3+4")
# A Linux interface name (15 bytes at most); a VLAN interface is <device>.<vid>.
NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,14}$")
VLAN_NAME = re.compile(r"^([a-zA-Z][a-zA-Z0-9_-]{0,9})\.([0-9]{1,4})$")


class NetworkError(ValueError):
    """A change refused or failed, with a message for the administrator."""


# ---- The definition: a list of interfaces, checked, then written as ifupdown stanzas ----


def _address(value):
    if value in (None, "", "manual"):
        return "manual"
    if value == "dhcp":
        return "dhcp"
    try:
        iface = ipaddress.ip_interface(value)
    except ValueError:
        raise NetworkError(f"« {value} » is not an address with its prefix (for example 192.168.10.2/24)") from None
    if iface.version != 4:
        raise NetworkError("Only IPv4 addresses are set here")
    if "/" not in value:
        raise NetworkError(f"« {value} » needs its prefix (for example {value}/24)")
    return str(iface)


def normalize(spec):
    """One interface of a request, checked alone: its kind, name, members and address. Raises NetworkError."""
    if not isinstance(spec, dict):
        raise NetworkError("Each interface is an object")
    kind = spec.get("type")
    if kind not in KINDS:
        raise NetworkError(f"Unknown interface type « {kind} » (bridge, bond or vlan)")
    out = {"type": kind}
    if kind == "vlan":
        raw, vid = spec.get("raw_device"), spec.get("vid")
        if not isinstance(raw, str) or not NAME.match(raw) or len(raw) > 10:
            raise NetworkError("A VLAN interface needs the device it runs on (a NIC, a bond or a bridge)")
        if not isinstance(vid, int) or isinstance(vid, bool) or not 1 <= vid <= 4094:
            raise NetworkError("A VLAN id is between 1 and 4094")
        out.update(name=f"{raw}.{vid}", raw_device=raw, vid=vid)
    else:
        name = spec.get("name")
        if not isinstance(name, str) or not NAME.match(name):
            raise NetworkError(f"« {name} » is not an interface name (letters, digits, - and _, 15 at most)")
        out["name"] = name
    if kind == "bridge":
        ports = spec.get("ports") or []
        if not isinstance(ports, list) or not all(isinstance(p, str) and NAME.match(p.split(".")[0]) for p in ports):
            raise NetworkError("A bridge's ports are interface names")
        out["ports"] = sorted(set(ports))
    if kind == "bond":
        slaves = spec.get("slaves") or []
        if not isinstance(slaves, list) or not slaves or not all(isinstance(s, str) and NAME.match(s) for s in slaves):
            raise NetworkError("A bond needs at least one network card")
        mode = spec.get("mode") or "active-backup"
        if mode not in BOND_MODES:
            raise NetworkError(f"Unknown bond mode « {mode} »")
        policy = spec.get("hash_policy") or "layer2"
        if policy not in HASH_POLICIES:
            raise NetworkError(f"Unknown hash policy « {policy} »")
        out.update(slaves=sorted(set(slaves)), mode=mode, hash_policy=policy)
    out["address"] = _address(spec.get("address"))
    gateway = spec.get("gateway") or None
    if gateway is not None:
        if out["address"] in ("manual", "dhcp"):
            raise NetworkError(f"{out['name']}: a gateway goes with a static address")
        try:
            gw = ipaddress.ip_address(gateway)
        except ValueError:
            raise NetworkError(f"« {gateway} » is not an address") from None
        if gw not in ipaddress.ip_interface(out["address"]).network:
            raise NetworkError(f"{out['name']}: the gateway {gateway} is not in {out['address']}")
        gateway = str(gw)
    out["gateway"] = gateway
    mtu = spec.get("mtu")
    if mtu is not None and (not isinstance(mtu, int) or isinstance(mtu, bool) or not 576 <= mtu <= 9216):
        raise NetworkError(f"{out['name']}: the MTU is between 576 and 9216")
    out["mtu"] = mtu
    out["autostart"] = spec.get("autostart", True) is not False
    return out


def render(interfaces):
    """The managed file's text for these (already checked) interfaces: bonds, then VLANs, then bridges, the order
    in which they come up."""
    order = {"bond": 0, "vlan": 1, "bridge": 2}
    out = [HEADER]
    for i in sorted(interfaces, key=lambda i: (order[i["type"]], i["name"])):
        out.append(_stanza(i))
    return "\n".join(out)


def _stanza(i):
    method = "static" if i["address"] not in ("manual", "dhcp") else i["address"]
    lines = []
    if i["autostart"]:
        lines.append(f"auto {i['name']}")
    lines.append(f"iface {i['name']} inet {method}")
    if method == "static":
        lines.append(f"    address {i['address']}")
        if i["gateway"]:
            lines.append(f"    gateway {i['gateway']}")
    if i["type"] == "bridge":
        lines.append(f"    bridge_ports {' '.join(i['ports']) or 'none'}")
        lines.append("    bridge_stp off")
        lines.append("    bridge_fd 0")
    elif i["type"] == "bond":
        lines.append(f"    bond-slaves {' '.join(i['slaves'])}")
        lines.append(f"    bond-mode {i['mode']}")
        lines.append("    bond-miimon 100")
        if i["mode"] in ("802.3ad", "balance-xor"):
            lines.append(f"    bond-xmit-hash-policy {i['hash_policy']}")
    elif i["type"] == "vlan":
        lines.append(f"    vlan-raw-device {i['raw_device']}")
    if i["mtu"]:
        lines.append(f"    mtu {i['mtu']}")
    return "\n".join(lines) + "\n"


def parse(text):
    """The interfaces a managed file defines, read back (the file Hyperlite wrote; nothing else is understood)."""
    interfaces, current, autos = [], None, set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        words = line.split()
        if words[0] == "auto":
            autos.update(words[1:])
        elif words[0] == "iface" and len(words) >= 4:
            current = {"name": words[1], "address": words[3] if words[3] in ("dhcp", "manual") else None}
            interfaces.append(current)
        elif current is not None:
            current.setdefault("_options", {})[words[0]] = words[1:]
    out = []
    for i in interfaces:
        opts = i.pop("_options", {})
        spec = {"name": i["name"], "address": " ".join(opts.get("address", [])) or i["address"]}
        spec["gateway"] = " ".join(opts.get("gateway", [])) or None
        spec["mtu"] = int(opts["mtu"][0]) if opts.get("mtu") else None
        spec["autostart"] = i["name"] in autos
        if "bridge_ports" in opts:
            spec.update(type="bridge", ports=[p for p in opts["bridge_ports"] if p != "none"])
        elif "bond-slaves" in opts:
            spec.update(
                type="bond",
                slaves=opts["bond-slaves"],
                mode=(opts.get("bond-mode") or ["active-backup"])[0],
                hash_policy=(opts.get("bond-xmit-hash-policy") or ["layer2"])[0],
            )
        elif "vlan-raw-device" in opts:
            raw = opts["vlan-raw-device"][0]
            spec.update(type="vlan", raw_device=raw, vid=int(i["name"].rsplit(".", 1)[1]))
        else:
            continue
        out.append(normalize(spec))
    return out


# ---- What the administrator's own files configure, and what the host has ----


def managed_path():
    return ETC_NETWORK / "interfaces.d" / MANAGED_NAME


def _admin_files():
    files = [ETC_NETWORK / "interfaces"]
    folder = ETC_NETWORK / "interfaces.d"
    if folder.is_dir():
        files += sorted(p for p in folder.iterdir() if p.is_file() and p.name != MANAGED_NAME)
    return [f for f in files if f.is_file()]


def admin_configured():
    """{interface: file} for every interface the administrator's own files name (an iface stanza, or a port, slave
    or raw device of one): Hyperlite leaves them alone."""
    named = {}
    for path in _admin_files():
        try:
            text = path.read_text()
        except OSError:
            continue
        for raw in text.splitlines():
            words = raw.split("#", 1)[0].split()
            if not words:
                continue
            if words[0] in ("iface", "auto", "allow-hotplug") and len(words) >= 2:
                for name in words[1:] if words[0] != "iface" else words[1:2]:
                    named.setdefault(name, str(path))
            elif words[0] in ("bridge_ports", "bridge-ports", "bond-slaves", "slaves", "vlan-raw-device"):
                for name in words[1:]:
                    if name not in ("none", "all"):
                        named.setdefault(name, str(path))
    named.pop("lo", None)
    return named


def current():
    """The interfaces of the managed file now."""
    path = managed_path()
    try:
        return parse(path.read_text())
    except FileNotFoundError:
        return []


def tools():
    """What this host can bring up: bridges need bridge-utils' hook for ifupdown, bonds ifenslave's."""
    hooks = ETC_NETWORK / "if-pre-up.d"
    ifupdown2 = Path("/usr/share/ifupdown2").exists()
    return {
        "ifupdown": bool(shutil.which("ifup")),
        "ifupdown2": ifupdown2,
        "bridge": ifupdown2 or (hooks / "bridge").exists(),
        "bond": ifupdown2 or (hooks / "ifenslave").exists(),
        "vlan": True,
    }


def check(interfaces, host=None, admin=None, used_by_networks=None, kit=None):
    """The whole new definition checked against the host: names, members, gateways, the tools it needs. Returns the
    normalized interfaces. Raises NetworkError with the first problem."""
    host = {i["name"]: i for i in (host if host is not None else host_interfaces.list_host_interfaces())}
    admin = admin_configured() if admin is None else admin
    kit = tools() if kit is None else kit
    wanted = [normalize(i) for i in interfaces]
    names = [i["name"] for i in wanted]
    duplicate = next((n for n in names if names.count(n) > 1), None)
    if duplicate:
        raise NetworkError(f"{duplicate} is defined twice")
    mine = {i["name"] for i in current()}
    members = {}
    for i in wanted:
        if i["name"] in admin:
            raise NetworkError(f"{i['name']} is configured in {admin[i['name']]}: Hyperlite does not change that file")
        if i["name"] in host and i["name"] not in mine and host[i["name"]]["type"] != i["type"]:
            raise NetworkError(f"{i['name']} already exists on this node")
        if i["type"] == "bridge" and not kit["bridge"]:
            raise NetworkError("Bridges need the bridge-utils package on this node")
        if i["type"] == "bond" and not kit["bond"]:
            raise NetworkError("Bonds need the ifenslave package on this node")
        for member in i.get("ports", []) + i.get("slaves", []) + ([i["raw_device"]] if i["type"] == "vlan" else []):
            if member == i["name"]:
                raise NetworkError(f"{i['name']} cannot be its own member")
            role = "raw" if i["type"] == "vlan" else "member"
            if role == "member":
                if member in members:
                    raise NetworkError(f"{member} is already a member of {members[member]}")
                members[member] = i["name"]
                if member in admin:
                    raise NetworkError(
                        f"{member} is configured in {admin[member]}: take it out of that file before using it here"
                    )
            if member not in host and member not in names:
                raise NetworkError(f"{member} does not exist on this node")
            if member in host and host[member]["type"] == "wifi":
                raise NetworkError(f"{member} is a Wi-Fi card: it cannot be bridged or bonded")
            kind = host[member]["type"] if member in host else next(w["type"] for w in wanted if w["name"] == member)
            if i["type"] == "bond" and kind != "physical":
                raise NetworkError(f"{member} is not a network card: a bond takes network cards only")
            if i["type"] == "bridge" and kind == "bridge":
                raise NetworkError(f"{member} is a bridge: a bridge cannot be the port of another")
            if i["type"] == "vlan" and kind == "vlan":
                raise NetworkError(f"{member} is a VLAN interface: a VLAN sits on a card, a bond or a bridge")
    gateways = [i for i in wanted if i["gateway"]]
    if len(gateways) > 1:
        raise NetworkError("Only one interface can have a gateway")
    if gateways and _default_route_elsewhere(mine | set(names)):
        raise NetworkError("This node already has a default route through an interface Hyperlite does not manage")
    used = used_by_networks if used_by_networks is not None else {}
    for name in mine - set(names):
        if name in used:
            raise NetworkError(f"{name} is used by the network {used[name]}: delete or change that network first")
    return wanted


def _default_route_elsewhere(managed):
    try:
        out = subprocess.run(["ip", "-j", "-4", "route", "show", "default"], capture_output=True, text=True, timeout=10)
        routes = json.loads(out.stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return any(r.get("dev") not in managed for r in routes)


def diff(old_text, new_text):
    return "".join(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            "now",
            "after",
        )
    )


def changed(old, new):
    """The names whose definition differs (added, removed or changed), and the ones of `new` that depend on them
    (a bridge on a changed bond comes down and up with it)."""
    before = {i["name"]: i for i in old}
    after = {i["name"]: i for i in new}
    names = {n for n in before.keys() | after.keys() if before.get(n) != after.get(n)}
    grew = True
    while grew:
        grew = False
        for i in list(before.values()) + list(after.values()):
            deps = set(i.get("ports", []) + i.get("slaves", []) + ([i["raw_device"]] if i["type"] == "vlan" else []))
            if i["name"] not in names and deps & names:
                names.add(i["name"])
                grew = True
    return names


# ---- Applying, confirming, rolling back ----


def _lock():
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(STATE_DIR / "lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _pending_path():
    return STATE_DIR / "pending.json"


def pending():
    """The change waiting for its confirmation, or None: {id, deadline, interfaces (the names it changed)}."""
    try:
        data = json.loads(_pending_path().read_text())
    except (FileNotFoundError, ValueError):
        return None
    return {"id": data["id"], "deadline": data["deadline"], "interfaces": data["names"]}


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.new")
    tmp.write_text(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def _ifcmd(action, names, ordered):
    """ifdown/ifup the names that are in `ordered`, in its order; the failures as {name: message}."""
    failures = {}
    for name in [i["name"] for i in ordered if i["name"] in names]:
        argv = ["ifdown", "--force", name] if action == "down" else ["ifup", name]
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as e:
            failures[name] = str(e)
            continue
        if r.returncode != 0:
            failures[name] = (r.stderr or r.stdout).strip()[-400:] or f"{argv[0]} failed"
    return failures


def _up_order(interfaces):
    order = {"bond": 0, "vlan": 1, "bridge": 2}
    return sorted(interfaces, key=lambda i: (order[i["type"]], i["name"]))


def _down_order(interfaces):
    return list(reversed(_up_order(interfaces)))


def _switch(old, new, names, new_text):
    """Bring `names` down as defined by `old`, write `new_text`, bring them up as defined by `new`."""
    down = _ifcmd("down", names, _down_order(old))
    if new_text:
        _write(managed_path(), new_text)
    else:  # nothing managed any more: no file at all, as before the first change
        managed_path().unlink(missing_ok=True)
    up = _ifcmd("up", names, [i for i in _up_order(new) if i["autostart"]])
    return down, up


def _start_timer(change_id, timeout_s):
    try:
        rollback_timer.start(ROLLBACK_UNIT, "app.core.host_network", change_id, timeout_s)
    except rollback_timer.TimerError as e:
        raise NetworkError(str(e)) from e


def _stop_timer():
    rollback_timer.stop(ROLLBACK_UNIT)


def preview(interfaces):
    """What applying `interfaces` would write: {diff, changes (the names it brings down and up)}. Raises NetworkError."""
    new = check(interfaces, used_by_networks=_used_by_networks())
    old = current()
    old_text = _read_managed()
    return {"diff": diff(old_text, render(new) if new else ""), "changes": sorted(changed(old, new))}


def _read_managed():
    try:
        return managed_path().read_text()
    except FileNotFoundError:
        return ""


def apply(interfaces, timeout_s=TIMEOUT_DEFAULT_S):
    """Apply the new definition, with the rollback timer armed first: {id, deadline, diff, changes}. A failure to
    bring an interface up rolls back at once and raises NetworkError."""
    if not TIMEOUT_MIN_S <= int(timeout_s) <= TIMEOUT_MAX_S:
        raise NetworkError(f"The confirmation delay is between {TIMEOUT_MIN_S} and {TIMEOUT_MAX_S} seconds")
    fd = _lock()
    try:
        if pending():
            raise NetworkError("A change is waiting for its confirmation: confirm it or roll it back first")
        new = check(interfaces, used_by_networks=_used_by_networks())
        old = current()
        old_text = _read_managed()
        new_text = render(new) if new else ""
        names = changed(old, new)
        if not names:
            return {"id": None, "deadline": None, "diff": "", "changes": []}
        change_id = secrets.token_hex(8)
        deadline = time.time() + int(timeout_s)
        (STATE_DIR / f"{change_id}.before").write_text(old_text)
        _write_pending(change_id, deadline, sorted(names), old_text, new_text)
        _start_timer(change_id, timeout_s)
        logger.info("Host network change %s: %s, to confirm within %ss", change_id, ", ".join(sorted(names)), timeout_s)
        _down, up = _switch(old, new, names, new_text)
        if up:
            _rollback_locked(change_id)
            detail = "; ".join(f"{n}: {m}" for n, m in up.items())
            raise NetworkError(f"The change could not be applied, the previous configuration is back ({detail})")
        return {"id": change_id, "deadline": deadline, "diff": diff(old_text, new_text), "changes": sorted(names)}
    finally:
        os.close(fd)


def _write_pending(change_id, deadline, names, old_text, new_text):
    data = {"id": change_id, "deadline": deadline, "names": names, "before": old_text, "after": new_text}
    tmp = STATE_DIR / ".pending.json.new"
    tmp.write_text(json.dumps(data))
    os.chmod(tmp, 0o600)
    os.replace(tmp, _pending_path())


def confirm(change_id):
    """Keep the change: the timer stops and the previous file is forgotten."""
    fd = _lock()
    try:
        waiting = pending()
        if waiting is None or waiting["id"] != change_id:
            raise NetworkError("No change of this id waits for its confirmation (it may have been rolled back)")
        _stop_timer()
        _pending_path().unlink(missing_ok=True)
        (STATE_DIR / f"{change_id}.before").unlink(missing_ok=True)
        logger.info("Host network change %s confirmed", change_id)
        return {"confirmed": change_id}
    finally:
        os.close(fd)


def rollback(change_id=None):
    """Put the previous configuration back (the timer, the administrator, or the daemon after a reboot)."""
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
    _stop_timer()
    new, old = parse(data["after"]), parse(data["before"])
    names = set(data["names"])
    _down, up = _switch(new, old, names, data["before"])
    _pending_path().unlink(missing_ok=True)
    (STATE_DIR / f"{data['id']}.before").unlink(missing_ok=True)
    if up:
        logger.error("Host network change %s rolled back, but: %s", data["id"], up)
    else:
        logger.warning("Host network change %s rolled back", data["id"])
    return {"rolled_back": data["id"], "failures": up}


def finish_interrupted():
    """At hyperlite-daemon's start: a change still pending past its deadline (the node rebooted before the timer
    fired, or the timer did not run) is rolled back."""
    waiting = pending()
    if waiting and waiting["deadline"] < time.time():
        logger.warning("Host network change %s was not confirmed before the restart: rolling it back", waiting["id"])
        rollback(waiting["id"])


def _used_by_networks():
    """{interface: libvirt network} for the managed interfaces a bridged network sits on."""
    try:
        from app.core.libvirt_utils import local_conn

        conn = local_conn()
    except Exception as e:  # no libvirt here (a test, a node being set up): nothing to protect
        logger.info("libvirt not read for the host network check: %s", e)
        return {}
    used = {}
    try:
        for net in conn.listAllNetworks():
            xml = net.XMLDesc(0)
            for found in re.finditer(r"<(?:bridge name|interface dev)='([^']+)'", xml):
                used.setdefault(found.group(1), net.name())
    finally:
        conn.close()
    return used


def state():
    """What the Network page shows: the host's interfaces (with who configures each), the managed ones, the change
    waiting for its confirmation, and the tools this node has."""
    admin = admin_configured()
    mine = current()
    names = {i["name"] for i in mine}
    listed = []
    for i in host_interfaces.list_host_interfaces():
        owner = "hyperlite" if i["name"] in names else ("system" if i["name"] in admin else None)
        listed.append({**i, "configured_by": owner, "file": admin.get(i["name"])})
    return {"interfaces": listed, "managed": mine, "pending": pending(), "tools": tools()}


def main(argv):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if len(argv) == 2 and argv[0] == "rollback":
        result = rollback(argv[1])
        print(json.dumps(result))
        return 0
    print("usage: python -m app.core.host_network rollback <id>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
