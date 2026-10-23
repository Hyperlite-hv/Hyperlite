"""Basic high availability: a very reduced equivalent of vSphere HA / Proxmox HA.

The scope is DELIBERATELY cautious: nothing prevents a node "detected as
offline" from actually being alive and merely unreachable (a network cut, a
reboot in progress...). Without protection, AUTOMATICALLY restarting a protected
VM elsewhere while the original still runs on the SAME shared disk would cause
real data corruption (two QEMU processes writing to the same qcow2 file at the
same time, the worst possible scenario for a tool meant to protect data).
Hyperlite therefore limits itself to:
 1. DETECT that a node carrying a protected VM is down (it reuses the existing
    poller, see cluster.py::_poll_nodes) and report it clearly (a visible alert
    plus an audit entry).
 2. Best-effort FENCING over SSH (`_attempt_ssh_fence()` below): BEFORE any
    recovery, try to confirm or force the stop of the original qemu process by
    connecting directly over SSH to the "offline" node. It does NOT go through
    libvirt, because it is precisely the libvirt connection that failed (see
    cluster.py::test_node_connection), very often because libvirtd crashed while
    the qemu process keeps running independently. This is a "weak" fencing (no
    IPMI/PDU power cut, no remote management card assumed): if the node is ALSO
    unreachable over SSH, the fencing fails and that is noted as such, purpose it
    does **NOT block** the recovery. QEMU's native write lock (Failed to get
    'write' lock...) remains the ultimate safety net, confirmed effective in
    testing.
 3. Let a HUMAN ADMIN, who has context Hyperlite lacks (is the node just
    rebooting? is it really dead?), trigger the recovery in one click. It is
    never automatic, not even after a successful SSH fencing: human control
    comes before any action that changes the cluster state.

Protection REQUIRES all the VM's disks to already be on a shared storage pool
(netfs): otherwise there is no guarantee the disk is even readable from another
node after a failover.

"""

import contextlib
import logging
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import libvirt

from app.core.audit import log_action
from app.core.libvirt_utils import domain_disk_paths, open_conn, uses_shared_storage


def _store():
    from app.repositories import registry

    return registry.ha().sync


logger = logging.getLogger(__name__)


def _now():
    return datetime.now(UTC).isoformat()


def _portable_xml(raw_xml):
    """Normalize the XML of an ACTIVE domain (domain.XMLDesc(0)) before caching it
    for a potential future recovery on ANOTHER node. Two real problems were
    found by testing a real recovery between two hosts running different QEMU
    versions on the same Intel hardware:
    1. The machine type ('machine=pc-i440fx-10.0') is resolved by libvirt to a
       CONCRETE version at start time, which the older emulator of the other
       node does not recognize ('unsupported configuration: ... does not
       support machine type'). It is brought back to the generic alias 'pc' (the
       same principle as the 'machine=pc' used at creation in vm_builder.py),
       which lets EACH host choose the concrete version it supports.
    2. The CPU is already resolved to 'custom'/'exact' with dozens of
       'feature policy=require' entries specific to the CPU of the node that ran
       the VM, and fails if the other node does not have EXACTLY the same
       ('Host CPU does not provide required features'). It is brought back to
       'host-model' (the default behaviour of vm_builder.py), which gives up the
       migratability optimization in favour of maximizing the chances that an
       EMERGENCY recovery succeeds, the only purpose of this cache."""
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError:
        return raw_xml  # unlikely (the XML comes from libvirt itself): do not block the cache on this best-effort normalization

    type_el = root.find("os/type")
    if type_el is not None and type_el.get("machine"):
        type_el.set("machine", "pc")

    cpu_el = root.find("cpu")
    if cpu_el is not None:
        root.remove(cpu_el)
    ET.SubElement(root, "cpu", mode="host-model")

    return ET.tostring(root, encoding="unicode")


def _conn_key(node_label):
    """The frontend convention for the local host ('local') ->
    None (the backend convention, see open_conn()). It is never a row of the
    `nodes` table. In a cluster, this node's own name is this node too."""
    from app.core import self_node

    return None if self_node.is_self(node_label) else node_label


def list_protected():
    return _store().protected()


def get_protected(vm_name):
    return _store().protected_vm(vm_name)


def _on_this_node(node_label):
    from app.core import self_node

    return self_node.is_self(node_label)


