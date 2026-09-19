import logging
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import libvirt
from fastapi import Depends, HTTPException

from app.core import zfs_storage
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import (
    open_conn,
)
from app.core.network_alloc import release_static_ip
from app.core.permissions import delete_acl_for_vm, remove_vm_from_all_pools
from app.core.security import require_role, require_vm_privilege
from app.core.tasks import create_task
from app.core.vm_meta import (
    clear_provisioning,
    delete_vm_auto_cleanup,
    delete_vm_os_label,
    delete_vm_ssh_user,
    touch_vm_activity,
)
from app.routers.vms._shared import _domain_summary, router

logger = logging.getLogger(__name__)


@router.post("/{name}/start")
def start_vm(name: str, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.power"))):
    conn = open_conn(node)
    task_id = create_task("start_vm", name, node=conn.getHostname(), username=user["username"])
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "start_vm", name, "echec", "VM not found", task_id=task_id)
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        if domain.isActive():
            log_action(user["username"], "start_vm", name, "echec", "VM already running", task_id=task_id)
            raise HTTPException(status_code=409, detail=f"VM '{name}' is already running")
        try:
            domain.create()
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "start_vm", name, "echec", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Unable to start the VM: {msg}") from e
        touch_vm_activity(name)  # resets the inactivity counter
        log_action(user["username"], "start_vm", name, "succes", task_id=task_id)
        return _domain_summary(domain)
    finally:
        conn.close()


@router.post("/{name}/stop")
def stop_vm(
    name: str, force: bool = False, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.power"))
):
    conn = open_conn(node)
    action_name = "force_stop_vm" if force else "stop_vm"
    task_id = create_task(action_name, name, node=conn.getHostname(), username=user["username"])
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "stop_vm", name, "echec", "VM not found", task_id=task_id)
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        if not domain.isActive():
            log_action(user["username"], "stop_vm", name, "echec", "VM already stopped", task_id=task_id)
            raise HTTPException(status_code=409, detail=f"VM '{name}' is already stopped")
        try:
            if force:
                domain.destroy()
            else:
                domain.shutdown()
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], action_name, name, "echec", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Unable to stop the VM: {msg}") from e
        log_action(user["username"], action_name, name, "succes", task_id=task_id)
        return _domain_summary(domain)
    finally:
        conn.close()


