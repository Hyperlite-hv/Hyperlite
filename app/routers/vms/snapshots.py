import logging
import threading
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

import libvirt
from fastapi import Depends, HTTPException
from pydantic import BaseModel

from app.core import zfs_storage
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import (
    open_conn,
)
from app.core.security import get_current_user, require_vm_privilege
from app.core.tasks import create_task, finish_task
from app.core.vm_builder import (
    validate_name,
)
from app.routers.vms._shared import _zvol_disks_of_domain, router

logger = logging.getLogger(__name__)


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
        "nom": snap.getName(),
        "description": desc_elem.text if desc_elem is not None else None,
        "date_creation": creation_elem.text if creation_elem is not None else None,
        "etat_vm": state_elem.text if state_elem is not None else None,
        "actuel": snap.isCurrent() == 1,
        "parent": parent_nom,
    }


@router.get("/{name}/snapshots")
def list_snapshots(name: str, user: dict = Depends(get_current_user)):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "list_snapshots", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        zvol_specs = _zvol_disks_of_domain(domain)
        if zvol_specs:
            # VM on a ZFS pool: one "logical" snapshot per NAME, merged across all the VM's
            # zvols. The list of the first zvol is taken as the reference (all the zvols of a
            # VM are snapshotted together by snapshot_zvols(), so the same names exist
            # everywhere barring a very rare external inconsistency).
            pool0, name0 = zvol_specs[0]
            result = [
                {
                    "nom": s["nom"],
                    "description": None,
                    "date_creation": str(s["creation_epoch"]),
                    "etat_vm": "disque_seul",
                    "actuel": False,
                    "parent": None,
                }
                for s in zfs_storage.list_zvol_snapshots(pool0, name0)
            ]
        else:
            snaps = domain.listAllSnapshots()
            result = [_snapshot_summary(s) for s in snaps]
        log_action(user["username"], "list_snapshots", name, "succes")
        return result
    finally:
        conn.close()


class SnapshotCreate(BaseModel):
    name: str
    description: str | None = None


def _create_snapshot_job(task_id, username, vm_name, snap_name, snap_xml):
    """Runs in a separate thread (see create_snapshot) with its OWN libvirt
    connection: never share a Domain/Connect object between threads, libvirt's
    Python bindings do not guarantee it. The final log_action (success/failure) is
    written here, at the real end of the work, not at the synchronous submission,
    which does not know yet whether it will work."""
    conn = open_conn()
    try:
        domain = conn.lookupByName(vm_name)
        domain.snapshotCreateXML(snap_xml, 0)
        finish_task(task_id, "termine")
        log_action(username, "create_snapshot", snap_name, "succes")
    except libvirt.libvirtError as e:
        msg = describe_exception(e)
        finish_task(task_id, "echec", msg)
        log_action(username, "create_snapshot", snap_name, "echec", msg)
    finally:
        conn.close()


def _create_zvol_snapshot_job(task_id, username, zvol_specs, snap_name):
    """ZFS equivalent of _create_snapshot_job(). Nearly instantaneous in practice
    (unlike the qcow2 + memory snapshot, which can take several seconds), it
    still runs as a background task to keep exactly the same API contract
    (task_id, 202) on both sides, so no frontend change is needed."""
    try:
        zfs_storage.snapshot_zvols(zvol_specs, snap_name)
        finish_task(task_id, "termine")
        log_action(username, "create_snapshot", snap_name, "succes")
    except zfs_storage.ZfsError as e:
        finish_task(task_id, "echec", e.message)
        log_action(username, "create_snapshot", snap_name, "echec", e.message)


