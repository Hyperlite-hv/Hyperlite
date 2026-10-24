"""A live migration of a running VM from one node to another (app/routers/vms/migration.py), by hyperlite-daemon
(vm.migrate, app/daemon/ops/vm.py; docs/design/privilege-separation.md, section 13, the cluster domain, part 3): both
nodes' libvirt opened with the cluster's key, the destination prepared (its networks, its default pool, the VM's
addresses, the disks pre-created and the CD-ROM files copied), `migrateToURI3`, then what the move leaves behind on
the source (its addresses, its copied disks). The API keeps the checks it answers at once, the lock, the task, and
what follows the VM in its records (HA protection, tags and notes, start at boot), written once this returns.

Its progress is written into the task here, and a cancellation (vm.migrate_cancel) aborts libvirt's job: the VM keeps
running on its source node."""

import logging
import os
import subprocess
import tempfile
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

import libvirt

from app.core import checkpoints, network_alloc, snapshot_carry
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import (
    domain_disk_paths,
    ensure_default_pool,
    node_conn,
    refresh_pools_for_paths,
    uses_shared_storage,
)
from app.core.tasks import task_log, update_task_progress

logger = logging.getLogger(__name__)

DEFAULT_POOL_DIR = "/var/lib/libvirt/images"  # the target of the 'default' pool (ensure_default_pool)


class MigrateError(Exception):
    """A refusal or a failure, with the daemon's kind and the sentence the task shows."""

    def __init__(self, kind, detail):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def _remote(node):
    return None if node in (None, "", "local") else node


