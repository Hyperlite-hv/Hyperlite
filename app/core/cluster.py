"""Multi-node management: the equivalent of vCenter driving several ESXi hosts
from a single interface. It lays the foundation for future clustering (NOT
vMotion/DRS, which are out of scope).

Architecture decision (two options were weighed):

  A) Direct remote libvirt connection (qemu+ssh://). CHOSEN.
     + No agent to deploy or maintain on the remote nodes: the only
       prerequisite is an SSH daemon plus libvirt/QEMU-KVM already in place
       (often already the case on a host meant to run VMs).
     + It fully reuses the existing code: every router already calls
       open_conn(), so extending that single function with a node_name
       parameter (see libvirt_utils.py) makes EVERYTHING THAT EXISTS
       multi-node capable, without rewriting vms.py/network.py/storage.py.
     - Sensitive to network latency (each libvirt call is an SSH round trip):
       fine for management and monitoring, potentially annoying for very
       frequent operations (tight metrics polling) over a slow link.
     - The hyperlite process itself must reach the node over SSH for EVERY
       operation (no connection caching in this first version): a network
       outage makes the operation in progress fail cleanly, not crash.

  B) A Hyperlite agent deployed on every remote node (a local API consumed by
     the main node). REJECTED for a project of this size.
     + More robust and decoupled: the agent can cache, retry, and expose an API
       designed for remote use instead of repurposing one designed for local.
     + Possibly better latency (local processing, condensed responses).
     - A second binary to build, version, deploy and update on EVERY node,
       multiplying the update problem by the number of nodes.
     - It duplicates much of the logic already written on the "local" side
       (app/core/*, app/routers/*) instead of reusing it.

  Verdict: (A) is the pragmatic choice for the current size of Hyperlite: no
  new component to deploy and maximum reuse of existing code. Reconsider it if
  SSH latency becomes a real problem in practice, or if the number of nodes
  grows (dozens) to the point where an agent's robustness would pay off.

"""

import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import libvirt

from app.core.audit import log_action
from app.core.database import get_conn
from app.core.vm_builder import PROJDIR

CLUSTER_SSH_KEY_DIR = PROJDIR / "data" / "ssh"
POLL_INTERVAL_S = 60


def _ensure_cluster_keypair():
    """SSH key DEDICATED to the cluster, distinct from the VM automation key
    (hyperlite_automation). A key that opens root access on other physical
    HOSTS must never be the same one as the key installed in guest VMs, which
    are potentially less sensitive."""
    CLUSTER_SSH_KEY_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    priv = CLUSTER_SSH_KEY_DIR / "hyperlite_cluster"
    pub = CLUSTER_SSH_KEY_DIR / "hyperlite_cluster.pub"
    if not priv.exists():
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(priv), "-C", "hyperlite-cluster"],
            check=True,
            capture_output=True,
            text=True,
        )
        priv.chmod(0o600)
    return priv, pub


def get_cluster_pubkey():
    _, pub = _ensure_cluster_keypair()
    return pub.read_text().strip()


def get_cluster_private_key_path():
    priv, _ = _ensure_cluster_keypair()
    return priv


def node_ssh_options(extra=None):
    """ssh/scp options for connecting to a registered cluster node.

    Uses the dedicated cluster key and trust-on-first-use host key checking:
    an unknown host key is recorded on first contact, a CHANGED key is
    refused (possible man-in-the-middle, or the node was reinstalled -- in
    that case remove the node and add it again). Host keys live in the
    service user's default known_hosts file so that libvirt's own ssh
    transport, which shells out to `ssh`, shares the same trust store."""
    return [
        "-i",
        str(get_cluster_private_key_path()),
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "BatchMode=yes",
        *(extra or []),
    ]


def _known_hosts_target(node):
    port = str(node["ssh_port"])
    return node["hostname"] if port == "22" else f"[{node['hostname']}]:{port}"