def _unlocked_reason(domain):
    """Why QEMU's lock on the VM's disks would not stop a second copy on another node, or None
    (docs/design/ha-automatic.md, 4.7). Read on the node that runs the VM: the mounts are its own."""
    from app.core import disk_locks

    if disk_locks.locking_off(domain.XMLDesc(0)):
        return "its definition turns QEMU's image locks off (locking=off)"
    for path in domain_disk_paths(domain):
        reason = disk_locks.local_only(path)
        if reason:
            return reason
    return None


def enable_protection(vm_name, node, username):
    node_label = node or "local"
    conn = open_conn(_conn_key(node_label))
    try:
        try:
            domain = conn.lookupByName(vm_name)
        except libvirt.libvirtError:
            raise RuntimeError(f"VM '{vm_name}' not found on node '{node_label}'") from None

        # src_conn == dest_conn: reuses uses_shared_storage() (designed to compare TWO
        # nodes during a migration) to answer a simpler question here: "is this disk on
        # A netfs pool, period", without needing a second candidate node.
        if not uses_shared_storage(conn, conn, domain):
            raise RuntimeError(
                "HA protection impossible: at least one disk of this VM is not on a shared storage pool "
                "(NFS). Move its disk to a shared pool first."
            )
        # The mounts are checked where the VM runs; a VM of another node is checked there, by its own sync.
        reason = _unlocked_reason(domain) if _on_this_node(node_label) else None
        if reason:
            raise RuntimeError(
                f"HA protection impossible: {reason}, so nothing would stop a second copy of the VM on another node"
            )

        admission = None
        if domain.isActive():  # a stopped VM counts once it starts (the start checks it then)
            from app.core import ha_admission, ha_recovery

            need = ha_recovery.memory_mib(domain.XMLDesc(0))
            admission = ha_admission.refuse_if_strict((vm_name, node_label, need, False))
        with contextlib.suppress(libvirt.libvirtError):
            domain.setAutostart(0)  # started at boot by Hyperlite only, which checks the HA record first (4.4)
        _store().protect(vm_name, node_label, _portable_xml(domain.XMLDesc(0)), username, _now())
        log_action(username, "ha_enable", vm_name, "success", f"node {node_label}")
    finally:
        conn.close()
    return admission


NODE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$")


def policy(vm_name):
    """{"priority", "preferred_nodes", "auto_restarts"} of a protected VM (app/core/ha_recovery.py)."""
    return _store().policy(vm_name)


def set_policy(vm_name, priority, preferred_nodes):
    bad = [n for n in preferred_nodes if not isinstance(n, str) or not NODE_NAME.match(n)]
    if bad:
        raise RuntimeError(f"Not a node name: {', '.join(map(str, bad))}")
    if priority not in ("high", "medium", "low"):
        raise RuntimeError("The priority is high, medium or low")
    _store().set_policy(vm_name, priority, list(dict.fromkeys(preferred_nodes)))


def disable_protection(vm_name, username):
    _store().unprotect(vm_name)
    log_action(username, "ha_disable", vm_name, "success")


def sync_protected_vms():
    """Called periodically (see cluster.py::_poll_nodes, the same loop, no
    additional dedicated thread) WHILE each protected node is reachable. It
    refreshes the domain_xml cache (the only way to have a configuration to
    redefine elsewhere the day this node REALLY fails, since its XML cannot be
    requested once it is unreachable) and automatically disables the protection
    if the storage is no longer shared (configuration changed in the meantime).
    A cleanly disabled protection with a clear audit trace is better than a
    protection that would silently lie about its guarantees.

    In a cluster, each node refreshes the VMs it hosts, from its own libvirt: the rows are shared, and every node
    rewriting every row each minute made them compete (a conflict a minute, app/repositories/cfs/shadow.py), and let
    any node that did not find a VM turn its protection off for the whole cluster. Outside a cluster, the controller
    refreshes them all, as before."""
    from app.core import cluster_lead

    own_only = cluster_lead.in_cluster()
    for row in list_protected():
        if own_only and row["node"] not in (None, "local"):
            continue
        try:
            conn = open_conn(_conn_key(row["node"]))
        except Exception:
            logger.debug("Ignored exception in sync_protected_vms()", exc_info=True)
            continue  # node unreachable right now: nothing to resynchronize, the existing cache stays the last known valid version
        try:
            try:
                domain = conn.lookupByName(row["vm_name"])
            except libvirt.libvirtError:
                disable_protection(row["vm_name"], "system")
                log_action("system", "ha_auto_disable", row["vm_name"], "failed", "VM not found on the protected node")
                continue
            if not uses_shared_storage(conn, conn, domain):
                disable_protection(row["vm_name"], "system")
                log_action("system", "ha_auto_disable", row["vm_name"], "failed", "storage no longer shared")
                continue
            reason = _unlocked_reason(domain) if _on_this_node(row["node"]) else None
            if reason:  # a share mounted again without locks across nodes, or a definition changed since
                disable_protection(row["vm_name"], "system")
                log_action("system", "ha_auto_disable", row["vm_name"], "failed", reason)
                continue
            _store().sync_definition(row["vm_name"], _portable_xml(domain.XMLDesc(0)), _now())
        finally:
            conn.close()


