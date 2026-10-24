import logging
import threading
from pathlib import Path

from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse

from app.core import cloudinit_edit, maintenance, privileged, vm_locks
from app.core import permissions as perm
from app.core.audit import log_action
from app.core.libvirt_utils import (
    open_conn,
)
from app.core.safe_paths import safe_child
from app.core.security import get_current_user
from app.core.tasks import answer_later, create_task
from app.core.vm_create import PLACEHOLDER_MAC, DiskSpec, VMCreate  # noqa: F401  (the route's models)
from app.core.vm_meta import (
    set_vm_auto_cleanup,
    set_vm_os_label,
    set_vm_ssh_user,
)
from app.daemon.registry import DaemonError
from app.routers.vms._shared import _domain_summary, router

logger = logging.getLogger(__name__)


def _pending_remote_isos(payload):
    """(name, node) of the images the VM needs that are not in this host's library yet."""
    from app.core.iso_share import isos_dir

    pending = []
    for name, node in ((payload.iso, payload.iso_node), (payload.drivers_iso, payload.drivers_iso_node)):
        if name and node and node != "local" and not safe_child(isos_dir(), Path(name).name).is_file():
            pending.append((name, node))
    return pending


def refuse_unallowed(payload, user):
    """403 unless `user` may create this VM: vm.allocate on its pool, storage.allocate on the storage pool of its
    disks, network.assign on its network (app/core/permissions.py; an administrator may always). 404 for a pool that
    does not exist."""
    if payload.resource_pool is not None and payload.resource_pool not in {p["id"] for p in perm.list_pools()}:
        raise HTTPException(status_code=404, detail=f"Pool {payload.resource_pool} not found")
    refusal = perm.allocation_refusal(
        user, payload.resource_pool, [payload.storage_pool or "default"], [payload.network]
    )
    if refusal:
        raise HTTPException(status_code=403, detail=refusal)


@router.post("", status_code=201)
def create_vm(payload: VMCreate, user: dict = Depends(get_current_user), wait: bool = True):
    """wait=false: every check is made, then the creation runs as a task and the answer is 202 with its id."""
    refuse_unallowed(payload, user)
    maintenance.refuse_if_in_maintenance("local", "VM creation")
    pending = _pending_remote_isos(payload)
    if pending:
        return _create_vm_after_copy(payload, user, pending)
    # Two creations under the same name would write the same disk files, as two clones would.
    claim = vm_locks.claim_or_409(payload.name, "a creation")
    if wait:
        with claim:
            return _create_vm(payload, user)
    try:
        _create_vm(payload, user, check_only=True)
        task_id = create_task("create_vm", payload.name, node=None, username=user["username"])
    except BaseException:
        claim.release()
        raise
    work = vm_locks.released_after(claim, lambda: _create_vm(payload, user, task_id=task_id))
    return answer_later(task_id, work, name=payload.name)


def _create_vm_after_copy(payload, user, pending):
    """Copy the images the VM needs from other nodes, then create it, in the background (202).

    Everything that can be checked without the images is checked first, so a multi-gigabyte copy never ends in
    a refusal the form could have shown. The copies are ordinary copy_iso tasks, and the creation its usual
    create_vm task: both show in Tasks, and both carry on if the browser is closed."""
    from app.core.iso_share import LOCAL, iso_size, resolve_node, start_copy, valid_iso_name

    _create_vm(payload, user, pending={name for name, _ in pending}, check_only=True)
    for name, node in pending:
        if not valid_iso_name(name):
            raise HTTPException(status_code=422, detail=f"Invalid ISO name '{name}'")
        try:
            source = resolve_node(node)
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        if iso_size(source, name) is None:
            raise HTTPException(status_code=422, detail=f"ISO '{name}' not found on {node}")

    remaining = {name for name, _ in pending}
    lock = threading.Lock()
    failed = []

    def after(name, ok):
        with lock:
            remaining.discard(name)
            if not ok:
                failed.append(name)
            last = not remaining
        if not last:
            return
        if failed:
            log_action(user["username"], "create_vm", payload.name, "failed", f"ISO copy failed: {', '.join(failed)}")
            return
        try:
            _create_vm(payload, user)
        except HTTPException as e:  # already recorded on the create_vm task by _create_vm
            logger.warning("VM '%s' not created after the ISO copy: %s", payload.name, e.detail)
        except Exception:
            logger.exception("VM '%s' not created after the ISO copy", payload.name)

    tasks = []
    for name, node in pending:
        try:
            tasks += start_copy(name, node, [LOCAL], user["username"], after=lambda ok, name=name: after(name, ok))
        except FileExistsError as e:
            after(name, False)  # a copy already started for another image must not wait for this one forever
            raise HTTPException(status_code=409, detail=f"{e}. Wait for it to finish, then create the VM again.") from e
        except (LookupError, ValueError) as e:
            after(name, False)
            raise HTTPException(status_code=422, detail=str(e)) from e
    return JSONResponse(status_code=202, content={"name": payload.name, "copy_iso": tasks})


# A creation is one call to hyperlite-daemon that waits: copying an imported disk or the system image takes minutes.
CREATE_TIMEOUT_S = 6 * 3600


def _create_vm(payload, user, pending=frozenset(), check_only=False, task_id=None):
    """pending: image names that are not here yet but are being brought (they pass the existence check).
    check_only: run every check, create nothing (no task either), return None.
    task_id: the task already made for this creation (wait=false); one is made otherwise.

    Checked and made by hyperlite-daemon (app/core/vm_create.py): the checks read the libraries and the pools, the
    disks are written as root. What the VM is recorded with stays here."""
    if not check_only and task_id is None:
        task_id = create_task("create_vm", payload.name, node=None, username=user["username"])
    try:
        made = privileged.run(
            "vm.create",
            user=user["username"],
            timeout=CREATE_TIMEOUT_S,
            spec=payload.model_dump(),
            pending=sorted(pending),
            check_only=check_only,
        )
    except DaemonError as e:
        if not check_only:
            reason = "; ".join(e.detail) if isinstance(e.detail, list) else e.detail
            log_action(user["username"], "create_vm", payload.name, "failed", reason, task_id=task_id)
        raise privileged.http_error(e) from None
    if check_only:
        return None
    if made["customised"]:
        cloudinit_edit.save_state(
            payload.name, payload.username, made["customised"]["keys"], made["customised"]["custom"]
        )
    if made["needs_account"]:
        set_vm_ssh_user(payload.name, payload.username)
    # "Declared" OS label (see vm_meta.py::set_vm_os_label): deduced from the name of
    # the mounted ISO, or "Debian 12" for the default cloud-init path (no ISO,
    # preinstalled image). It is not a really "detected" OS (no qemu-guest-agent is
    # installed in guest VMs), but it is reliable: Hyperlite itself asked for that OS.
    os_label = (
        f"Imported ({Path(payload.import_disk).stem})"
        if made["import_mode"]
        else Path(payload.iso).stem
        if made["install_mode"]
        else "Debian 12"
    )
    set_vm_os_label(payload.name, os_label)
    if payload.auto_cleanup_days:
        set_vm_auto_cleanup(payload.name, payload.auto_cleanup_days)
    if payload.resource_pool is not None:
        perm.add_pool_member(payload.resource_pool, payload.name)
    log_action(user["username"], "create_vm", payload.name, "success", task_id=task_id)
    conn = open_conn()
    try:
        return _domain_summary(conn.lookupByName(payload.name))
    finally:
        conn.close()
