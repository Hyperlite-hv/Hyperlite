"""Network/datacenter firewall. Distinct from the PER-VM firewall
(app/routers/vms.py, libvirt's nwfilter subsystem): this one filters at the
BRIDGE level itself (the kernel FORWARD chain), so it applies to ALL the VMs
of a network, present and future, without touching each interface
individually.

Why not nwfilter here: checked against libvirt's RNG schemas on the host
(/usr/share/libvirt/schemas/network.rng and nwfilter.rng), `<filterref>` only
exists in the DOMAIN schema (a VM interface); the `<network>` schema has no
notion of a "default filter applied to every interface of this network". A
firewall that is really at network level must therefore filter the real
traffic crossing point: the Linux bridge attached to the network, through
iptables/FORWARD, exactly where libvirt itself already inserts its own chains
for NAT (LIBVIRT_FWI/FWO/FWX, present for every started NAT/isolated network).

Our chain (HYPERLITENETFW) is inserted at position 1 of FORWARD, so it is
evaluated BEFORE libvirt's chains: an explicit DROP here blocks the traffic
before libvirt has any chance to allow it."""

import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET

import libvirt

from app.core.database import get_conn

UMBRELLA_CHAIN = "HYPERLITENETFW"


def _chain_name(network_name: str) -> str:
    # Real limit of an iptables chain name: 28 characters. A deterministic hash
    # rather than a truncation of the name: two networks whose names share their
    # first 20 characters must never end up on the same chain.
    h = hashlib.sha1(network_name.encode("utf-8"), usedforsecurity=False).hexdigest()[:16].upper()
    return f"HLNET{h}"


def _run(*args):
    return subprocess.run(["iptables", *args], capture_output=True, text=True)


def _chain_exists(chain: str) -> bool:
    return _run("-nL", chain).returncode == 0


def _ensure_umbrella_chain():
    if not _chain_exists(UMBRELLA_CHAIN):
        _run("-N", UMBRELLA_CHAIN)
    # Reassert position 1 of FORWARD on EVERY application: libvirt reinserts its own
    # jump rules each time a network is (re)started, which can technically push ours
    # lower. Checked before inserting (idempotent: never a duplicate, even when
    # called in a loop). Known limitation, documented rather than hidden: a
    # `virsh net-start` run OUTSIDE Hyperlite between two calls here could in theory
    # put a libvirt chain in front of ours. Replaying a rule from the UI (or
    # restarting hyperlite.service, see reapply_all) is enough to reassert position 1.
    check = _run("-C", "FORWARD", "-j", UMBRELLA_CHAIN)
    if check.returncode != 0:
        _run("-I", "FORWARD", "1", "-j", UMBRELLA_CHAIN)


def _bridge_name(conn, network_name: str):
    try:
        net = conn.networkLookupByName(network_name)
    except libvirt.libvirtError:
        return None
    root = ET.fromstring(net.XMLDesc(0))
    bridge = root.find("bridge")
    return bridge.get("name") if bridge is not None else None


def _ensure_network_jump(bridge: str, chain: str):
    for flag in ("-i", "-o"):
        if _run("-C", UMBRELLA_CHAIN, flag, bridge, "-j", chain).returncode != 0:
            _run("-A", UMBRELLA_CHAIN, flag, bridge, "-j", chain)


def _remove_network_jump(bridge: str, chain: str):
    for flag in ("-i", "-o"):
        while _run("-C", UMBRELLA_CHAIN, flag, bridge, "-j", chain).returncode == 0:
            _run("-D", UMBRELLA_CHAIN, flag, bridge, "-j", chain)