@router.post("/{name}/snapshots", status_code=202)
def create_snapshot(name: str, payload: SnapshotCreate, user: dict = Depends(require_vm_privilege("vm.snapshot"))):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "create_snapshot", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        name_error = validate_name(payload.name)
        if name_error:
            log_action(user["username"], "create_snapshot", payload.name, "echec", name_error)
            raise HTTPException(status_code=422, detail=name_error)

        zvol_specs = _zvol_disks_of_domain(domain)
        if zvol_specs:
            # VM on a ZFS pool: the native ZFS mechanism, see zfs_storage.py. It is a
            # DISK-ONLY snapshot (never the memory, unlike the qcow2 path below when the VM
            # is running), documented on the frontend/API side rather than silently
            # different.
            task_id = create_task("create_snapshot", payload.name, node=conn.getHostname(), username=user["username"])
            threading.Thread(
                target=_create_zvol_snapshot_job,
                args=(task_id, user["username"], zvol_specs, payload.name),
                daemon=True,
            ).start()
            return {"task_id": task_id, "nom": payload.name, "statut": "en_cours"}

        try:
            domain.snapshotLookupByName(payload.name)
            log_action(user["username"], "create_snapshot", payload.name, "echec", "Snapshot already exists")
            raise HTTPException(status_code=422, detail=f"A snapshot '{payload.name}' already exists for this VM")
        except libvirt.libvirtError:
            logger.debug("Ignored exception in create_snapshot()", exc_info=True)

        desc_xml = f"<description>{escape(payload.description)}</description>" if payload.description else ""
        snap_xml = f"""
        <domainsnapshot>
          <name>{escape(payload.name)}</name>
          {desc_xml}
        </domainsnapshot>
        """
        # flags=0: internal, memory included automatically if the VM is running, disk
        # only if it is stopped. See the design note above _snapshot_summary for why no
        # other option is offered.
        task_id = create_task("create_snapshot", payload.name, node=conn.getHostname(), username=user["username"])
        threading.Thread(
            target=_create_snapshot_job,
            args=(task_id, user["username"], name, payload.name, snap_xml),
            daemon=True,
        ).start()

        return {"task_id": task_id, "nom": payload.name, "statut": "en_cours"}
    finally:
        conn.close()


def _restore_snapshot_job(task_id, username, vm_name, snapshot_name):
    conn = open_conn()
    try:
        domain = conn.lookupByName(vm_name)
        snap = domain.snapshotLookupByName(snapshot_name)
        domain.revertToSnapshot(snap, 0)
        finish_task(task_id, "termine")
        log_action(username, "restore_snapshot", snapshot_name, "succes")
    except libvirt.libvirtError as e:
        msg = describe_exception(e)
        finish_task(task_id, "echec", msg)
        log_action(username, "restore_snapshot", snapshot_name, "echec", msg)
    finally:
        conn.close()


def _restore_zvol_snapshot_job(task_id, username, zvol_specs, snapshot_name):
    try:
        zfs_storage.rollback_zvols(zvol_specs, snapshot_name)
        finish_task(task_id, "termine")
        log_action(username, "restore_snapshot", snapshot_name, "succes")
    except zfs_storage.ZfsError as e:
        finish_task(task_id, "echec", e.message)
        log_action(username, "restore_snapshot", snapshot_name, "echec", e.message)


