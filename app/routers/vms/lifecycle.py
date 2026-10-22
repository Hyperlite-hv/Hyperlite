import contextlib
import logging
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import libvirt
from fastapi import Depends, HTTPException

from app.core import firmware, guest_agent, vm_locks, vm_records, zfs_storage
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import (
    open_conn,
)
from app.core.security import require_role, require_vm_privilege
from app.core.tasks import create_task
from app.core.vm_builder import generated_drives
from app.core.vm_meta import (
    touch_vm_activity,
)
from app.routers.vms._shared import _domain_summary, router

logger = logging.getLogger(__name__)


def _stopped_networks(conn, domain):
    """The libvirt networks the VM has an interface on that are not running, in order."""
    try:
        root = ET.fromstring(domain.XMLDesc(0))
    except (libvirt.libvirtError, ET.ParseError):
        return []
    names = dict.fromkeys(s.get("network") for s in root.findall("./devices/interface/source") if s.get("network"))
    stopped = []
    for net in names:
        try:
            if not conn.networkLookupByName(net).isActive():
                stopped.append(net)
        except libvirt.libvirtError:
            stopped.append(net)  # gone: libvirt would refuse too
    return stopped


@router.post("/{name}/start")
def start_vm(name: str, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.power"))):
    conn = open_conn(node)
    task_id = create_task("start_vm", name, node=node, username=user["username"])
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "start_vm", name, "failed", "VM not found", task_id=task_id)
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        if domain.isActive():
            log_action(user["username"], "start_vm", name, "failed", "VM already running", task_id=task_id)
            raise HTTPException(status_code=409, detail=f"VM '{name}' is already running")
        # A restore, a cold backup, a disk move or a clone works on the stopped VM's disks: started meanwhile, the VM
        # would write to files being copied or replaced (a restore then deletes them), as Proxmox's lock prevents.
        busy = vm_locks.running(name, node)
        if busy:
            msg = f"VM '{name}' is busy: {busy} is in progress. Start it once it has finished."
            log_action(user["username"], "start_vm", name, "failed", msg, task_id=task_id)
            raise HTTPException(status_code=409, detail=msg)
        stopped = _stopped_networks(conn, domain)
        if stopped:
            # libvirt's own refusal named no remedy ("network 'default' is not active").
            msg = (
                f"The network {', '.join(repr(n) for n in stopped)} of this VM is stopped: start it on the Network page"
            )
            log_action(user["username"], "start_vm", name, "failed", msg, task_id=task_id)
            raise HTTPException(status_code=409, detail=msg)
        try:
            domain.create()
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "start_vm", name, "failed", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Unable to start the VM: {msg}") from e
        touch_vm_activity(name)  # resets the inactivity counter
        log_action(user["username"], "start_vm", name, "success", task_id=task_id)
        return _domain_summary(domain)
    finally:
        conn.close()


# A guest may ignore the shutdown request (the ACPI button pressed while it is still booting, or just after a live
# migration: twice in a 24 h lab run, the guest healthy and stopping at once on a second request): the VM then kept
# running, and the request said nothing. The request is sent again after ASK_AGAIN_S while the VM runs; asked with
# force_after, the VM still running at the end is stopped by force, as Proxmox's shutdown timeout with forceStop.
FORCE_AFTER_RANGE = (10, 3600)
ASK_AGAIN_S = (30, 90)


def _watch_stop(name, node, username, force_after=None):
    from app.core.libvirt_utils import open_conn as _open

    asks = [t for t in ASK_AGAIN_S if force_after is None or t < force_after]
    if force_after and not asks:
        asks = [force_after / 2]
    end = force_after or ASK_AGAIN_S[-1] + 1
    start = time.monotonic()
    while time.monotonic() - start < end:
        time.sleep(2)
        conn = _open(node)
        try:
            domain = conn.lookupByName(name)
            if not domain.isActive():
                return
            if asks and time.monotonic() - start >= asks[0]:
                asks.pop(0)
                with contextlib.suppress(libvirt.libvirtError):
                    guest_agent.shutdown(domain)
        except libvirt.libvirtError:
            return  # gone meanwhile
        finally:
            conn.close()
    if not force_after:
        return
    conn = _open(node)
    try:
        domain = conn.lookupByName(name)
        if domain.isActive():
            domain.destroy()
            log_action(
                username, "force_stop_vm", name, "success", f"still running {force_after} s after the shutdown request"
            )
    except libvirt.libvirtError as e:
        log_action(username, "force_stop_vm", name, "failed", describe_exception(e))
    finally:
        conn.close()