def _build_rule_specs(bridge: str, config: dict):
    """Reuses EXACTLY the FirewallConfig/FirewallRule shape of the per-VM firewall
    (app/routers/vms.py, through app/core/firewall_shared.py): same UI, same
    validation, only the TARGET changes (an iptables chain instead of an
    nwfilter). "in"/"out" keep the meaning of the per-VM firewall (relative to
    the VM): "in" = traffic ENTERING the VMs of this network (the bridge is the
    packet's exit on the host side, -o), "out" = traffic LEAVING the VMs (the
    bridge is the packet's entry, -i)."""
    directions_map = {"in": ["-o"], "out": ["-i"], "inout": ["-i", "-o"]}
    # Rules are stateful: without this, each rule is evaluated statelessly, so
    # allowing OUTGOING traffic does not automatically let its REPLY come back (a
    # separate INCOMING flow as far as this filter is concerned). It showed up as an
    # outgoing ping being allowed but its ICMP reply never returning. Like any real
    # firewall (iptables best practice, Proxmox, pfSense...), the return traffic of an
    # already allowed connection must pass without a manual mirror rule for every
    # protocol/port in both directions.
    specs = [
        ["-i", bridge, "-m", "state", "--state", "ESTABLISHED,RELATED", "-j", "ACCEPT"],
        ["-o", bridge, "-m", "state", "--state", "ESTABLISHED,RELATED", "-j", "ACCEPT"],
    ]
    for rule in config["rules"]:
        proto = [] if rule["protocol"] == "all" else ["-p", rule["protocol"]]
        port = ["--dport", str(rule["port"])] if rule.get("port") and rule["protocol"] in ("tcp", "udp") else []
        target = "ACCEPT" if rule["action"] == "accept" else "DROP"
        for flag in directions_map[rule["direction"]]:
            specs.append([flag, bridge, *proto, *port, "-j", target])
    default_target = "ACCEPT" if config["default_policy"] == "accept" else "DROP"
    specs.append(["-i", bridge, "-j", default_target])
    specs.append(["-o", bridge, "-j", default_target])
    return specs


def apply_network_firewall(conn, network_name: str, config: dict):
    """Fully rebuild the network's dedicated chain (flush + rules in order), the same
    principle as nwfilterDefineXML for the per-VM firewall: everything is
    redefined instead of applying an incremental patch, so it never diverges
    from `config`. Returns a summary and raises ValueError/RuntimeError on
    failure (the caller translates them to HTTPException)."""
    bridge = _bridge_name(conn, network_name)
    if not bridge:
        raise ValueError(
            f"Network '{network_name}' not found or has no associated bridge ('bridge' mode towards a host bridge not managed by Hyperlite?)"
        )

    chain = _chain_name(network_name)
    _ensure_umbrella_chain()
    if not _chain_exists(chain):
        _run("-N", chain)
    _run("-F", chain)
    for spec in _build_rule_specs(bridge, config):
        result = _run("-A", chain, *spec)
        if result.returncode != 0:
            raise RuntimeError(f"iptables refused a rule ({' '.join(spec)}): {result.stderr.strip()}")
    _ensure_network_jump(bridge, chain)

    with get_conn() as db:
        db.execute(
            "INSERT INTO network_firewall (network_name, default_policy, rules_json) VALUES (?, ?, ?) "
            "ON CONFLICT(network_name) DO UPDATE SET default_policy = excluded.default_policy, rules_json = excluded.rules_json",
            (network_name, config["default_policy"], json.dumps(config["rules"])),
        )
        db.commit()

    return {"pont": bridge, "regles_appliquees": len(config["rules"])}


def get_network_firewall(network_name: str) -> dict:
    with get_conn() as db:
        row = db.execute(
            "SELECT default_policy, rules_json FROM network_firewall WHERE network_name = ?", (network_name,)
        ).fetchone()
    if not row:
        return {
            "default_policy": "accept",
            "rules": [],
        }  # nothing configured: everything is allowed, the default behaviour
    return {"default_policy": row["default_policy"], "rules": json.loads(row["rules_json"])}


def remove_network_firewall(conn, network_name: str):
    """Called when a network is deleted (app/routers/network.py). Without it, the
    jump from HYPERLITENETFW and the dedicated chain would stay referenced to a
    bridge that no longer exists (harmless in practice, since iptables never
    matches anything for a vanished bridge, but an orphan chain accumulating
    with every recreated network would eventually pollute the table)."""
    bridge = _bridge_name(conn, network_name)
    chain = _chain_name(network_name)
    if bridge:
        _remove_network_jump(bridge, chain)
    if _chain_exists(chain):
        _run("-F", chain)
        _run("-X", chain)
    with get_conn() as db:
        db.execute("DELETE FROM network_firewall WHERE network_name = ?", (network_name,))
        db.commit()


def reapply_all(conn):
    """Called when the service starts (app/main.py::on_startup). iptables rules do
    NOT survive a HOST reboot (unlike the per-VM firewall's nwfilter, which
    libvirt itself stores and reapplies automatically). Without this reapply, a
    network firewall configured before a server reboot would silently
    disappear afterwards with nothing indicating it in the UI (the network still
    looks "active", just without any filtering)."""
    with get_conn() as db:
        rows = db.execute("SELECT network_name, default_policy, rules_json FROM network_firewall").fetchall()
    for row in rows:
        config = {"default_policy": row["default_policy"], "rules": json.loads(row["rules_json"])}
        try:
            apply_network_firewall(conn, row["network_name"], config)
        except Exception as e:
            print(f"[network_firewall] reapply failed for '{row['network_name']}': {e!r}", flush=True)
