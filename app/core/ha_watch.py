"""HA watcher in dry-run mode (design docs/design/ha-automatic.md, steps 2 and 3).

Every WATCH_INTERVAL_S, each node that carries a protected VM is probed: a read-only libvirt connection and an SSH
`true`. A node is "suspect" after `seuil_suspect` rounds where both fail, and "en_panne" after `seuil_panne`. A node
whose libvirt fails while SSH answers is only "libvirt_injoignable": libvirtd is down, the VMs most likely still run.

When a node fails, the watcher decides what automatic HA *would* do, records it on each protected VM of the node,
writes an audit entry and sends one notification. It never fences, powers off or restarts anything: the maintainer
asked for this dry run before any automatic action. The decision follows the design:
  - the controller first checks it is not the isolated party: it must reach a majority of the voters (itself, the
    registered nodes and the witness when one is set); otherwise it does nothing;
  - on a two-node cluster without a witness, automatic restart is never possible;
  - the failed node needs a fencing method (ha_fencing), else the recovery stays manual;
  - the restart target is the first online node that is not in maintenance and is not the failed one.

Leases: `lease_check()` reports whether libvirt's lockd lock manager is enabled on a node (the second barrier of
the design, which makes QEMU refuse a disk another node holds).
"""

import logging
import socket
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from app.core import ha_fencing, maintenance
from app.core.audit import log_action


def _store():
    from app.repositories import registry

    return registry.ha().sync


logger = logging.getLogger(__name__)

WATCH_INTERVAL_S = 10
DEFAULTS = {"temoin": "", "seuil_suspect": "3", "seuil_panne": "6"}
QEMU_CONF = Path("/etc/libvirt/qemu.conf")
LOCKD_CONF = Path("/etc/libvirt/qemu-lockd.conf")

OK, SUSPECT, FAILED, LIBVIRT_DOWN = "ok", "suspect", "en_panne", "libvirt_injoignable"

# node -> {"echecs": int, "etat": str, "verifie_le": iso}: in memory, rebuilt within a minute after a restart.
_state = {}
_lock = threading.Lock()


def _now():
    return datetime.now(UTC).isoformat()


# --- Settings ---------------------------------------------------------------------------------------------------


def settings():
    merged = {**DEFAULTS, **_store().settings()}
    return {
        "temoin": merged["temoin"],
        "seuil_suspect": int(merged["seuil_suspect"]),
        "seuil_panne": int(merged["seuil_panne"]),
    }


def save_settings(temoin, seuil_suspect, seuil_panne):
    temoin = (temoin or "").strip()
    host, _, port = temoin.rpartition(":") if temoin.count(":") == 1 else (temoin, "", "")
    if temoin and (not ha_fencing._valid_host(host) or (port and not port.isdigit())):
        raise ValueError("The witness must be a host name or an IP address, optionally with :port")
    if not 1 <= seuil_suspect < seuil_panne <= 60:
        raise ValueError("Thresholds: 1 <= suspect < failed <= 60 rounds (one round every 10 s)")
    _store().save_settings({"temoin": temoin, "seuil_suspect": str(seuil_suspect), "seuil_panne": str(seuil_panne)})
    return settings()


# --- Probes -----------------------------------------------------------------------------------------------------