def follow_migration(vm_name, target_node):
    """Move a protected VM's record to the node it was live-migrated to. Without it, the next sync looks for the VM
    on its old node, does not find it, and disables the protection."""
    return _store().move_protected(vm_name, target_node or "local")


def alert_for_down_node(node_name):
    """Called by cluster.py::_poll_nodes as soon as a node TRANSITIONS to the
    'offline' state. It reports each protected VM that was on it, WITHOUT
    attempting fencing here (the SSH fencing, see _attempt_ssh_fence() below,
    only happens when an admin actually triggers recover(), not at every
    detection cycle, which would be far more frequent and noisy for no gain as
    long as nobody recovers)."""
    for row in list_protected():
        if row["node"] != node_name:
            continue
        log_action(
            "system",
            "ha_alert",
            row["vm_name"],
            "failed",
            f"Node '{node_name}' is offline. Protected VM, manual recovery available (HA tab)",
        )


def _attempt_ssh_fence(node_name, vm_name):
    """Best-effort SSH fencing (see the module docstring). NO libvirt here: the
    node is detected as "offline" by a failure of THAT precise connection
    (cluster.py::test_node_connection), so retrying it would bring nothing; the
    process is queried directly at the kernel level. It finds the qemu PID(s) of
    this VM by its command line: libvirt always starts qemu with `-name
    guest=<name>,...`, a stable pattern whatever the QEMU/libvirt version.

    Returns (fenced: bool, detail: str). fenced=True means "confirmed that no
    qemu process of this VM runs there anymore" (kill succeeded OR already
    absent); fenced=False means fencing was impossible (node unreachable even
    over SSH, or kill failed), purpose it MUST NEVER block recover(): QEMU's native
    write lock remains the ultimate safety net in that case (already confirmed
    effective in testing)."""
    from app.core.cluster import get_node, node_ssh_options

    node = get_node(node_name)
    if not node:
        return False, "node not found in the `nodes` table"

    ssh_opts = node_ssh_options(["-o", "ConnectTimeout=8"])
    ssh_target = f"{node['ssh_user']}@{node['hostname']}"
    # `pgrep -af` used to match ITS OWN invocation (sshd runs the remote command
    # through a `bash -c "pgrep -af 'guest=<name>,' | ..."` whose command line itself
    # contains the searched pattern), giving a false "found" PID on every call,
    # including after a kill that really succeeded (confirmed separately with
    # `virsh domstate`: the VM was really off while the "recheck" still claimed an
    # active PID). Fixed by filtering on the binary name (2nd field, right after the
    # PID): only the real qemu process starts with 'qemu-system', never
    # 'bash'/'pgrep', which match themselves.
    find_cmd = f"pgrep -af 'guest={vm_name},' | awk '$2 ~ /qemu-system/ {{print $1}}'"

    def _find_pids():
        try:
            r = subprocess.run(
                ["ssh", *ssh_opts, "-p", str(node["ssh_port"]), ssh_target, find_cmd],
                capture_output=True,
                text=True,
                timeout=12,
            )
        except (subprocess.SubprocessError, OSError) as e:
            return None, f"SSH unreachable: {e}"
        # pgrep returns 1 (not an error) when nothing matches; only a code >1 indicates a
        # real problem (SSH or remote command).
        if r.returncode not in (0, 1):
            return None, f"SSH unreachable or error: {(r.stderr or '').strip()[:200]}"
        return [p for p in r.stdout.split() if p.isdigit()], None

    pids, err = _find_pids()
    if pids is None:
        return False, err
    if not pids:
        return True, "no qemu process found for this VM (already stopped)"

    subprocess.run(
        ["ssh", *ssh_opts, "-p", str(node["ssh_port"]), ssh_target, "kill -9 " + " ".join(pids)],
        capture_output=True,
        text=True,
        timeout=12,
    )
    remaining, err2 = _find_pids()
    if remaining is None:
        return False, f"kill sent (PID {','.join(pids)}) purpose verification impossible: {err2}"
    if remaining:
        return False, f"process still active after kill (PID {','.join(remaining)})"
    return True, f"qemu process killed successfully (PID {','.join(pids)})"