@router.post("/{name}/restart")
def restart_vm(
    name: str, force: bool = False, node: str | None = None, user: dict = Depends(require_vm_privilege("vm.power"))
):
    conn = open_conn(node)
    task_id = create_task("restart_vm", name, node=conn.getHostname(), username=user["username"])
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "restart_vm", name, "echec", "VM not found", task_id=task_id)
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        if not domain.isActive():
            log_action(user["username"], "restart_vm", name, "echec", "VM stopped", task_id=task_id)
            raise HTTPException(status_code=409, detail=f"VM '{name}' is stopped, start it first")
        try:
            if force:
                domain.destroy()
                domain.create()
            else:
                domain.reboot()
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "restart_vm", name, "echec", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Unable to restart the VM: {msg}") from e
        log_action(user["username"], "restart_vm", name, "succes", task_id=task_id)
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
    connection but disk paths remain paths on the REMOTE FILESYSTEM: a local
    unlink() could delete the wrong file (or nothing at all) on the local host.
    Remote deletion therefore goes through SSH (the same cluster key as the rest,
    see _copy_file_to_node) rather than unlink()."""
    # Captured BEFORE the undefine (no longer queryable afterwards) for ALL the disks
    # and ALL the interfaces, not only the first ones: a multi-disk/multi-NIC VM must
    # not leave an orphan qcow2 file or a phantom DHCP reservation for its disks and
    # interfaces beyond the first. (This was found when testing multi-disk cloning:
    # the secondary disk of a deleted VM stayed on the host disk and caused a name
    # conflict at the next clone.)
    # ISOs generated by Hyperlite itself for THIS VM (cloud-init/kickstart/autoinstall)
    # are recognized by their file NAME, never by their directory: they can live on ANY
    # pool (see create_vm), not necessarily IMAGES_DIR. The REAL path is captured from
    # the domain XML, as already done for the disks; rebuilding it by hand through
    # IMAGES_DIR would leave the cloud-init ISO orphaned on any non-default pool.
    OWN_GENERATED_ISO_NAMES = {f"{name}-cloudinit.iso", f"{name}-oemdrv.iso", f"{name}-autoinstall.iso"}

    disk_paths_to_remove = []
    # BLOCK disks (ZFS zvols): source='dev', not 'file'. They are handled separately
    # (deletion through `zfs destroy`, not Path.unlink()), see below. They are always
    # local for now (single-node ZFS management, the same limit as
    # create_vm/storage.py): never expected in the remote deletion path (node= towards
    # another node).
    zvol_paths_to_remove = []
    ifaces_to_release = []
    try:
        root = ET.fromstring(domain.XMLDesc())
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
            if disk_el.get("device") == "disk" or (
                disk_el.get("device") == "cdrom" and Path(source_file).name in OWN_GENERATED_ISO_NAMES
            ):
                disk_paths_to_remove.append(Path(source_file))
        for iface in root.findall(".//interface[@type='network']"):
            mac_el, source_el = iface.find("mac"), iface.find("source")
            if mac_el is not None and source_el is not None and source_el.get("network"):
                ifaces_to_release.append((source_el.get("network"), mac_el.get("address")))
    except (libvirt.libvirtError, ET.ParseError):
        logger.debug("Ignored exception in _perform_vm_deletion()", exc_info=True)

    # VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA: without this flag, undefine() simply
    # fails as soon as one or more snapshots remain ("cannot delete inactive domain
    # with N snapshots"), even partially deleted ones. Harmless here: the qcow2 file
    # that held the internal snapshots is deleted right afterwards anyway (unlink
    # below), and the VM itself is already irrevocably confirmed as deleted.
    domain.undefineFlags(libvirt.VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA)

    for iface_network, iface_mac in ifaces_to_release:
        try:
            release_static_ip(conn, iface_network, iface_mac)
        except libvirt.libvirtError:
            logger.debug("Ignored exception in _perform_vm_deletion()", exc_info=True)

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
    delete_vm_ssh_user(name)
    delete_vm_os_label(name)
    clear_provisioning(name)
    delete_acl_for_vm(name)
    remove_vm_from_all_pools(name)
    delete_vm_auto_cleanup(name)


@router.delete("/{name}")
def delete_vm(name: str, confirm: bool = False, node: str | None = None, user: dict = Depends(require_role("admin"))):
    conn = open_conn(node)
    task_id = create_task("delete_vm", name, node=conn.getHostname(), username=user["username"])
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "delete_vm", name, "echec", "VM not found", task_id=task_id)
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        if domain.isActive():
            log_action(user["username"], "delete_vm", name, "echec", "VM running, it must be stopped", task_id=task_id)
            raise HTTPException(status_code=409, detail=f"VM '{name}' is running. Stop it before deleting it")
        if not confirm:
            log_action(user["username"], "delete_vm", name, "echec", "Confirmation manquante", task_id=task_id)
            raise HTTPException(
                status_code=400, detail="Irreversible action: add ?confirm=true to confirm the deletion"
            )

        try:
            _perform_vm_deletion(conn, domain, name, node=node)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "delete_vm", name, "echec", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Unable to delete the VM: {msg}") from e

        log_action(user["username"], "delete_vm", name, "succes", task_id=task_id)
        return {"message": f"VM '{name}' deleted"}
    finally:
        conn.close()


# --- Automatic deletion of inactive VMs: post-creation management
# (enable/reconfigure/disable). The creation itself goes through
# VMCreate.auto_cleanup_days above. The same privilege level as the per-VM
# firewall (vm.hardware): this is a VM setting, not a cluster-wide action like
# migration.
