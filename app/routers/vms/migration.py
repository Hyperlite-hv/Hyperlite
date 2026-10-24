import logging
import threading

import libvirt
from fastapi import Depends, HTTPException
from pydantic import BaseModel

from app.core import (
    checkpoints,
    cluster_compat,
    ha,
    iscsi,
    maintenance,
    object_meta,
    privileged,
    self_node,
    vm_boot,
    vm_locks,
)
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import (
    open_conn,
)
from app.core.security import require_role
from app.core.tasks import create_task, finish_task, register_cancel, task_log, update_task_progress
from app.daemon.registry import DaemonError
from app.routers.vms._shared import router

logger = logging.getLogger(__name__)


class MigrateRequest(BaseModel):
    target_node: str
    # Compatibility checks: a heuristic can be wrong, so the admin has the last word.
    # This only disables the REFUSAL, and the checks are still logged.
    skip_checks: bool = False


def _norm_node(n):
    """None/'local' = the local host."""
    return None if n in (None, "", "local") else n


# _pool_target_path/_domain_disk_paths/_uses_shared_storage moved to
# app/core/libvirt_utils.py: shared with the shared-storage detection needed for
# HA protection. See pool_type_and_target_path/domain_disk_paths/uses_shared_storage.


# A migration is one call to hyperlite-daemon that waits: a disk copied along takes hours.
MIGRATE_TIMEOUT_S = 24 * 3600


def _migrate_vm_job(task_id, username, source_node, target_node, vm_name):
    """Migrated by hyperlite-daemon (app/core/vm_migrate.py), which writes the progress into the task; what follows
    the VM in Hyperlite's records is written here once it runs on target_node. A failure closes the task."""
    source = _norm_node(source_node)
    register_cancel(task_id, lambda: privileged.run("vm.migrate_cancel", user=username, task_id=task_id))
    try:
        moved = privileged.run(
            "vm.migrate",
            user=username,
            timeout=MIGRATE_TIMEOUT_S,
            vm=vm_name,
            source_node=source,
            target_node=target_node,
            task_id=task_id,
        )
    except DaemonError as e:
        msg = e.detail if isinstance(e.detail, str) else str(e.detail)
        finish_task(task_id, "failed", msg)
        log_action(username, "migrate_vm", vm_name, "failed", msg)
        return
    if moved["checkpoints_dropped"]:
        checkpoints.forget(vm_name, moved["checkpoints_dropped"])
    # What says where the VM is, HA above all: a protection left on the node the VM just left would let a recovery
    # start a second copy of it there. Each step that fails is a warning on the task, never the end of the others.
    warnings = list(moved["warnings"])

    def after(what, step):
        try:
            step()
        except Exception as e:  # one failed step never stops the next ones
            logger.exception("After migrating %s to %s: %s failed", vm_name, target_node, what)
            warnings.append(f"{what}: {describe_exception(e)}")
            task_log(task_id, f"Warning: {what} failed: {describe_exception(e)}")

    def follow_ha():
        if ha.follow_migration(vm_name, target_node):
            log_action(username, "ha_follow", vm_name, "success", f"HA protection follows the VM to {target_node}")

    before = len(warnings)
    after("moving its HA protection", follow_ha)
    if len(warnings) > before:
        log_action(
            username, "ha_follow", vm_name, "failed",
            f"The VM runs on {target_node} but its HA protection still names {source_node or self_node.name()}: "
            "disable and enable it again in the HA tab",
        )  # fmt: skip
    after("moving its tags and notes", lambda: object_meta.follow_migration(vm_name, source_node, target_node))

    def follow_boot():
        if vm_boot.follow_migration(vm_name, source_node, target_node):
            log_action(
                username, "autostart_follow", vm_name, "success", f"Start at boot follows the VM to {target_node}"
            )

    after("moving its start at boot", follow_boot)
    # Then what the move left on the source, by the daemon, which kept it (its addresses, its copied disks).
    try:
        warnings += privileged.run("vm.migrate_finish", user=username, timeout=MIGRATE_TIMEOUT_S, task_id=task_id)[
            "warnings"
        ]
    except DaemonError as e:
        warnings.append(f"cleaning up the source node: {e.detail}")
    update_task_progress(task_id, 100)
    finish_task(task_id, "succeeded", "; ".join(warnings) or None)
    storage = "shared" if moved["shared"] else "copied"
    log_action(
        username,
        "migrate_vm",
        vm_name,
        "success",
        # The local node has no name of its own in source_node (None): the audit log said "None -> hlc2".
        f"{source_node or self_node.name()} -> {target_node} ({storage} storage)"
        + (f", with warnings: {'; '.join(warnings)}" if warnings else ""),
    )


@router.get("/{name}/migration-check")
def migration_check(name: str, target_node: str, node: str | None = None, user: dict = Depends(require_role("admin"))):
    """Compatibility diagnostic BEFORE a migration: read-only, it migrates nothing. See
    app/core/cluster_compat.py."""
    if _norm_node(target_node) == _norm_node(node):
        raise HTTPException(status_code=422, detail="The destination node must be different from the source node")
    src_conn = open_conn(_norm_node(node), readonly=True)
    try:
        try:
            domain = src_conn.lookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        dst_conn = open_conn(_norm_node(target_node), readonly=True)
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
    maintenance.refuse_if_in_maintenance(payload.target_node, "Migration")
    # Migration from a remote node to the local host was long blocked here, because
    # peer-to-peer migration is initiated by the SOURCE libvirtd, which needs to be
    # able to connect ITSELF to the local host. It is now possible through a dedicated
    # reverse SSH trust, established automatically when each node is registered (see
    # cluster.py::ensure_reverse_trust, a key SPECIFIC to that node, never shared). If
    # that trust could not be established (a node registered before this existed, or a
    # best-effort failure at the time), the migration fails with a clear SSH error on
    # the task side rather than being refused a priori here.

    src_conn = open_conn(node, readonly=True)
    try:
        try:
            domain = src_conn.lookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        iscsi.refuse_if_iscsi(domain, "Live migration")
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
        dest_conn = open_conn(_norm_node(payload.target_node), readonly=True)
        try:
            compat = cluster_compat.report(cluster_compat.check_vm_migration(src_conn, dest_conn, domain))
            if compat["summary"]["blocking"] and not payload.skip_checks:
                blocages = [c["message"] for c in compat["checks"] if c["status"] == "blocking"]
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
        claim = vm_locks.claim_or_409(name, "a migration", node=source_node)
        try:
            task_id = create_task("migrate_vm", name, node=source_node, username=user["username"])
            threading.Thread(
                target=vm_locks.released_after(claim, _migrate_vm_job),
                args=(task_id, user["username"], node, payload.target_node, name),
                daemon=True,
            ).start()
        except BaseException:
            claim.release()
            raise
        return {"task_id": task_id, "status": "running"}
    finally:
        src_conn.close()