def recover(vm_name, target_node, username):
    row = get_protected(vm_name)
    if not row:
        raise RuntimeError(f"'{vm_name}' is not an HA-protected VM")
    if row["node"] == target_node:
        raise RuntimeError("The destination node must be different from the current protected node")
    if not row["domain_xml"]:
        raise RuntimeError("No cached configuration for this VM: it was never synchronized successfully")

    # Best-effort fencing BEFORE any action (see the module docstring). It always
    # logs, whether it succeeds or not: the admin must be able to see whether the
    # original process's death was really confirmed or whether the recovery only
    # relies on QEMU's native write lock (a real safety net, confirmed in testing,
    # purpose weaker than an active confirmation).
    # A node with a BMC or AMT is powered off through it first, and the VM is restarted elsewhere only once its power
    # state reads off (docs/design/ha-automatic.md, 4.2): that proof exists, so a recovery without it is refused.
    from app.core import ha_fencing

    method = (ha_fencing.get(row["node"]) or {}).get("method")
    if method in ha_fencing.AGENTS:
        result = ha_fencing.power_off(row["node"])
        fenced, fence_detail = result["fenced"], result["detail"]
    else:
        fenced, fence_detail = _attempt_ssh_fence(row["node"], vm_name)
    log_action(
        username,
        "ha_fence",
        vm_name,
        "success" if fenced else "failed",
        f"Node '{row['node']}': {fence_detail}",
    )
    if method in ha_fencing.AGENTS and not fenced:
        raise RuntimeError(
            f"'{row['node']}' was not confirmed powered off through its {method} ({fence_detail}): the VM is not "
            f"restarted elsewhere while its old copy may still run. Fix the node's fencing, or remove it to recover "
            f"without it"
        )

    try:
        ejected = start_on(row, target_node)
    except libvirt.libvirtError as e:
        raise RuntimeError(f"Recovery failed: {e}") from e
    note = f" (CD-ROM emptied: {', '.join(ejected)})" if ejected else ""
    log_action(username, "ha_recover", vm_name, "success", f"{row['node']} -> {target_node}{note}")


def _uuid_of(domain_xml):
    try:
        return (ET.fromstring(domain_xml).findtext("uuid") or "").strip() or None
    except ET.ParseError:
        return None


def eject_media(domain_xml):
    """(definition, ejected paths): the definition with every CD-ROM empty. A recovered VM restarts where its old
    node's ISO library is not: an installation ISO left in its drive kept it from starting anywhere else (lab). Its
    disks are untouched."""
    root = ET.fromstring(domain_xml)
    ejected = []
    for disk in root.findall(".//devices/disk[@device='cdrom']"):
        source = disk.find("source")
        if source is not None:
            ejected.append(source.get("file") or source.get("dev") or "?")
            disk.remove(source)
    return (ET.tostring(root, encoding="unicode") if ejected else domain_xml), ejected


def start_on(row, target_node, reuse_stopped=False):
    """Define the protected VM on `target_node` from its stored definition, its CD-ROMs empty (eject_media), and start
    it; its record then names that node. A VM of that name already there is refused, unless `reuse_stopped` and it is
    this very VM (same UUID), stopped: what an earlier automatic attempt left when the disk was still locked
    (app/core/ha_recovery.py). Returns the ISO paths ejected. Raises RuntimeError for a conflict,
    libvirt.libvirtError when libvirt refuses."""
    vm_name = row["vm_name"]
    domain_xml, ejected = eject_media(row["domain_xml"])
    conn = open_conn(_conn_key(target_node))
    try:
        try:
            domain = conn.lookupByName(vm_name)
        except libvirt.libvirtError:
            domain = None
        if domain is not None and not (
            reuse_stopped and not domain.isActive() and domain.UUIDString() == _uuid_of(row["domain_xml"])
        ):
            raise RuntimeError(
                f"A VM '{vm_name}' already exists on '{target_node}': risk of conflict, recovery refused"
            )
        _start_networks(conn, domain_xml)
        if domain is None:
            domain = conn.defineXML(domain_xml)
        domain.setAutostart(0)  # started at boot by Hyperlite only, which checks the HA record first
        domain.create()
        _store().sync_definition(vm_name, _portable_xml(domain.XMLDesc(0)), _now(), node=target_node)
    finally:
        conn.close()
    _follow(vm_name, row["node"], target_node)
    return ejected