@router.post("/{name}/stop")
def stop_vm(
    name: str,
    force: bool = False,
    force_after: int | None = None,
    node: str | None = None,
    user: dict = Depends(require_vm_privilege("vm.power")),
):
    if force_after is not None and not FORCE_AFTER_RANGE[0] <= force_after <= FORCE_AFTER_RANGE[1]:
        raise HTTPException(
            status_code=422, detail=f"force_after: between {FORCE_AFTER_RANGE[0]} and {FORCE_AFTER_RANGE[1]} seconds"
        )
    conn = open_conn(node)
    action_name = "force_stop_vm" if force else "stop_vm"
    task_id = create_task(action_name, name, node=node, username=user["username"])
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "stop_vm", name, "failed", "VM not found", task_id=task_id)
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        if not domain.isActive():
            log_action(user["username"], "stop_vm", name, "failed", "VM already stopped", task_id=task_id)
            raise HTTPException(status_code=409, detail=f"VM '{name}' is already stopped")
        try:
            method = None
            if force:
                domain.destroy()
            else:
                method = guest_agent.shutdown(domain)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], action_name, name, "failed", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Unable to stop the VM: {msg}") from e
        detail = f"via {method}" if method else None
        if not force:
            if force_after:
                detail = f"{detail}, forced after {force_after} s if still running"
            threading.Thread(target=_watch_stop, args=(name, node, user["username"], force_after), daemon=True).start()
        log_action(user["username"], action_name, name, "success", detail, task_id=task_id)
        return _domain_summary(domain)
    finally:
        conn.close()


@router.post("/{name}/restart")
def restart_vm(
    name: str, force: bool = False, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.power"))
):
    conn = open_conn(node)
    task_id = create_task("restart_vm", name, node=node, username=user["username"])
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "restart_vm", name, "failed", "VM not found", task_id=task_id)
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        if not domain.isActive():
            log_action(user["username"], "restart_vm", name, "failed", "VM stopped", task_id=task_id)
            raise HTTPException(status_code=409, detail=f"VM '{name}' is stopped, start it first")
        try:
            method = None
            if force:
                busy = vm_locks.running(name, node)
                if busy:  # a hot backup or a live copy holds the VM: destroying it would break that work
                    raise HTTPException(status_code=409, detail=f"VM '{name}' is busy: {busy} is in progress")
                domain.destroy()
                domain.create()
            else:
                method = guest_agent.reboot(domain)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "restart_vm", name, "failed", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Unable to restart the VM: {msg}") from e
        log_action(
            user["username"], "restart_vm", name, "success", f"via {method}" if method else None, task_id=task_id
        )
        return _domain_summary(domain)
    finally:
        conn.close()


