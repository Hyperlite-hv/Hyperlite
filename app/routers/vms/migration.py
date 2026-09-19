import logging
import subprocess
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

import libvirt
from fastapi import Depends, HTTPException
from pydantic import BaseModel

from app.core import cluster_compat
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import (
    domain_disk_paths,
    open_conn,
    uses_shared_storage,
)
from app.core.security import require_role
from app.core.tasks import create_task, finish_task, update_task_progress
from app.routers.vms._shared import router

logger = logging.getLogger(__name__)


class MigrateRequest(BaseModel):
    target_node: str
    # Compatibility checks: a heuristic can be wrong, so the admin has the last word.
    # This only disables the REFUSAL, and the checks are still logged.
    ignorer_verifications: bool = False


def _norm_node(n):
    """None/'local' = the local host."""
    return None if n in (None, "", "local") else n


# _pool_target_path/_domain_disk_paths/_uses_shared_storage moved to
# app/core/libvirt_utils.py: shared with the shared-storage detection needed for
# HA protection. See pool_type_and_target_path/domain_disk_paths/uses_shared_storage.


def _migration_progress_job(stop_event, task_id, node, vm_name):
    """Runs in its OWN thread (a third one, with its own libvirt connection: never share
    a Domain object between threads) while the main thread is blocked in
    domain.migrate(). jobInfo() can fail transiently (no job started yet, job
    finished between two calls), which is never fatal here: just ignored,
    best-effort."""
    conn = None
    try:
        conn = open_conn(node)
        domain = conn.lookupByName(vm_name)
        while not stop_event.is_set():
            try:
                info = domain.jobInfo()
                # (type, timeElapsed, timeRemaining, dataTotal, dataProcessed, dataRemaining, ...)
                data_total, data_processed = info[3], info[4]
                if data_total > 0:
                    pct = min(99, int(data_processed * 100 / data_total))
                    update_task_progress(task_id, pct)
            except libvirt.libvirtError:
                logger.debug("Ignored exception in _migration_progress_job()", exc_info=True)
            stop_event.wait(2)
    except libvirt.libvirtError:
        logger.debug("Ignored exception in _migration_progress_job()", exc_info=True)
    finally:
        if conn:
            conn.close()


def _cdrom_source_paths(domain):
    root = ET.fromstring(domain.XMLDesc(0))
    paths = []
    for disk_el in root.findall(".//devices/disk"):
        if disk_el.get("device") != "cdrom":
            continue
        source_el = disk_el.find("source")
        path = source_el.get("file") if source_el is not None else None
        if path:
            paths.append(path)
    return paths


def _delete_paths_on_node(paths, node_name):
    """Best-effort: delete a list of files, LOCALLY if node_name is None/"local", over
    SSH (cluster key) otherwise: the same logic as _perform_vm_deletion, reused
    here to clean up the SOURCE disk after a successful migration on NON-shared
    storage. A real bug found when testing multi-node VM actions: a
    VIR_MIGRATE_NON_SHARED_DISK migration copies the disk to the destination but
    NEVER deletes the source file, so every migration silently left an orphan
    qcow2 + cloud-init ISO on the origin node."""
    if not paths:
        return
    if not node_name or node_name == "local":
        for p in paths:
            Path(p).unlink(missing_ok=True)
        return
    from app.core.cluster import get_node, node_ssh_options

    remote_node = get_node(node_name)
    if not remote_node:
        return
    ssh_opts = node_ssh_options()
    ssh_target = f"{remote_node['ssh_user']}@{remote_node['hostname']}"
    for p in paths:
        subprocess.run(
            ["ssh", *ssh_opts, "-p", str(remote_node["ssh_port"]), ssh_target, "rm", "-f", str(p)],
            capture_output=True,
            text=True,
            timeout=15,
        )