def _tcp(host, port, timeout=3):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def witness_reachable(temoin):
    """A witness is "host" (ICMP ping) or "host:port" (a TCP connection, for a network that drops ICMP)."""
    if not temoin:
        return None
    if temoin.count(":") == 1:
        host, port = temoin.split(":")
        return _tcp(host, port)
    try:
        return subprocess.run(["ping", "-c", "1", "-W", "2", temoin], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def probe(node):
    """(libvirt_ok, ssh_ok) for a registered node."""
    from app.core.cluster import _run_ssh, test_node_connection

    libvirt_ok, _ = test_node_connection(node["hostname"], node["ssh_user"], node["ssh_port"])
    try:
        ssh_ok = _run_ssh(node, ["true"], timeout=8).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ssh_ok = False
    return libvirt_ok, ssh_ok


def isolation(nodes, temoin):
    """{"isole": bool, "joignables", "votants", "temoin_joignable"}: can the controller see a majority of the voters
    (itself, every registered node, and the witness)? Reachability is the SSH port, cheap and independent of
    libvirt."""
    reachable = 1 + sum(1 for n in nodes if _tcp(n["hostname"], n["ssh_port"]))
    witness = witness_reachable(temoin)
    voters = 1 + len(nodes) + (1 if temoin else 0)
    reachable += 1 if witness else 0
    return {"isole": reachable * 2 <= voters, "joignables": reachable, "votants": voters, "temoin_joignable": witness}


def auto_restart_possible(nodes, temoin):
    """(bool, reason): the structural conditions of the design, whatever the current failures."""
    if not nodes:
        return False, "Only one node: there is nowhere to restart a VM"
    if len(nodes) == 1 and not temoin:
        return (
            False,
            "Two nodes and no witness: automatic restart stays off (a network cut would look like a failure on both sides)",
        )
    return True, None


# --- Decisions ----------------------------------------------------------------------------------------------------


def _restart_target(failed_node, all_nodes):
    candidates = ["local"] + [n["name"] for n in all_nodes if n["statut"] == "en_ligne"]
    for name in candidates:
        if name != failed_node and maintenance.get(name) is None:
            return name
    return None


def decide(failed_node, all_nodes, temoin, isolated):
    """What automatic HA would do for the VMs of a failed node: (would_act: bool, sentence). The structural
    conditions come first: on two nodes without a witness, the controller can never tell a dead peer from its own
    isolation, which is exactly why the design requires a witness there."""
    possible, reason = auto_restart_possible(all_nodes, temoin)
    if not possible:
        return False, f"Manual recovery: {reason}"
    if isolated["isole"]:
        return False, (
            f"No action: this controller reaches only {isolated['joignables']} of {isolated['votants']} voters, "
            "it may be the isolated one"
        )
    fencing = ha_fencing.get(failed_node)
    if fencing is None:
        return False, f"Manual recovery: no fencing is set for {failed_node}"
    target = _restart_target(failed_node, all_nodes)
    if target is None:
        return False, "Manual recovery: no online node outside maintenance to restart on"
    how = (
        "wait for its storage leases to expire"
        if fencing["methode"] == "lease_only"
        else f"power {failed_node} off through {fencing['methode'].upper()}"
    )
    return True, f"Would {how}, confirm it is off, then restart the VM on {target} (dry run: nothing done)"


def _record(vm_name, etat, action):
    _store().record_watch(vm_name, etat, action, _now())


def tick():
    """One round of the watcher. Returns the node states, for the API and the tests."""
    from app.core import ha
    from app.core.cluster import list_nodes

    protected = ha.list_protected()
    all_nodes = list_nodes()
    by_name = {n["name"]: n for n in all_nodes}
    watched = sorted({r["node"] for r in protected if r["node"] != "local" and r["node"] in by_name})
    conf = settings()
    with _lock:
        for name in list(_state):
            if name not in watched:
                del _state[name]
    iso = None
    for name in watched:
        libvirt_ok, ssh_ok = probe(by_name[name])
        with _lock:
            st = _state.setdefault(name, {"echecs": 0, "etat": OK, "verifie_le": None})
            previous = st["etat"]
            st["echecs"] = 0 if (libvirt_ok or ssh_ok) else st["echecs"] + 1
            if libvirt_ok:
                st["etat"] = OK
            elif ssh_ok:
                st["etat"] = LIBVIRT_DOWN
            elif st["echecs"] >= conf["seuil_panne"]:
                st["etat"] = FAILED
            elif st["echecs"] >= conf["seuil_suspect"]:
                st["etat"] = SUSPECT
            st["verifie_le"] = _now()
            current = st["etat"]
        if current == previous:
            continue
        vms = [r["vm_name"] for r in protected if r["node"] == name]
        if current == FAILED:
            iso = iso or isolation(all_nodes, conf["temoin"])
            would_act, sentence = decide(name, all_nodes, conf["temoin"], iso)
            for vm in vms:
                _record(vm, FAILED, sentence)
                log_action("system", "ha_dry_run", vm, "succes" if would_act else "echec", f"{name} failed. {sentence}")
            from app.core.notifications import notify

            notify(
                "ha_alert", f"HA: node {name} failed", f"Protected VMs: {', '.join(vms)}. {sentence}", result="echec"
            )
        else:
            note = {
                OK: "Node reachable again" if previous != OK else "",
                SUSPECT: f"Node {name} is not answering (libvirt and SSH)",
                LIBVIRT_DOWN: f"libvirt does not answer on {name} but SSH does: the VMs most likely still run",
            }[current]
            for vm in vms:
                _record(vm, current, note)
            if current != OK:
                log_action("system", "ha_watch", name, "echec", note)
    return status()


def status():
    from app.core.cluster import list_nodes

    conf = settings()
    nodes = list_nodes()
    possible, reason = auto_restart_possible(nodes, conf["temoin"])
    with _lock:
        states = {k: dict(v) for k, v in _state.items()}
    return {
        "mode": "essai",
        "reglages": conf,
        "redemarrage_auto_possible": possible,
        "raison": reason,
        "noeuds": [{"node": k, **v} for k, v in sorted(states.items())],
    }


def _loop():
    while True:
        try:
            tick()
        except Exception:
            logger.exception("HA watcher round failed")
        time.sleep(WATCH_INTERVAL_S)


def start_ha_watch():
    thread = threading.Thread(target=_loop, daemon=True, name="ha-watch")
    thread.start()
    return thread


# --- Leases -------------------------------------------------------------------------------------------------------


def _lockd_state(qemu_conf, lockd_conf):
    enabled = any(
        line.split("#", 1)[0].replace(" ", "") in ('lock_manager="lockd"', "lock_manager='lockd'")
        for line in (qemu_conf or "").splitlines()
    )
    lockspace = None
    for line in (lockd_conf or "").splitlines():
        body = line.split("#", 1)[0].strip()
        if body.replace(" ", "").startswith("file_lockspace_dir="):
            lockspace = body.split("=", 1)[1].strip().strip("\"'")
    if not enabled:
        return {"actif": False, "detail": 'lock_manager = "lockd" is not set in /etc/libvirt/qemu.conf'}
    if not lockspace:
        return {
            "actif": False,
            "detail": "lockd is on, but file_lockspace_dir is not set in /etc/libvirt/qemu-lockd.conf",
        }
    return {"actif": True, "detail": f"lockd with its lockspace in {lockspace} (it must be on the shared NFS storage)"}


def lease_check(node_name):
    """Whether this node runs libvirt's lockd lock manager with a lockspace directory."""
    if node_name == "local":
        read = lambda p: p.read_text() if p.exists() else ""  # noqa: E731
        return _lockd_state(read(QEMU_CONF), read(LOCKD_CONF))
    from app.core.cluster import _run_ssh, get_node

    node = get_node(node_name)
    if node is None:
        return {"actif": False, "detail": "Unknown node"}
    try:
        qemu = _run_ssh(node, ["cat", str(QEMU_CONF)], timeout=10)
        lockd = _run_ssh(node, ["cat", str(LOCKD_CONF)], timeout=10)
    except (OSError, subprocess.SubprocessError):
        logger.warning("Cannot read the libvirt configuration of %s", node_name, exc_info=True)
        return {"actif": False, "detail": "Cannot read the node's libvirt configuration over SSH (see the service log)"}
    return _lockd_state(qemu.stdout, lockd.stdout)
