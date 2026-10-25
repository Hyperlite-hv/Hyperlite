"""A VM's snapshots: taken, restored and deleted by hyperlite-daemon (app/core/vm_snapshots.py), listed here. The
route answers every refusal at once (the daemon's checks, with check_only), then the work runs as a task."""

import logging
import xml.etree.ElementTree as ET

import libvirt
from fastapi import Depends, HTTPException
from pydantic import BaseModel

from app.core import checkpoints, privileged, vm_locks, vm_snapshots, zfs_storage
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import (
    open_conn,
)
from app.core.listing import listing
from app.core.security import get_current_user, require_vm_privilege
from app.core.tasks import create_task, finish_task, start_thread, task_log
from app.daemon.registry import DaemonError
from app.routers.vms._shared import router

logger = logging.getLogger(__name__)

# Saving a running VM's memory, or a large disk's snapshot, takes longer than an ordinary operation.
SNAPSHOT_TIMEOUT_S = 3600


def _snapshot_summary(snap):
    xml_desc = snap.getXMLDesc()
    root = ET.fromstring(xml_desc)
    desc_elem = root.find("description")
    creation_elem = root.find("creationTime")
    state_elem = root.find("state")
    try:
        parent_nom = snap.getParent().getName()
    except libvirt.libvirtError:
        parent_nom = None
    return {
        "name": snap.getName(),
        "description": desc_elem.text if desc_elem is not None else None,
        "created_at": creation_elem.text if creation_elem is not None else None,
        "vm_state": state_elem.text if state_elem is not None else None,
        "current": snap.isCurrent() == 1,
        "parent": parent_nom,
    }


def _disk_only(name, created_at):
    return {
        "name": name,
        "description": None,
        "created_at": created_at,
        "vm_state": "disk_only",
        "current": False,
        "parent": None,
    }


@router.get("/{name}/snapshots")
def list_snapshots(name: str, user: dict = Depends(get_current_user)):
    conn = open_conn(readonly=True)
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "list_snapshots", name, "failed", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        root = ET.fromstring(domain.XMLDesc())
        zvol_specs = vm_snapshots.zvol_disks(root)
        if zvol_specs:
            # VM on a ZFS pool: one "logical" snapshot per NAME, merged across all the VM's zvols. The list of the
            # first zvol is taken as the reference (all the zvols of a VM are snapshotted together by
            # snapshot_zvols(), so the same names exist everywhere barring a very rare external inconsistency).
            pool0, name0 = zvol_specs[0]
            result = [
                _disk_only(s["name"], str(s["creation_epoch"])) for s in zfs_storage.list_zvol_snapshots(pool0, name0)
            ]
        elif vm_snapshots.lvm_disk_paths(root):
            # LVM-thin: the snapshots of the first disk, all the VM's disks being snapshotted together; thick volumes
            # have none. `lvm` reads as root: hyperlite-daemon lists them.
            try:
                found = privileged.run("vm.lvm_snapshots", user=user["username"], vm=name)["snapshots"]
            except DaemonError as e:
                raise privileged.http_error(e) from None
            result = [_disk_only(s["name"], s["created_at"]) for s in found]
        else:
            result = []
            for snap in domain.listAllSnapshots():
                # A snapshot deleted while the list is built (the page refreshes it during a deletion) is skipped:
                # the whole list used to fail with "Domain snapshot not found".
                try:
                    result.append(_snapshot_summary(snap))
                except libvirt.libvirtError as e:
                    if e.get_error_code() != libvirt.VIR_ERR_NO_DOMAIN_SNAPSHOT:
                        raise
                    logger.debug("Snapshot vanished while listing %s: %s", name, e)
        log_action(user["username"], "list_snapshots", name, "success")
        return listing(result)
    finally:
        conn.close()


class SnapshotCreate(BaseModel):
    name: str
    description: str | None = None


def _daemon(op, user, action, vm, snapshot_name, **args):
    """`op` checked or run by hyperlite-daemon: its result, or its refusal recorded and answered."""
    try:
        return privileged.run(op, user=user["username"], vm=vm, name=snapshot_name, **args)
    except DaemonError as e:
        log_action(user["username"], action, vm, "failed", e.detail)
        raise privileged.http_error(e) from None


