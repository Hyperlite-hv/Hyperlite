"""Cloning a VM of this node (POST /vms/{name}/clone): the checks and the copy are hyperlite-daemon's
(app/core/vm_clone.py); the permissions, the locks, the task and the records are the API's."""

from fastapi import Depends
from pydantic import BaseModel

from app.core import maintenance, privileged, vm_locks
from app.core import permissions as perm
from app.core.audit import log_action
from app.core.security import require_vm_privilege
from app.core.tasks import answer_later, create_task
from app.core.vm_meta import (
    rename_vm_os_label,
    rename_vm_ssh_user,
)
from app.daemon.registry import DaemonError
from app.routers.vms._shared import router


class CloneRequest(BaseModel):
    new_name: str


@router.post("/{name}/clone", status_code=201)
def clone_vm(
    name: str, payload: CloneRequest, user: dict = Depends(require_vm_privilege("vm.clone")), wait: bool = True
):
    """wait=false: every check is made, then the copy runs as a task and the answer is 202 with its id."""
    # A clone's disks are copied into the images directory, the "default" storage pool: space taken there, as Proxmox VE
    # checks Datastore.AllocateSpace on a clone's target storage.
    perm.refuse_unless(
        user, "storage", "default", "storage.allocate", "storage pool 'default' (where a clone is copied)"
    )
    maintenance.refuse_if_in_maintenance("local", "Cloning")
    # The source's disks are read: no restore, move or deletion may change them meanwhile.
    source = vm_locks.claim_or_409(name, "a clone")
    try:
        # Two clones to the same new name would write the same disk files (the same name is refused by the checks).
        target = None if payload.new_name == name else vm_locks.claim_or_409(payload.new_name, "a clone")
    except BaseException:
        source.release()
        raise

    def release():
        if target is not None:
            target.release()
        source.release()

    if wait:
        try:
            return _clone_vm(name, payload, user)
        finally:
            release()
    try:
        _clone_vm(name, payload, user, check_only=True)
        task_id = create_task("clone_vm", name, node=None, username=user["username"])
    except BaseException:
        release()
        raise

    def work():
        try:
            _clone_vm(name, payload, user, task_id)
        finally:
            release()

    return answer_later(task_id, work, name=payload.new_name)


# A clone is one call to hyperlite-daemon that waits: copying the disks takes minutes.
CLONE_TIMEOUT_S = 6 * 3600


def _clone_vm(name, payload, user, task_id=None, check_only=False):
    """Checked and copied by hyperlite-daemon (app/core/vm_clone.py). check_only: the checks alone, no task, None.
    What the clone is recorded with stays here.

    Cloning is gated by its own privilege, vm.clone (app/core/permissions.py), which no predefined role holds: an
    administrator delegates it in a custom role. A viewer once cloned through a route that only asked for a login."""
    if not check_only and task_id is None:
        task_id = create_task("clone_vm", name, node=None, username=user["username"])
    try:
        made = privileged.run(
            "vm.clone",
            user=user["username"],
            timeout=CLONE_TIMEOUT_S,
            vm=name,
            new_name=payload.new_name,
            check_only=check_only,
        )
    except DaemonError as e:
        if not check_only:
            log_action(user["username"], "clone_vm", name, "failed", e.detail, task_id=task_id)
        raise privileged.http_error(e) from None
    if check_only:
        return None
    rename_vm_ssh_user(name, payload.new_name)
    rename_vm_os_label(name, payload.new_name)
    log_action(user["username"], "clone_vm", name, "success", f"clone -> {payload.new_name}", task_id=task_id)
    return {
        "source": name,
        "clone": made["clone"],
        "state": "stopped",
        "guest_customization": made["guest_customization"],
    }