@router.post("/{name}/snapshots/{snapshot_name}/restore", status_code=202)
def restore_snapshot(
    name: str, snapshot_name: str, confirm: bool = False, user: dict = Depends(require_vm_privilege("vm.snapshot"))
):
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "restore_snapshot", name, "echec", "VM not found")
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        zvol_specs = _zvol_disks_of_domain(domain)
        if zvol_specs:
            # SAFETY: unlike the qcow2 internal snapshot (libvirt itself handles the "running
            # VM" case for revertToSnapshot), a `zfs rollback` on a zvol actively opened by
            # the qemu process of a RUNNING VM would desynchronize the guest kernel's cache
            # from the real state of the disk, which means near-certain corruption. The VM
            # MUST be stopped before a ZFS rollback, checked explicitly rather than letting it
            # fail (or worse, silently succeed) in a dangerous way.
            if domain.isActive():
                log_action(
                    user["username"],
                    "restore_snapshot",
                    snapshot_name,
                    "echec",
                    "VM running (it must be stopped for a ZFS rollback)",
                )
                raise HTTPException(
                    status_code=409,
                    detail="The VM must be stopped before restoring a ZFS snapshot (unlike the qcow2 snapshot, a ZFS rollback cannot be done while running)",
                )
            if not confirm:
                log_action(user["username"], "restore_snapshot", snapshot_name, "echec", "Confirmation manquante")
                raise HTTPException(
                    status_code=400, detail="Irreversible action: add ?confirm=true to confirm the restore"
                )
            task_id = create_task("restore_snapshot", snapshot_name, node=conn.getHostname(), username=user["username"])
            threading.Thread(
                target=_restore_zvol_snapshot_job,
                args=(task_id, user["username"], zvol_specs, snapshot_name),
                daemon=True,
            ).start()
            return {
                "task_id": task_id,
                "statut": "en_cours",
                "message": f"Restore of '{name}' to '{snapshot_name}' in progress",
            }

        try:
            domain.snapshotLookupByName(snapshot_name)
        except libvirt.libvirtError:
            log_action(user["username"], "restore_snapshot", snapshot_name, "echec", "Snapshot not found")
            raise HTTPException(status_code=404, detail=f"Snapshot '{snapshot_name}' not found") from None

        if not confirm:
            log_action(user["username"], "restore_snapshot", snapshot_name, "echec", "Confirmation manquante")
            raise HTTPException(status_code=400, detail="Irreversible action: add ?confirm=true to confirm the restore")

        task_id = create_task("restore_snapshot", snapshot_name, node=conn.getHostname(), username=user["username"])
        threading.Thread(
            target=_restore_snapshot_job,
            args=(task_id, user["username"], name, snapshot_name),
            daemon=True,
        ).start()

        return {
            "task_id": task_id,
            "statut": "en_cours",
            "message": f"Restore of '{name}' to '{snapshot_name}' in progress",
        }
    finally:
        conn.close()


@router.delete("/{name}/snapshots/{snapshot_name}")
def delete_snapshot(name: str, snapshot_name: str, user: dict = Depends(require_vm_privilege("vm.snapshot"))):
    # It stays synchronous (no background thread/task): unlike create/restore,
    # deleting an internal snapshot is nearly instantaneous even with a child (libvirt
    # reparents the child automatically), tested and confirmed on this host. `zfs
    # destroy` of a snapshot is of the same order of magnitude (nearly instantaneous),
    # hence the same choice for ZFS VMs.
    conn = open_conn()
    task_id = create_task("delete_snapshot", snapshot_name, node=conn.getHostname(), username=user["username"])
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            log_action(user["username"], "delete_snapshot", name, "echec", "VM not found", task_id=task_id)
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None

        zvol_specs = _zvol_disks_of_domain(domain)
        if zvol_specs:
            try:
                zfs_storage.delete_zvol_snapshot(zvol_specs, snapshot_name)
            except zfs_storage.ZfsError as e:
                log_action(user["username"], "delete_snapshot", snapshot_name, "echec", e.message, task_id=task_id)
                raise HTTPException(status_code=500, detail=f"Deletion error: {e.message}") from e
            log_action(user["username"], "delete_snapshot", snapshot_name, "succes", task_id=task_id)
            return {"message": f"Snapshot '{snapshot_name}' deleted"}

        try:
            snap = domain.snapshotLookupByName(snapshot_name)
        except libvirt.libvirtError:
            log_action(
                user["username"], "delete_snapshot", snapshot_name, "echec", "Snapshot not found", task_id=task_id
            )
            raise HTTPException(status_code=404, detail=f"Snapshot '{snapshot_name}' not found") from None

        try:
            snap.delete(0)
        except libvirt.libvirtError as e:
            msg = describe_exception(e)
            log_action(user["username"], "delete_snapshot", snapshot_name, "echec", msg, task_id=task_id)
            raise HTTPException(status_code=500, detail=f"Deletion error: {msg}") from e

        log_action(user["username"], "delete_snapshot", snapshot_name, "succes", task_id=task_id)
        return {"message": f"Snapshot '{snapshot_name}' deleted"}
    finally:
        conn.close()