def _daemon_task(op, vm, user, action, snapshot_name, **args):
    """The work as a task, in a thread that holds the VM's lock until it ends; the replication chain the daemon's
    dropped checkpoints end is forgotten here."""
    claim = vm_locks.claim_or_409(vm, "a snapshot")

    def work(task_id):
        try:
            done = privileged.run(
                op, user=user["username"], timeout=SNAPSHOT_TIMEOUT_S, vm=vm, name=snapshot_name, **args
            )
            if done and done.get("checkpoints_dropped"):
                checkpoints.forget(vm, done["checkpoints_dropped"])
            finish_task(task_id, "succeeded")
            log_action(user["username"], action, vm, "success", f"Snapshot '{snapshot_name}'")
        except Exception as e:  # any error closes the task
            msg = e.detail if isinstance(e, DaemonError) else describe_exception(e)
            finish_task(task_id, "failed", msg)
            log_action(user["username"], action, vm, "failed", f"Snapshot '{snapshot_name}': {msg}")

    try:
        task_id = create_task(action, vm, node=None, username=user["username"])
        task_log(task_id, f"Snapshot '{snapshot_name}'")
        start_thread(vm_locks.released_after(claim, work), task_id, task_id=task_id)
    except BaseException:
        claim.release()
        raise
    return task_id


@router.post("/{name}/snapshots", status_code=202)
def create_snapshot(name: str, payload: SnapshotCreate, user: dict = Depends(require_vm_privilege("vm.snapshot"))):
    """A VM on ZFS or LVM-thin gets a disk-only snapshot (never its memory), the qcow2 internal snapshot otherwise
    (with its memory while it runs): said by the list's vm_state rather than silently different."""
    fields = {"description": payload.description}
    _daemon("vm.snapshot_create", user, "create_snapshot", name, payload.name, check_only=True, **fields)
    task_id = _daemon_task("vm.snapshot_create", name, user, "create_snapshot", payload.name, **fields)
    return {"task_id": task_id, "name": payload.name, "status": "running"}


@router.post("/{name}/snapshots/{snapshot_name}/restore", status_code=202)
def restore_snapshot(
    name: str, snapshot_name: str, confirm: bool = False, user: dict = Depends(require_vm_privilege("vm.snapshot"))
):
    _daemon("vm.snapshot_restore", user, "restore_snapshot", name, snapshot_name, check_only=True)
    if not confirm:
        log_action(
            user["username"], "restore_snapshot", name, "failed", f"Snapshot '{snapshot_name}': confirmation missing"
        )
        raise HTTPException(status_code=400, detail="Irreversible action: add ?confirm=true to confirm the restore")
    task_id = _daemon_task("vm.snapshot_restore", name, user, "restore_snapshot", snapshot_name)
    return {
        "task_id": task_id,
        "status": "running",
        "message": f"Restore of '{name}' to '{snapshot_name}' in progress",
    }


@router.delete("/{name}/snapshots/{snapshot_name}")
def delete_snapshot(name: str, snapshot_name: str, user: dict = Depends(require_vm_privilege("vm.snapshot"))):
    # Synchronous: deleting a snapshot is nearly instantaneous whatever its kind (libvirt reparents a child itself,
    # `zfs destroy` and `lvremove` of a snapshot are as quick).
    with vm_locks.claim_or_409(name, "a snapshot deletion"):
        task_id = create_task("delete_snapshot", name, node=None, username=user["username"])
        task_log(task_id, f"Snapshot '{snapshot_name}'")
        try:
            privileged.run("vm.snapshot_delete", user=user["username"], vm=name, name=snapshot_name)
        except Exception as e:  # never a task left running: closed with what happened
            msg = e.detail if isinstance(e, DaemonError) else describe_exception(e)
            log_action(
                user["username"], "delete_snapshot", name, "failed", f"Snapshot '{snapshot_name}': {msg}",
                task_id=task_id,
            )  # fmt: skip
            if isinstance(e, DaemonError):
                raise privileged.http_error(e) from None
            raise
        finish_task(task_id, "succeeded")
        log_action(user["username"], "delete_snapshot", name, "success", f"Snapshot '{snapshot_name}'")
        return {"message": f"Snapshot '{snapshot_name}' deleted"}