def runs_on(row, node):
    """True when this very VM (same UUID) runs on `node`: an earlier recovery round started it there and its record
    could not be updated after (app/core/ha_recovery.py), so it must not be started anywhere else."""
    uuid = _uuid_of(row["domain_xml"])
    if not uuid:
        return False
    conn = open_conn(_conn_key(node))
    try:
        try:
            return bool(conn.lookupByUUIDString(uuid).isActive())
        except libvirt.libvirtError:
            return False
    finally:
        conn.close()


def adopt(row, node):
    """The record of a protected VM found running on `node` (runs_on) names that node, as start_on would have."""
    _store().sync_definition(row["vm_name"], row["domain_xml"], _now(), node=node)
    _follow(row["vm_name"], row["node"], node)


def _start_networks(conn, domain_xml):
    """Start, on the target, each network of the VM that exists there but is stopped, as a migration does: a network
    missing there stays a reason the VM does not start, said by libvirt."""
    try:
        names = {i.get("network") for i in ET.fromstring(domain_xml).findall(".//devices/interface/source")}
    except ET.ParseError:
        return
    for name in sorted(n for n in names if n):
        try:
            net = conn.networkLookupByName(name)
            if not net.isActive():
                net.create()
                net.setAutostart(True)
        except libvirt.libvirtError:
            logger.debug("Network %s not started on the target", name, exc_info=True)


def _follow(vm_name, source_node, target_node):
    """The VM's notes, tags and start at boot go with it, as after a migration. Its snapshots' records stayed in the
    lost node's libvirt: they are not carried."""
    from app.core import object_meta, vm_boot

    for what, move in (
        ("its tags and notes", lambda: object_meta.follow_migration(vm_name, source_node, target_node)),
        ("its start at boot", lambda: vm_boot.follow_migration(vm_name, source_node, target_node)),
    ):
        try:
            move()
        except Exception:  # the VM runs: a step after it never undoes that
            logger.exception("HA %s: %s did not follow it to %s", vm_name, what, target_node)


def moved_away(vm_name):
    """True when HA restarted this VM on another member: its record names another node."""
    from app.core import self_node

    row = get_protected(vm_name)
    return bool(row) and not self_node.is_self(row["node"])


def forget_moved():
    """At this node's start, before anything starts at boot (docs/design/ha-automatic.md, 4.4): undefine each stopped
    VM here that HA restarted on another member while this node was down. Its disks are shared: only the definition
    goes. A copy running here is left alone (QEMU's lock kept a second copy from opening the disk): an administrator's
    call. Returns the VMs undefined."""
    from app.core import self_node

    rows = {r["vm_name"]: r for r in list_protected() if not self_node.is_self(r["node"])}
    if not rows:
        return []
    conn = open_conn(None)
    done = []
    try:
        for name, row in rows.items():
            try:
                domain = conn.lookupByName(name)
            except libvirt.libvirtError:
                continue
            if domain.isActive() or domain.UUIDString() != _uuid_of(row["domain_xml"]):
                continue
            domain.undefineFlags(
                libvirt.VIR_DOMAIN_UNDEFINE_KEEP_NVRAM
                | libvirt.VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA
                | libvirt.VIR_DOMAIN_UNDEFINE_MANAGED_SAVE
            )
            log_action(
                "system", "ha_forget_moved", name, "success", f"restarted on {row['node']} while this node was down"
            )
            done.append(name)
    finally:
        conn.close()
    return done


def forget_on(row, target_node):
    """Undefine, on `target_node`, the stopped copy of the protected VM an abandoned attempt left there."""
    conn = open_conn(_conn_key(target_node))
    try:
        domain = conn.lookupByName(row["vm_name"])
        if not domain.isActive() and domain.UUIDString() == _uuid_of(row["domain_xml"]):
            domain.undefine()
    except libvirt.libvirtError:
        logger.debug("Nothing to undefine on %s", target_node, exc_info=True)
    finally:
        conn.close()