def ensure_known_host(node):
    """Record the node's SSH host key on first use (idempotent).

    libvirt's ssh transport cannot prompt, so an unknown host would fail with
    an opaque error; learning the key here (trust on first use) keeps
    existing registrations working after the switch from unchecked host keys.
    A key that is already recorded is never overwritten."""
    known = subprocess.run(["ssh-keygen", "-F", _known_hosts_target(node)], capture_output=True, text=True)
    if known.returncode == 0:
        return
    subprocess.run(
        [
            "ssh",
            *node_ssh_options(["-o", "ConnectTimeout=8"]),
            "-p",
            str(node["ssh_port"]),
            f"{node['ssh_user']}@{node['hostname']}",
            "true",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )


def forget_known_host(node):
    """Drop a removed node's recorded host key so it can be re-added after a reinstall."""
    subprocess.run(["ssh-keygen", "-R", _known_hosts_target(node)], capture_output=True, text=True)


def build_libvirt_uri(node):
    """qemu+ssh://<user>@<host>:<port>/system using the dedicated cluster key.

    Host keys are verified through ssh's default known_hosts (see
    node_ssh_options). The key is learned here on first use because libvirt's
    ssh transport cannot prompt; every code path that opens a connection to a
    node goes through this function."""
    ensure_known_host(node)
    key_path = get_cluster_private_key_path()
    return (
        f"qemu+ssh://{node['ssh_user']}@{node['hostname']}:{node['ssh_port']}/system?keyfile={key_path}&sshauth=privkey"
    )


def get_node(name):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM nodes WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def list_nodes():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM nodes ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def test_node_connection(hostname, ssh_user, ssh_port):
    """Try a real remote libvirt connection and check that it is a QEMU/KVM
    host: not just "the SSH port answers"."""
    fake_node = {"hostname": hostname, "ssh_user": ssh_user, "ssh_port": ssh_port}
    uri = build_libvirt_uri(fake_node)
    try:
        conn = libvirt.openReadOnly(uri)
    except libvirt.libvirtError as e:
        return False, str(e)
    if conn is None:
        return False, "Connection refused (unknown reason)"
    try:
        hv_type = conn.getType()
        hostname_reelle = conn.getHostname()
        if hv_type != "QEMU":
            return False, f"Hypervisor '{hv_type}' detected, QEMU/KVM expected"
        return True, hostname_reelle
    finally:
        conn.close()


REVERSE_KEY_DIR = CLUSTER_SSH_KEY_DIR / "reverse"
REVERSE_REMOTE_DIR = "/root/.hyperlite-reverse"
REVERSE_KEY_TAG = (
    "hyperlite-reverse"  # marker at the end of an authorized_keys line, to find and clean up our own entries
)


def _authorized_keys_path():
    return Path("/root/.ssh/authorized_keys")


def _run_ssh(node, args, timeout=15):
    ssh_opts = node_ssh_options()
    target = f"{node['ssh_user']}@{node['hostname']}"
    return subprocess.run(
        ["ssh", *ssh_opts, "-p", str(node["ssh_port"]), target, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def ensure_reverse_trust(node):
    """SSH trust in the REVERSE direction (remote node -> local host), needed so
    that a live migration INITIATED from a remote node can bring a VM back to
    the local host: peer-to-peer migration is always initiated by the SOURCE
    libvirtd, which must be able to connect ITSELF to the destination. Without
    it, only the local host -> remote node trust exists (a SINGLE key shared by
    all nodes, `hyperlite_cluster`).

    The design deliberately DIFFERS from that single key: reusing the SAME
    shared key for the reverse direction would give EVERY compromised node
    direct root access to the local host (which holds the GPG signing key, the
    complete database and every secret), which is unacceptable. So it generates
    a key pair DEDICATED TO THIS NODE, pushed ONLY to this node (never shared),
    and authorized on the local host with a `from=` restriction to THIS node's
    address: a key stolen from one node can only be reused from that same
    node's address, not from anywhere.

    Fully best-effort: called automatically when a node is registered, but a
    failure here must NEVER make the registration itself fail (the local host ->
    node direction, by far the most used, keeps working independently)."""
    REVERSE_KEY_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    priv = REVERSE_KEY_DIR / f"{node['name']}_ed25519"
    pub = REVERSE_KEY_DIR / f"{node['name']}_ed25519.pub"
    if not priv.exists():
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(priv), "-C", f"hyperlite-reverse-{node['name']}"],
            check=True,
            capture_output=True,
            text=True,
        )
        priv.chmod(0o600)
    pub_line = pub.read_text().strip()

    # 1) Push the PRIVATE key to the remote node (through the EXISTING local host ->
    # node trust): that is where the source libvirtd will need it at migration time.
    mkdir_r = _run_ssh(node, ["mkdir", "-p", REVERSE_REMOTE_DIR])
    if mkdir_r.returncode != 0:
        raise RuntimeError(f"Unable to create {REVERSE_REMOTE_DIR} on {node['name']}: {mkdir_r.stderr.strip()}")
    ssh_opts = node_ssh_options()
    scp_r = subprocess.run(
        [
            "scp",
            *ssh_opts,
            "-P",
            str(node["ssh_port"]),
            str(priv),
            f"{node['ssh_user']}@{node['hostname']}:{REVERSE_REMOTE_DIR}/id_ed25519",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if scp_r.returncode != 0:
        raise RuntimeError(f"Failed to copy the key to {node['name']}: {scp_r.stderr.strip()}")
    _run_ssh(node, ["chmod", "600", f"{REVERSE_REMOTE_DIR}/id_ed25519"])

    # 2) Authorize the PUBLIC key on the local host, restricted to this specific
    # node's address. Idempotent (no duplicate if already present, e.g. a second call
    # after a change to the node).
    auth_path = _authorized_keys_path()
    auth_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    existing = auth_path.read_text() if auth_path.exists() else ""
    marker = f"{REVERSE_KEY_TAG}-{node['name']}"
    if marker not in existing:
        line = (
            f'from="{node["hostname"]}",no-port-forwarding,no-X11-forwarding,no-agent-forwarding {pub_line} {marker}\n'
        )
        with open(auth_path, "a") as f:
            f.write(line)
        auth_path.chmod(0o600)


def revoke_reverse_trust(node_name):
    """Best-effort cleanup when a node is removed (see remove_node): remove the
    matching authorized_keys entry on the local host (always possible, it is a
    local file) and the local key pair. It does NOT try to reach the remote
    node to delete the private key there (it may already be unreachable, the
    very reason it is being removed): the key left on the node simply becomes
    useless as soon as the matching authorized_keys entry no longer exists on
    the local host."""
    marker = f"{REVERSE_KEY_TAG}-{node_name}"
    auth_path = _authorized_keys_path()
    if auth_path.exists():
        lines = [line for line in auth_path.read_text().splitlines(keepends=True) if marker not in line]
        auth_path.write_text("".join(lines))
    (REVERSE_KEY_DIR / f"{node_name}_ed25519").unlink(missing_ok=True)
    (REVERSE_KEY_DIR / f"{node_name}_ed25519.pub").unlink(missing_ok=True)


def get_reverse_key_remote_path():
    """Path (ON THE REMOTE NODE, not on the local host) of the private key pushed
    by ensure_reverse_trust(). Used by migrate_vm() to build the destination URI
    when the local host is the target of a migration initiated by a remote
    node."""
    return f"{REVERSE_REMOTE_DIR}/id_ed25519"


def register_node(name, hostname, ssh_user, ssh_port, username):
    ok, message = test_node_connection(hostname, ssh_user, ssh_port)
    if not ok:
        raise RuntimeError(
            f"Connection failed: {message}. Check that the cluster public key is "
            f"installed in ~{ssh_user}/.ssh/authorized_keys on {hostname} (GET /nodes/cluster-pubkey "
            f"to retrieve it) and that libvirtd is running there."
        )
    now = datetime.now(UTC).isoformat()
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO nodes (name, hostname, ssh_user, ssh_port, statut, derniere_verification, added_at) "
                "VALUES (?, ?, ?, ?, 'en_ligne', ?, ?)",
                (name, hostname, ssh_user, ssh_port, now, now),
            )
            conn.commit()
        except Exception:
            raise RuntimeError(f"A node '{name}' already exists") from None
    log_action(username, "register_node", name, "succes", f"{ssh_user}@{hostname}:{ssh_port}")
    node = get_node(name)
    # Best-effort (see the ensure_reverse_trust docstring): migration from a remote
    # node to the local host is a secondary feature and must never make the node
    # registration itself fail.
    try:
        ensure_reverse_trust(node)
    except Exception as e:
        log_action(
            username,
            "register_node",
            name,
            "succes",
            f"Reverse SSH trust not established (node -> local host migration unavailable): {e}",
        )
    return node


def remove_node(name, username):
    node = get_node(name)
    with get_conn() as conn:
        conn.execute("DELETE FROM nodes WHERE name = ?", (name,))
        conn.commit()
    if node:
        forget_known_host(node)
    revoke_reverse_trust(name)
    log_action(username, "remove_node", name, "succes")


def node_summary(node_name):
    """CPU/RAM/storage/VMs of the node, in the same shape as the local
    GET /dashboard (app/routers/dashboard.py), so the frontend can reuse the
    same rendering whichever node is shown."""
    from app.core.libvirt_utils import ensure_default_pool, open_conn

    conn = open_conn(node_name)
    try:
        domains = conn.listAllDomains()
        active = sum(1 for d in domains if d.isActive())
        try:
            pool = ensure_default_pool(conn)
            pool.refresh(0)
            _, capacity, _allocation, available = pool.info()
        except libvirt.libvirtError:
            capacity = available = None
        return {
            "hostname": conn.getHostname(),
            "connecte": conn.isAlive() == 1,
            "vms_actives": active,
            "vms_arretees": len(domains) - active,
            "stockage_capacite_go": round(capacity / (1024**3), 1) if capacity else None,
            "stockage_disponible_go": round(available / (1024**3), 1) if available else None,
        }
    finally:
        conn.close()


def _poll_nodes():
    while True:
        try:
            with get_conn() as conn:
                nodes = conn.execute("SELECT * FROM nodes").fetchall()
            for node in nodes:
                ok, _ = test_node_connection(node["hostname"], node["ssh_user"], node["ssh_port"])
                new_statut = "en_ligne" if ok else "hors_ligne"
                with get_conn() as conn:
                    prev = conn.execute("SELECT statut FROM nodes WHERE id = ?", (node["id"],)).fetchone()
                    conn.execute(
                        "UPDATE nodes SET statut = ?, derniere_verification = ? WHERE id = ?",
                        (new_statut, datetime.now(UTC).isoformat(), node["id"]),
                    )
                    conn.commit()
                if prev and prev["statut"] != new_statut:
                    log_action("system", "node_statut_change", node["name"], "succes" if ok else "echec", new_statut)
                    if new_statut == "hors_ligne":
                        # HA: report the protected VMs of this node as soon as it is detected as down.
                        # Late import, avoids a cycle (ha.py already imports from libvirt_utils.py).
                        from app.core.ha import alert_for_down_node

                        try:
                            alert_for_down_node(node["name"])
                        except Exception as ha_exc:
                            print(f"[cluster] HA alert failed for {node['name']}: {ha_exc!r}", flush=True)
            # HA: resynchronize the cache of protected VMs while their node is reachable.
            # Same cadence as the node poll (POLL_INTERVAL_S), no need for a separate
            # dedicated loop.
            try:
                from app.core.ha import sync_protected_vms

                sync_protected_vms()
            except Exception as ha_exc:
                print(f"[cluster] HA resynchronization failed: {ha_exc!r}", flush=True)
        except Exception as e:
            print(f"[cluster] poll failed: {e!r}", flush=True)
        time.sleep(POLL_INTERVAL_S)


def start_node_poller():
    thread = threading.Thread(target=_poll_nodes, daemon=True)
    thread.start()
    return thread
