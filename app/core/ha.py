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
    unreachable over SSH, the fencing fails and that is noted as such, but it
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

import logging
import subprocess
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import libvirt

from app.core.audit import log_action
from app.core.database import get_conn
from app.core.libvirt_utils import open_conn, uses_shared_storage

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
    `nodes` table."""
    return None if node_label in (None, "local") else node_label


def list_protected():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM ha_protected_vms ORDER BY vm_name").fetchall()
    return [dict(r) for r in rows]


def get_protected(vm_name):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM ha_protected_vms WHERE vm_name = ?", (vm_name,)).fetchone()
    return dict(row) if row else None


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
                "HA protection impossible: at least one disk of this VM is not on a shared storage pool"
                "(NFS). Move its disk to a shared pool first."
            )

        now = _now()
        with get_conn() as db:
            db.execute(
                "INSERT INTO ha_protected_vms (vm_name, node, domain_xml, enabled_by, enabled_at, last_synced_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(vm_name) DO UPDATE SET node=excluded.node, domain_xml=excluded.domain_xml, last_synced_at=excluded.last_synced_at",
                (vm_name, node_label, _portable_xml(domain.XMLDesc(0)), username, now, now),
            )
            db.commit()
        log_action(username, "ha_enable", vm_name, "succes", f"node {node_label}")
    finally:
        conn.close()


def disable_protection(vm_name, username):
    with get_conn() as db:
        db.execute("DELETE FROM ha_protected_vms WHERE vm_name = ?", (vm_name,))
        db.commit()
    log_action(username, "ha_disable", vm_name, "succes")


def sync_protected_vms():
    """Called periodically (see cluster.py::_poll_nodes, the same loop, no
    additional dedicated thread) WHILE each protected node is reachable. It
    refreshes the domain_xml cache (the only way to have a configuration to
    redefine elsewhere the day this node REALLY fails, since its XML cannot be
    requested once it is unreachable) and automatically disables the protection
    if the storage is no longer shared (configuration changed in the meantime).
    A cleanly disabled protection with a clear audit trace is better than a
    protection that would silently lie about its guarantees."""
    for row in list_protected():
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
                log_action("system", "ha_auto_disable", row["vm_name"], "echec", "VM not found on the protected node")
                continue
            if not uses_shared_storage(conn, conn, domain):
                disable_protection(row["vm_name"], "system")
                log_action("system", "ha_auto_disable", row["vm_name"], "echec", "storage no longer shared")
                continue
            with get_conn() as db:
                db.execute(
                    "UPDATE ha_protected_vms SET domain_xml = ?, last_synced_at = ? WHERE vm_name = ?",
                    (_portable_xml(domain.XMLDesc(0)), _now(), row["vm_name"]),
                )
                db.commit()
        finally:
            conn.close()


def alert_for_down_node(node_name):
    """Called by cluster.py::_poll_nodes as soon as a node TRANSITIONS to the
    'hors_ligne' state. It reports each protected VM that was on it, WITHOUT
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
            "echec",
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
    over SSH, or kill failed), but it MUST NEVER block recover(): QEMU's native
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
        return False, f"kill sent (PID {','.join(pids)}) but verification impossible: {err2}"
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
    # but weaker than an active confirmation).
    fenced, fence_detail = _attempt_ssh_fence(row["node"], vm_name)
    log_action(
        username,
        "ha_fence",
        vm_name,
        "succes" if fenced else "echec",
        f"Node '{row['node']}': {fence_detail}",
    )

    conn = open_conn(_conn_key(target_node))
    try:
        try:
            conn.lookupByName(vm_name)
            raise RuntimeError(
                f"A VM '{vm_name}' already exists on '{target_node}': risk of conflict, recovery refused"
            )
        except libvirt.libvirtError:
            logger.debug("Ignored exception in recover()", exc_info=True)

        try:
            new_domain = conn.defineXML(row["domain_xml"])
            new_domain.create()
        except libvirt.libvirtError as e:
            raise RuntimeError(f"Recovery failed: {e}") from e

        with get_conn() as db:
            db.execute(
                "UPDATE ha_protected_vms SET node = ?, domain_xml = ?, last_synced_at = ? WHERE vm_name = ?",
                (target_node, _portable_xml(new_domain.XMLDesc(0)), _now(), vm_name),
            )
            db.commit()
        log_action(username, "ha_recover", vm_name, "succes", f"{row['node']} -> {target_node}")
    finally:
        conn.close()