def _ssh(node_name, *args, timeout=15):
    from app.core.cluster import get_node, node_ssh_options

    node = get_node(node_name)
    if not node:
        raise RuntimeError(f"Node '{node_name}' not found")
    return node, subprocess.run(
        ["ssh", *node_ssh_options(), "-p", str(node["ssh_port"]), f"{node['ssh_user']}@{node['hostname']}", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def delete_paths_on_node(paths, node_name):
    """Best-effort: delete files, here when node_name is None/"local", over SSH (cluster key) otherwise. A
    VIR_MIGRATE_NON_SHARED_DISK migration copies the disk to the destination but never deletes the source file: every
    migration once left an orphan qcow2 and cloud-init ISO on the origin node."""
    if not paths:
        return
    if not _remote(node_name):
        for p in paths:
            Path(p).unlink(missing_ok=True)
        return
    import shlex

    for p in paths:
        try:
            _ssh(node_name, "rm", "-f", "--", shlex.quote(str(p)))
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            logger.warning("%s left on %s after the migration: %s", p, node_name, e)


def copy_file_to_node(local_path, node_name, remote_path):
    """scp of a file here to the same absolute path on another node, with the cluster's key.
    VIR_MIGRATE_NON_SHARED_DISK only copies device='disk' disks, never the CD-ROMs: the cloud-init ISO was missing on
    the destination and the migration was refused ('unable to access the storage file')."""
    import shlex

    from app.core.cluster import node_ssh_options

    node, mkdir = _ssh(node_name, "mkdir", "-p", "--", shlex.quote(str(Path(remote_path).parent)))
    if mkdir.returncode != 0:
        raise RuntimeError(
            f"Preparing the remote directory failed ({Path(remote_path).parent} on {node_name}): "
            f"{mkdir.stderr.strip()[:300]}"
        )
    target = f"{node['ssh_user']}@{node['hostname']}"
    scp = subprocess.run(
        ["scp", *node_ssh_options(), "-P", str(node["ssh_port"]), str(local_path), f"{target}:{remote_path}"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if scp.returncode != 0:
        raise RuntimeError(f"Copy of '{Path(local_path).name}' to {node_name} failed: {scp.stderr.strip()[:300]}")


def copy_file_from_node(node_name, remote_path, local_path):
    """The mirror of copy_file_to_node, for a migration from another node to this one."""
    from app.core.cluster import get_node, node_ssh_options

    node = get_node(node_name)
    if not node:
        raise RuntimeError(f"Node '{node_name}' not found")
    Path(local_path).parent.mkdir(parents=True, exist_ok=True)
    target = f"{node['ssh_user']}@{node['hostname']}"
    scp = subprocess.run(
        ["scp", *node_ssh_options(), "-P", str(node["ssh_port"]), f"{target}:{remote_path}", str(local_path)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if scp.returncode != 0:
        raise RuntimeError(f"Copy of '{Path(remote_path).name}' from {node_name} failed: {scp.stderr.strip()[:300]}")


def _relay_file(source_node, target_node, path):
    """A file of one other node copied to the same path on another, through a private temporary file here."""
    fd, tmp = tempfile.mkstemp(prefix="hyperlite-relay-")
    os.close(fd)
    try:
        copy_file_from_node(source_node, path, tmp)
        copy_file_to_node(tmp, target_node, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _cdrom_source_paths(domain):
    root = ET.fromstring(domain.XMLDesc(0))
    return [
        s.get("file")
        for s in root.findall(".//devices/disk[@device='cdrom']/source")
        if s is not None and s.get("file")
    ]


def _network_names(domain):
    root = ET.fromstring(domain.XMLDesc(0))
    return [s.get("network") for s in root.findall(".//devices/interface/source") if s.get("network")]


def _ensure_networks_active(conn, names):
    """Start (and autostart) on the destination every network of the VM that exists but is stopped: a node installed
    by hand had 'default' defined and stopped, and libvirt refused the migration with "network default is not
    active". A network missing there stays an error libvirt reports as it is."""
    for name in names:
        try:
            net = conn.networkLookupByName(name)
        except libvirt.libvirtError:
            continue
        if not net.isActive():
            net.create()
            net.setAutostart(True)


def _ensure_default_pool_for(conn, disk_paths):
    """libvirt creates the copied disk on the destination through the pool of its directory, which a fresh node
    defines only when something first asks for it: "no storage pool with matching target path"."""
    if any(os.path.dirname(path) == DEFAULT_POOL_DIR for path in disk_paths):
        ensure_default_pool(conn)


def local_migrate_uri_host(source_node=None):
    """Address through which THIS node is reachable by another node for the QEMU data stream of a migration, when
    this node is the destination: its address in the cluster's configuration, else the address this host uses to
    reach the source node (its route), else Tailscale. None when nothing is found."""
    import ipaddress
    import socket

    from app.core import cluster_setup, self_node

    try:
        conf = cluster_setup.shared_conf()
        me = self_node.name()
        for member in (conf or {}).get("members", []):
            if member.get("name") == me and member.get("address"):
                return member["address"]
    except Exception:
        logger.debug("No cluster address for this node", exc_info=True)
    if _remote(source_node):
        try:
            from app.core.cluster import get_node

            node = get_node(source_node)
            if node:
                peer = socket.getaddrinfo(node["hostname"], None, socket.AF_INET)[0][4][0]
                r = subprocess.run(["ip", "-4", "route", "get", peer], capture_output=True, text=True, timeout=5)
                words = r.stdout.split()
                if r.returncode == 0 and "src" in words:
                    src = words[words.index("src") + 1]
                    if not ipaddress.IPv4Address(src).is_loopback:
                        return src
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            logger.debug("No route to the source node", exc_info=True)
    try:
        r = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError):
        logger.debug("No Tailscale address", exc_info=True)
    return None


def _destination(source_node, dest_node):
    """(destination URI for the source libvirtd, migration parameters)."""
    from app.core.cluster import build_libvirt_uri, get_node

    if dest_node is not None:
        node = get_node(dest_node)
        if node is None:
            raise MigrateError("not_found", f"Node '{dest_node}' not found")
        return build_libvirt_uri(node), {"migrate_uri": f"tcp://{node['hostname']}"}
    local_addr = local_migrate_uri_host(source_node)
    params = {"migrate_uri": f"tcp://{local_addr}"} if local_addr else {}
    if not _remote(source_node):
        return "qemu:///system", params
    # From another node to this one: the URI runs in the SOURCE libvirtd, where "qemu:///system" would be itself
    # ("Attempt to migrate guest to the same host"). In a cluster every member trusts the others' cluster key; the
    # dedicated reverse key is the older single-controller setup's (app/core/cluster.py::ensure_reverse_trust).
    from app.core import cluster_lead
    from app.core.cluster import get_cluster_private_key_path, get_reverse_key_remote_path

    if not local_addr:
        raise MigrateError(
            "failed",
            "No address of this node reachable from the source node (cluster configuration, route, Tailscale): "
            "migration from a remote node to this node is impossible without it",
        )
    keyfile = get_cluster_private_key_path() if cluster_lead.mode() == "cluster" else get_reverse_key_remote_path()
    return f"qemu+ssh://root@{local_addr}/system?keyfile={keyfile}&no_verify=1&sshauth=privkey", params


def _progress(stop, task_id, node, vm_name):
    """The copy's progress written into the task, from libvirt's job, through a connection of its own (a Domain
    object is never shared between threads). jobInfo fails between two jobs: ignored."""
    try:
        with node_conn(node) as conn:
            domain = conn.lookupByName(vm_name)
            while not stop.is_set():
                try:
                    info = domain.jobInfo()
                    if info[3] > 0:
                        update_task_progress(task_id, min(99, int(info[4] * 100 / info[3])))
                except libvirt.libvirtError:
                    logger.debug("No migration job yet for %s", vm_name)
                stop.wait(2)
    except Exception:
        logger.debug("Progress of the migration of %s not followed", vm_name, exc_info=True)


def _pre_create_disks(domain):
    """From another node to this one on non-shared storage, some libvirt versions need the destination file to exist
    already ("Cannot access storage file"): blank qcow2 files of the right size, read with blockInfo."""
    root = ET.fromstring(domain.XMLDesc(0))
    for disk in root.findall(".//devices/disk[@device='disk']"):
        source, target = disk.find("source"), disk.find("target")
        path = source.get("file") if source is not None else None
        dev = target.get("dev") if target is not None else None
        if not path or not dev or Path(path).exists():
            continue
        try:
            subprocess.run(
                ["qemu-img", "create", "-f", "qcow2", path, str(domain.blockInfo(dev)[0])],
                check=True,
                capture_output=True,
                text=True,
            )
        except (libvirt.libvirtError, subprocess.CalledProcessError):
            logger.warning("Disk %s not pre-created: migrateToURI3 will say why", path, exc_info=True)


def migrate(task_id, vm_name, source_node, target_node, aborts):
    """Migrate the running VM. `aborts`: what vm.migrate_cancel calls to stop it ({"abort": callable, "cancelled":
    Event}). Returns ({"shared", "warnings", "checkpoints_dropped"}, finish): `finish()` cleans the source up and
    returns its warnings (vm.migrate_finish). The steps after the move never fail it, each failure is a warning."""
    source, dest = _remote(source_node), _remote(target_node)
    with node_conn(source) as src_conn, node_conn(dest) as dest_conn:
        try:
            domain = src_conn.lookupByName(vm_name)
        except libvirt.libvirtError:
            raise MigrateError("not_found", f"VM '{vm_name}' not found") from None
        if not domain.isActive():
            raise MigrateError("in_use", "The VM must be running for a live migration")

        def abort():
            try:
                domain.abortJob()
            except libvirt.libvirtError as e:
                logger.info("No libvirt job to abort on %s: %s", vm_name, e)  # still preparing: stops at its next step

        aborts["abort"] = abort
        _ensure_networks_active(dest_conn, _network_names(domain))
        _ensure_default_pool_for(dest_conn, domain_disk_paths(domain))
        # The VM keeps its addresses on the per-node NAT networks: reserved on the destination first, refused when
        # another machine holds one there.
        try:
            moved, added = network_alloc.carry_reservations(src_conn, dest_conn, domain.XMLDesc(0))
        except network_alloc.AddressInUse as e:
            raise MigrateError(
                "refused", f"Migration refused: {e}. Free that address on {target_node}, or give the VM another one."
            ) from None
        source_disks = domain_disk_paths(domain)  # the source domain is undefined once it moved
        if source and dest is None:
            _pre_create_disks(domain)

        copied_cdroms = []
        if not source:
            for path in _cdrom_source_paths(domain):
                if Path(path).exists():
                    copy_file_to_node(path, target_node, path)
                    copied_cdroms.append(path)
        elif dest is not None:
            # Between two other nodes (a drain or a rolling update driven from here): through this host, which has
            # the SSH trust of both.
            for path in _cdrom_source_paths(domain):
                _relay_file(source, dest, path)
                copied_cdroms.append(path)
        else:
            for path in _cdrom_source_paths(domain):
                try:
                    copy_file_from_node(source, path, path)
                    copied_cdroms.append(path)
                except RuntimeError:
                    logger.warning("CD-ROM %s not copied from %s: migrateToURI3 will say why", path, source)

        shared = uses_shared_storage(src_conn, dest_conn, domain)
        flags = (
            libvirt.VIR_MIGRATE_LIVE
            | libvirt.VIR_MIGRATE_PEER2PEER
            | libvirt.VIR_MIGRATE_PERSIST_DEST
            | libvirt.VIR_MIGRATE_UNDEFINE_SOURCE
        )
        if not shared:
            flags |= libvirt.VIR_MIGRATE_NON_SHARED_DISK
        # No VIR_MIGRATE_TUNNELLED: with PEER2PEER and a qemu+ssh URI carrying parameters, libvirt 9 fails with "the
        # host argument key must not have a Null value". The QEMU stream goes directly between the hosts, to the
        # address Hyperlite reached the destination at (migrate_uri), not the name the destination gives itself.
        dest_uri, params = _destination(source, dest)

        if aborts["cancelled"].is_set():
            raise MigrateError("failed", "Cancelled before the migration started")
        task_log(task_id, f"Migrating {vm_name} to {target_node} ({'shared' if shared else 'copied'} storage)")
        stop = threading.Event()
        threading.Thread(target=_progress, args=(stop, task_id, source, vm_name), daemon=True).start()
        # Replication is per node: the VM leaves this one, and its checkpoint must not block the disk copy.
        dropped = checkpoints.drop(domain)
        # libvirt refuses a VM with snapshot records; on shared storage they go with it (app/core/snapshot_carry.py).
        stashed = snapshot_carry.stash(domain) if shared else None
        if stashed:
            task_log(task_id, f"Carrying {len(stashed['snapshots'])} snapshot(s) to {target_node}")
        try:
            domain.migrateToURI3(dest_uri, params, flags)
        except libvirt.libvirtError as e:
            network_alloc.release_reservations(dest_conn, added)
            lost = snapshot_carry.restore(domain, stashed)
            if lost:
                logger.error("Snapshots of %s not defined again after a failed migration: %s", vm_name, lost)
            raise MigrateError("failed", describe_exception(e)) from e
        finally:
            stop.set()

        # From here the VM runs on target_node: nothing that follows may fail the migration.
        warnings = []
        if stashed:
            carry = lambda: _carry_snapshots(task_id, dest_conn.lookupByName(vm_name), stashed)  # noqa: E731
            _step(warnings, task_id, vm_name, target_node, "defining its snapshots again", carry)

    def finish():
        """What the move leaves on the source, removed once the API has written where the VM is (HA first): its
        addresses there, and its copied disks and CD-ROM files (never on shared storage, the same file on both)."""
        done = []
        task_log(task_id, "Migration done, cleaning up")
        with node_conn(source) as src_conn, node_conn(dest) as dest_conn:
            release = lambda: network_alloc.release_reservations(src_conn, moved)  # noqa: E731
            _step(done, task_id, vm_name, target_node, "releasing its addresses on the source node", release)
            if not shared:

                def clean_source():
                    delete_paths_on_node(source_disks, source)
                    delete_paths_on_node(copied_cdroms, source)
                    refresh_pools_for_paths(src_conn, source_disks)
                    refresh_pools_for_paths(dest_conn, source_disks)

                _step(done, task_id, vm_name, target_node, "deleting its old disks on the source node", clean_source)
        return done

    return {"shared": shared, "warnings": warnings, "checkpoints_dropped": dropped}, finish


def _carry_snapshots(task_id, domain, stashed):
    lost = snapshot_carry.restore(domain, stashed)
    if lost:
        task_log(task_id, f"Snapshots not carried (their data stays in the disks): {', '.join(lost)}")


def _step(warnings, task_id, vm_name, target_node, what, step):
    """One step after the move: its failure is a warning on the task, never the end of the steps after it."""
    try:
        step()
    except Exception as e:
        logger.exception("After migrating %s to %s: %s failed", vm_name, target_node, what)
        warnings.append(f"{what}: {describe_exception(e)}")
        task_log(task_id, f"Warning: {what} failed: {describe_exception(e)}")