def _copy_file_to_node(local_path, node_name, remote_path):
    """Best-effort scp of a LOCAL file to the same absolute path on a remote node,
    with the SSH key dedicated to the cluster (the same key as the qemu+ssh://
    connections). A real bug found when testing a real migration:
    VIR_MIGRATE_NON_SHARED_DISK only copies device='disk' disks, never the
    device='cdrom' CD-ROMs/ISOs, so the cloud-init ISO (created for every VM,
    see vm_builder.py::create_cloudinit_iso) was systematically missing on the
    destination and the migration was refused ('unable to access the storage
    file'). Only supported from the LOCAL node (Hyperlite always drives from the
    local host, see cluster.py): migrating a VM between two remote nodes fails
    here with a clear error rather than being handled silently."""
    from app.core.cluster import get_node, node_ssh_options

    node = get_node(node_name)
    if not node:
        raise RuntimeError(f"Node '{node_name}' not found")
    remote_dir = str(Path(remote_path).parent)
    ssh_target = f"{node['ssh_user']}@{node['hostname']}"
    ssh_opts = node_ssh_options()
    mkdir_r = subprocess.run(
        ["ssh", *ssh_opts, "-p", str(node["ssh_port"]), ssh_target, "mkdir", "-p", remote_dir],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if mkdir_r.returncode != 0:
        raise RuntimeError(
            f"Preparing the remote directory failed ({remote_dir} on {node_name}): {mkdir_r.stderr.strip()[:300]}"
        )
    scp_r = subprocess.run(
        ["scp", *ssh_opts, "-P", str(node["ssh_port"]), local_path, f"{ssh_target}:{remote_path}"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if scp_r.returncode != 0:
        raise RuntimeError(f"Copy of '{Path(local_path).name}' to {node_name} failed: {scp_r.stderr.strip()[:300]}")


def _copy_file_from_node(node_name, remote_path, local_path):
    """Mirror of _copy_file_to_node: scp of a file from a remote node to the LOCAL
    host, with the same SSH key dedicated to the cluster. Needed for migration
    from a remote node to the local host (reverse SSH trust): the SAME limitation
    as documented for the forward direction applies here in mirror, since
    VIR_MIGRATE_NON_SHARED_DISK only copies device='disk' disks, never
    CD-ROMs/ISOs. A real bug found when testing this precisely, previously masked
    by the ORPHAN bug fixed at the same time: a cloud-init.iso file wrongly left
    on the local host after a previous forward migration gave the illusion that
    the return direction worked, whereas nothing really copied that file."""
    from app.core.cluster import get_node, node_ssh_options

    node = get_node(node_name)
    if not node:
        raise RuntimeError(f"Node '{node_name}' not found")
    Path(local_path).parent.mkdir(parents=True, exist_ok=True)
    ssh_target = f"{node['ssh_user']}@{node['hostname']}"
    ssh_opts = node_ssh_options()
    scp_r = subprocess.run(
        ["scp", *ssh_opts, "-P", str(node["ssh_port"]), f"{ssh_target}:{remote_path}", local_path],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if scp_r.returncode != 0:
        raise RuntimeError(f"Copy of '{Path(remote_path).name}' from {node_name} failed: {scp_r.stderr.strip()[:300]}")


def _domain_network_names(domain):
    root = ET.fromstring(domain.XMLDesc(0))
    names = []
    for iface in root.findall(".//devices/interface"):
        source_el = iface.find("source")
        if source_el is not None and source_el.get("network"):
            names.append(source_el.get("network"))
    return names


def _ensure_networks_active(conn, network_names):
    """Start (and autostart) on `conn` every libvirt network named in network_names
    that exists but is inactive. A real bug found when testing a real
    cross-site migration to a manually installed node that never went through
    the Hyperlite installer (see ensure_default_pool for the same kind of guard
    on the storage side): the 'default' network existed but was not started on
    the destination node, and libvirt refused the migration with a poorly
    actionable error ("network default is not active"). Rather than letting it
    fail and leaving the user to guess, Hyperlite fixes the common case itself.
    It does nothing if the network does not exist at all on the destination:
    that remains a real error to report as is (a misconfiguration, not a simple
    forgotten start)."""
    for name in network_names:
        try:
            net = conn.networkLookupByName(name)
        except libvirt.libvirtError:
            logger.debug("Ignored exception in _ensure_networks_active()", exc_info=True)
            continue  # network missing on the destination: migrate() will fail with a clear error, nothing to "repair" here
        if not net.isActive():
            net.create()
            net.setAutostart(True)


def _local_migrate_uri_host():
    """Address through which THIS node (the local host, never a row of the `nodes`
    table: it is the host running Hyperlite itself) is reachable by ANOTHER node
    for the QEMU data stream of a migration. Only useful when the local host is
    the DESTINATION of a migration (the far more frequent direction, TOWARDS a
    registered remote node, does not need it, see below). It queries Tailscale
    (already used by this project) rather than guessing or hard-coding an IP.
    Best-effort: None if unavailable, and the migration then tries without an
    explicit migrate_uri instead of failing here."""
    try:
        r = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError):
        logger.debug("Ignored exception in _local_migrate_uri_host()", exc_info=True)
    return None


def _migrate_vm_job(task_id, username, source_node, target_node, vm_name):
    from app.core.cluster import build_libvirt_uri, get_node

    # "local" is the FRONTEND convention for the local host (see
    # fetchNodes(), dashboard/src/api/client.js), never a row of the `nodes` table on
    # the backend. A real bug found when testing a complete round trip: open_conn()
    # tries to RESOLVE it as a registered remote node and fails with 404 as soon as
    # target_node is that literal value. It is translated here to None (= a local
    # connection, the same convention as everywhere else in the code, see open_conn()).
    dest_node_key = None if target_node == "local" else target_node
    src_conn = open_conn(source_node)
    dest_conn = None
    stop_event = threading.Event()
    progress_thread = threading.Thread(
        target=_migration_progress_job, args=(stop_event, task_id, source_node, vm_name), daemon=True
    )
    try:
        domain = src_conn.lookupByName(vm_name)
        dest_conn = open_conn(dest_node_key)

        _ensure_networks_active(dest_conn, _domain_network_names(domain))

        # Paths of the SOURCE disks, captured BEFORE the migration: needed to clean up the
        # original disk AFTER a successful migration on non-shared storage (see below).
        # The source domain is UNDEFINED (VIR_MIGRATE_UNDEFINE_SOURCE) once the migration
        # is over, so it can no longer be queried at that point.
        source_disk_paths = domain_disk_paths(domain)

        # Real bug found when testing a migration from a remote node to the local host on
        # NON-shared storage: "Cannot access storage file ... No such file or directory".
        # Confirmed through research (wiki.libvirt.org/Migration_fails_because_disk_image_
        # cannot_be_found.html) that some libvirt versions require the destination file to
        # ALREADY exist before the NON_SHARED_DISK transfer: the automatic creation on the
        # destination side does not trigger reliably in this specific direction (it works
        # in the local host -> remote node direction). Fixed by PRE-CREATING blank qcow2
        # files of the right size on the local host before calling migrateToURI3. The size
        # is read with domain.blockInfo() (a libvirt API that works through the remote
        # connection, with no need for direct file access, which would collide with the
        # write lock of an ACTIVE VM's disk anyway).
        if source_node and source_node != "local" and dest_node_key is None:
            root_for_targets = ET.fromstring(domain.XMLDesc(0))
            for disk_el in root_for_targets.findall(".//devices/disk"):
                if disk_el.get("device") != "disk":
                    continue
                source_el, target_el = disk_el.find("source"), disk_el.find("target")
                path = source_el.get("file") if source_el is not None else None
                dev = target_el.get("dev") if target_el is not None else None
                if not path or not dev or Path(path).exists():
                    continue
                try:
                    capacity = domain.blockInfo(dev)[0]
                    subprocess.run(
                        ["qemu-img", "create", "-f", "qcow2", path, str(capacity)],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                except (libvirt.libvirtError, subprocess.CalledProcessError):
                    logger.debug(
                        "Ignored exception in _migrate_vm_job()", exc_info=True
                    )  # best-effort: migrateToURI3 will report a clear error if the pre-creation fails

        # Attached cloud-init ISOs/CD-ROMs are never copied by libvirt
        # (VIR_MIGRATE_NON_SHARED_DISK only copies device='disk'): handled manually here
        # in BOTH directions. Without this copy the migration fails ('unable to access the
        # storage file') as soon as the destination domain references an ISO that is
        # missing on the destination. It was hidden for a while by the ORPHAN bug fixed at
        # the same time (a cloud-init.iso file left by an earlier forward migration gave
        # the illusion that the return direction worked, by coincidence with the same
        # expected path).
        copied_cdrom_paths = []
        if not source_node or source_node == "local":
            for cdrom_path in _cdrom_source_paths(domain):
                if Path(cdrom_path).exists():
                    _copy_file_to_node(cdrom_path, target_node, cdrom_path)
                    copied_cdrom_paths.append(cdrom_path)
        else:
            for cdrom_path in _cdrom_source_paths(domain):
                try:
                    _copy_file_from_node(source_node, cdrom_path, cdrom_path)
                    copied_cdrom_paths.append(cdrom_path)
                except RuntimeError:
                    logger.debug(
                        "Ignored exception in _migrate_vm_job()", exc_info=True
                    )  # ISO missing/unreachable on the source side: let migrateToURI3 fail with a clear error rather than guessing

        shared = uses_shared_storage(src_conn, dest_conn, domain)
        # The constant is called VIR_MIGRATE_PERSIST_DEST in this libvirt-python version,
        # not VIR_MIGRATE_PERSISTENT (which does not exist at all, verified through
        # dir(libvirt)). The wrong name crashed the thread BEFORE the first jobInfo call,
        # outside the `except libvirt.libvirtError` below, leaving a task stuck "en_cours"
        # forever with no trace for the user other than the server logs.
        flags = (
            libvirt.VIR_MIGRATE_LIVE
            | libvirt.VIR_MIGRATE_PEER2PEER
            | libvirt.VIR_MIGRATE_PERSIST_DEST
            | libvirt.VIR_MIGRATE_UNDEFINE_SOURCE
        )
        if not shared:
            flags |= libvirt.VIR_MIGRATE_NON_SHARED_DISK

        # NO VIR_MIGRATE_TUNNELLED: tested for real, and it systematically fails with
        # 'internal error: the host argument key must not have a Null value' as soon as
        # PEER2PEER+TUNNELLED are combined on this libvirt version (9.0.0, Debian 12) with
        # a qemu+ssh:// URI carrying query parameters (keyfile=/sshauth=privkey). It is
        # very probably a libvirt bug in that specific code path, not something fixable on
        # the Hyperlite side. WITHOUT the tunnel, the QEMU data stream (memory + disk with
        # VIR_MIGRATE_NON_SHARED_DISK) goes DIRECTLY between the two hosts rather than
        # through the SSH tunnel. That is acceptable here: a registered node (see
        # cluster.py) must already be directly reachable for SSH, so it almost always is
        # for this direct stream too (confirmed for real between two hosts on different
        # sites through Tailscale). A second real bug found at the same time: without
        # being told explicitly, QEMU tries to resolve the destination node's OWN host name
        # as IT knows it (e.g. 'hyperlite.home', not resolvable from the other host)
        # rather than the address through which Hyperlite actually reached it. Fixed by
        # providing migrate_uri explicitly, with the address already verified reachable
        # (the same registered `hostname` used for the qemu+ssh:// connection itself).
        if dest_node_key is None:
            local_addr = _local_migrate_uri_host()
            if source_node and source_node != "local":
                # Migration from a remote node to the local host (see
                # cluster.py::ensure_reverse_trust). A plain "qemu:///system" would be interpreted
                # BY THE SOURCE LIBVIRTD (the remote node's, since this URI runs THERE) as
                # "myself", which is exactly the already documented bug ("Attempt to migrate guest
                # to the same host"). An EXPLICIT qemu+ssh:// URI to the local host is needed,
                # authenticated with the dedicated key pushed to THIS node at its registration
                # (never the shared local host -> nodes key, see the ensure_reverse_trust docstring
                # for why).
                from app.core.cluster import get_reverse_key_remote_path

                if not local_addr:
                    raise RuntimeError(
                        "Tailscale address of the local host not found: migration from a remote node to the local host is impossible without it"
                    )
                dest_uri = f"qemu+ssh://root@{local_addr}/system?keyfile={get_reverse_key_remote_path()}&no_verify=1&sshauth=privkey"
            else:
                dest_uri = "qemu:///system"
            migrate_params = {"migrate_uri": f"tcp://{local_addr}"} if local_addr else {}
        else:
            dest_node = get_node(dest_node_key)
            dest_uri = build_libvirt_uri(dest_node)
            migrate_params = {"migrate_uri": f"tcp://{dest_node['hostname']}"}

        progress_thread.start()
        domain.migrateToURI3(dest_uri, migrate_params, flags)

        # Cleanup of the SOURCE disk: VIR_MIGRATE_NON_SHARED_DISK copies to the destination
        # but NEVER deletes the original file, so every migration on non-shared storage
        # systematically left an orphan qcow2 on the source node with nothing to signal it.
        # Only if `not shared`: a disk on SHARED storage must obviously never be deleted
        # (it is the SAME file seen by both nodes).
        if not shared:
            _delete_paths_on_node(source_disk_paths, source_node)
            # cdrom files copied above (only the local host -> remote node direction, see
            # copied_cdrom_paths): the source file (on the local host, so always a LOCAL
            # unlink here) becomes useless once the copy to the destination is confirmed
            # successful.
            _delete_paths_on_node(copied_cdrom_paths, source_node)

        stop_event.set()
        update_task_progress(task_id, 100)
        finish_task(task_id, "termine")
        log_action(
            username,
            "migrate_vm",
            vm_name,
            "succes",
            f"{source_node} -> {target_node} ({'shared' if shared else 'copied'} storage)",
        )
    except libvirt.libvirtError as e:
        stop_event.set()
        msg = describe_exception(e)
        finish_task(task_id, "echec", msg)
        log_action(username, "migrate_vm", vm_name, "echec", msg)
    except Exception as e:
        # Generic safety net: ANY unexpected exception must still close the task,
        # otherwise it stays "en_cours" forever in the UI with no visible explanation for
        # the user. It was really needed in testing (see the comment above about
        # VIR_MIGRATE_PERSIST_DEST).
        stop_event.set()
        finish_task(task_id, "echec", f"Internal error: {e}")
        log_action(username, "migrate_vm", vm_name, "echec", f"Internal error: {e}")
    finally:
        stop_event.set()
        src_conn.close()
        if dest_conn:
            dest_conn.close()


@router.get("/{name}/migration-check")
def migration_check(name: str, target_node: str, node: str | None = None, user: dict = Depends(require_role("admin"))):
    """Compatibility diagnostic BEFORE a migration: read-only, it migrates nothing. See
    app/core/cluster_compat.py."""
    if _norm_node(target_node) == _norm_node(node):
        raise HTTPException(status_code=422, detail="The destination node must be different from the source node")
    src_conn = open_conn(_norm_node(node))
    try:
        try:
            domain = src_conn.lookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        dst_conn = open_conn(_norm_node(target_node))
        try:
            return cluster_compat.report(cluster_compat.check_vm_migration(src_conn, dst_conn, domain))
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


@router.post("/{name}/migrate", status_code=202)
def migrate_vm(
    name: str, payload: MigrateRequest, node: str | None = None, user: dict = Depends(require_role("admin"))
):
    """Live migration to another node (relying on shared storage when available).
    Restricted to admins (not a per-VM ACL privilege like vm.clone): moving a VM
    changes the resource allocation of a node OF THE WHOLE CLUSTER, a scope beyond
    what an ACL scoped to one VM is meant to cover."""
    if _norm_node(payload.target_node) == _norm_node(node):
        raise HTTPException(status_code=422, detail="The destination node must be different from the source node")
    # Migration from a remote node to the local host was long blocked here, because
    # peer-to-peer migration is initiated by the SOURCE libvirtd, which needs to be
    # able to connect ITSELF to the local host. It is now possible through a dedicated
    # reverse SSH trust, established automatically when each node is registered (see
    # cluster.py::ensure_reverse_trust, a key SPECIFIC to that node, never shared). If
    # that trust could not be established (a node registered before this existed, or a
    # best-effort failure at the time), the migration fails with a clear SSH error on
    # the task side rather than being refused a priori here.

    src_conn = open_conn(node)
    try:
        try:
            domain = src_conn.lookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        if not domain.isActive():
            raise HTTPException(
                status_code=409,
                detail="The VM must be running for a live migration (use export/import for a stopped VM)",
            )

        # Check the destination right away (a clear immediate error: open_conn already
        # raises an explicit HTTPException if the node is not found or unreachable) rather
        # than letting the background thread fail with no other form of trial for the
        # user. "local" is the frontend convention for the local
        # host, never a row of the `nodes` table (see _migrate_vm_job for the same
        # treatment).
        dest_conn = open_conn(_norm_node(payload.target_node))
        try:
            compat = cluster_compat.report(cluster_compat.check_vm_migration(src_conn, dest_conn, domain))
            if compat["resume"]["bloquant"] and not payload.ignorer_verifications:
                blocages = [c["message"] for c in compat["controles"] if c["statut"] == "blocking"]
                raise HTTPException(
                    status_code=409, detail="Migration refused by the compatibility diagnostic: " + " ; ".join(blocages)
                )
            try:
                dest_conn.lookupByName(name)
                raise HTTPException(status_code=409, detail=f"A VM '{name}' already exists on the destination node")
            except libvirt.libvirtError:
                logger.debug("Ignored exception in migrate_vm()", exc_info=True)
        finally:
            dest_conn.close()

        source_node = node or "local"
        task_id = create_task("migrate_vm", name, node=source_node, username=user["username"])
        threading.Thread(
            target=_migrate_vm_job,
            args=(task_id, user["username"], node, payload.target_node, name),
            daemon=True,
        ).start()
        return {"task_id": task_id, "statut": "en_cours"}
    finally:
        src_conn.close()