def _perform_vm_deletion(conn, domain, name, node=None):
    """The real deletion sequence (disks + reserved IP + metadata), shared by
    DELETE /vms/{name} (user confirmation) and the automatic cleanup of inactive
    VMs (app/core/vm_cleanup.py): no divergence is possible between the two
    paths. The caller must have ALREADY checked that the VM is inactive; this
    function does not revalidate it. It may raise libvirt.libvirtError
    (undefine fails), which the caller must translate.

    `node`: None = local VM (the historical behaviour, files removed through
    the LOCAL Path.unlink()). On a REMOTE node, `conn` is a qemu+ssh://
    connection purpose disk paths remain paths on the REMOTE FILESYSTEM: a local
    unlink() could delete the wrong file (or nothing at all) on the local host.
    Remote deletion therefore goes through SSH (the same cluster key as the rest,
    see _copy_file_to_node) rather than unlink()."""
    # Captured BEFORE the undefine (no longer queryable afterwards) for ALL the disks
    # and ALL the interfaces, not only the first ones: a multi-disk/multi-NIC VM must
    # not leave an orphan qcow2 file or a phantom DHCP reservation for its disks and
    # interfaces beyond the first. (This was found when testing multi-disk cloning:
    # the secondary disk of a deleted VM stayed on the host disk and caused a name
    # conflict at the next clone.)
    # The drives Hyperlite generated for THIS VM (cloud-init, and the Kickstart/autoinstall answers of VMs created
    # before 1.0) go with it, wherever its pool put them, and never an image of the ISO library that carries the
    # same name (vm_builder.generated_drives).

    disk_paths_to_remove = []
    # BLOCK disks (ZFS zvols): source='dev', not 'file'. They are handled separately
    # (deletion through `zfs destroy`, not Path.unlink()), see below. They are always
    # local for now (single-node ZFS management, the same limit as
    # create_vm/storage.py): never expected in the remote deletion path (node= towards
    # another node).
    zvol_paths_to_remove = []
    ifaces_to_release = []
    extra_undefine_flags = 0
    try:
        root = ET.fromstring(domain.XMLDesc())
        extra_undefine_flags = firmware.undefine_flags(root)
        own_drives = {path for _disk, path in generated_drives(root, name)}
        for disk_el in root.findall(".//devices/disk"):
            source_el = disk_el.find("source")
            if source_el is None:
                continue
            if disk_el.get("type") == "block":
                source_dev = source_el.get("dev")
                if source_dev and disk_el.get("device") == "disk":
                    zvol_paths_to_remove.append(source_dev)
                continue
            source_file = source_el.get("file")
            if not source_file:
                continue
            if disk_el.get("device") == "disk" or Path(source_file) in own_drives:
                disk_paths_to_remove.append(Path(source_file))
        ifaces_to_release = vm_records.reservations(root)
    except (libvirt.libvirtError, ET.ParseError):
        logger.debug("Ignored exception in _perform_vm_deletion()", exc_info=True)

    # VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA: without this flag, undefine() simply
    # fails as soon as one or more snapshots remain ("cannot delete inactive domain
    # with N snapshots"), even partially deleted ones. Harmless here: the qcow2 file
    # that held the internal snapshots is deleted right afterwards anyway (unlink
    # below), and the VM itself is already irrevocably confirmed as deleted.
    # A UEFI VM also takes its NVRAM and TPM state with it (see app/core/firmware.py).
    domain.undefineFlags(libvirt.VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA | extra_undefine_flags)

    vm_records.release_addresses(conn, ifaces_to_release)

    if node:
        from app.core.cluster import get_node, node_ssh_options

        remote_node = get_node(node)
        if remote_node:
            ssh_opts = node_ssh_options()
            ssh_target = f"{remote_node['ssh_user']}@{remote_node['hostname']}"
            for disk_path in disk_paths_to_remove:
                subprocess.run(
                    ["ssh", *ssh_opts, "-p", str(remote_node["ssh_port"]), ssh_target, "rm", "-f", str(disk_path)],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
    else:
        for disk_path in disk_paths_to_remove:
            disk_path.unlink(missing_ok=True)
        for zvol_path in zvol_paths_to_remove:
            # '/dev/zvol/<pool>/<name>': the pool is the 3rd segment, the name is the rest (a
            # zvol name never contains a '/' itself, see validate_zfs_name). Best-effort: a
            # VM already partially cleaned up (a zvol removed by hand outside Hyperlite) must
            # not make the whole deletion fail.
            parts = zvol_path.strip("/").split("/")
            if len(parts) >= 4 and parts[0] == "dev" and parts[1] == "zvol":
                try:
                    zfs_storage.delete_zvol(parts[2], "/".join(parts[3:]))
                except zfs_storage.ZfsError:
                    logger.debug("Ignored exception in _perform_vm_deletion()", exc_info=True)
    vm_records.forget(name, node)


@router.delete("/{name}")
def delete_vm(name: str, confirm: bool = False, node: str | None = None, user: dict = Depends(require_role("admin"))):
    with vm_locks.claim_or_409(name, "a deletion", node=node):
        return _delete_vm(name, confirm, node, user)


def _delete_vm(name, confirm, node, user):
    conn = open_conn(node)
    task_id = create_task("delete_vm", name, node=node, username=user["username"])
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "delete_vm", name, "failed", "VM not found", task_id=task_id)
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        if domain.isActive():
            log_action(user["username"], "delete_vm", name, "failed", "VM running, it must be stopped", task_id=task_id)
            raise HTTPException(status_code=409, detail=f"VM '{name}' is running. Stop it before deleting it")
        if not confirm:
            log_action(user["username"], "delete_vm", name, "failed", "Confirmation manquante", task_id=task_id)
            raise HTTPException(
                status_code=400, detail="Irreversible action: add ?confirm=true to confirm the deletion"
            )

        try:
            _perform_vm_deletion(conn, domain, name, node=node)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "delete_vm", name, "failed", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Unable to delete the VM: {msg}") from e

        log_action(user["username"], "delete_vm", name, "success", task_id=task_id)
        return {"message": f"VM '{name}' deleted"}
    finally:
        conn.close()


# --- Automatic deletion of inactive VMs: post-creation management
# (enable/reconfigure/disable). The creation itself goes through
# VMCreate.auto_cleanup_days above. The same privilege level as the per-VM
# firewall (vm.hardware): this is a VM setting, not a cluster-wide action like
# migration.
