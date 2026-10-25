import logging

import libvirt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import (
    cluster_view,
    linked_disks,
    maintenance,
    privileged,
    templates_store,
    vm_locks,
    vm_meta,
    vm_records,
)
from app.core.audit import log_action
from app.core.libvirt_utils import open_conn
from app.core.listing import listing
from app.core.security import get_current_user, require_role
from app.core.tasks import answer_later, create_task
from app.daemon.registry import DaemonError

logger = logging.getLogger(__name__)

# ?node= on every route: the member that holds the object does the work (app/core/cluster_view.py, on_node).
router = APIRouter(prefix="/templates", tags=["templates"], dependencies=[Depends(cluster_view.on_node)])


@router.get("")
def list_templates(user: dict = Depends(get_current_user)):
    """Each template with `linked_clones`, the VMs whose disk reads through the template's disk (null when libvirt
    does not answer: the templates are still listed), and `linkable`, true when its disk is a qcow2 image, which a
    linked clone needs as its backing file."""
    templates = templates_store.list_templates()
    try:
        conn = open_conn(readonly=True)
        try:
            disks = linked_disks.domain_disks(conn)
        finally:
            conn.close()
    except libvirt.libvirtError as e:
        logger.warning("Linked clones not listed, libvirt does not answer: %s", e)
        disks = None
    for tpl in templates:
        base = str(templates_store.disk_path(tpl["name"]))
        tpl["linkable"] = linked_disks.is_qcow2(base)
        tpl["linked_clones"] = (
            None if disks is None else sorted({vm for vm, path in disks if linked_disks.depends_on(path, base)})
        )
    return listing(templates)


# A copy of a template's disk is one call to hyperlite-daemon that waits: it takes minutes.
COPY_TIMEOUT_S = 6 * 3600


def _daemon(op, user, action, target, task_id=None, timeout=None, **args):
    """`op` run by hyperlite-daemon (app/core/vm_templates.py): its result, or its refusal or failure recorded as
    `action` on `target` (on the task when there is one) and answered."""
    try:
        if timeout:
            args["timeout"] = timeout
        return privileged.run(op, user=user["username"], **args)
    except DaemonError as e:
        log_action(user["username"], action, target, "failed", e.detail, task_id=task_id)
        raise privileged.http_error(e) from None


class ConvertRequest(BaseModel):
    template_name: str | None = None
    # True: the template gets a copy of the VM's disk and the VM stays, as vSphere's "Clone to Template". False (the
    # default, as before): the VM becomes the template and leaves the list, as Proxmox VE's "Convert to template".
    keep_vm: bool = False


@router.post("/from-vm/{name}", status_code=201)
def convert_to_template(
    name: str, payload: ConvertRequest, user: dict = Depends(require_role("admin")), wait: bool = True
):
    """wait=false: every check is made, then the work runs as a task and the answer is 202 with its id."""
    claim = vm_locks.claim_or_409(name, "a copy to a template" if payload.keep_vm else "a conversion to a template")
    try:
        _convert_to_template(name, payload, user, check_only=True)
        # A task as for a clone: a copy to a template takes minutes, and shows in Tasks while it runs.
        action = "clone_to_template" if payload.keep_vm else "convert_to_template"
        task_id = create_task(action, name, node=None, username=user["username"])
    except BaseException:
        claim.release()
        raise
    work = vm_locks.released_after(claim, lambda: _convert_to_template(name, payload, user, task_id=task_id))
    if wait:
        return work()
    return answer_later(task_id, work, name=(payload.template_name or name).strip())


def _convert_to_template(name, payload, user, check_only=False, task_id=None):
    """check_only: run every check, change nothing, return None. task_id: the task the outcome is recorded on.

    The account and the system of the VM go with its disk: a VM deployed from the template needs them for the web
    terminal (seen on a real node: an ISO-installed RHEL deployed from its template had no SSH user)."""
    tpl_name = (payload.template_name or name).strip()
    action = "clone_to_template" if payload.keep_vm else "convert_to_template"
    _daemon(
        "template.make",
        user,
        action,
        name,
        task_id=task_id,
        timeout=None if check_only else COPY_TIMEOUT_S,
        vm=name,
        template=tpl_name,
        keep_vm=payload.keep_vm,
        created_by=user["username"],
        ssh_user=vm_meta.get_vm_ssh_user(name),
        os_label=vm_meta.get_vm_os_label(name),
        check_only=check_only,
    )
    if check_only:
        return None
    if payload.keep_vm:
        log_action(user["username"], action, name, "success", f"template -> {tpl_name}, VM kept", task_id=task_id)
        return {"template": tpl_name, "vm_source": name, "vm_kept": True}
    # The VM is gone, as after a deletion: every record of it (rights, pools, backup schedule, which would fail at
    # every run...) goes; the template keeps its SSH user and system.
    vm_records.forget(name)
    log_action(user["username"], action, name, "success", f"template -> {tpl_name}", task_id=task_id)
    return {"template": tpl_name, "vm_source": name, "vm_kept": False}


class DeployRequest(BaseModel):
    new_name: str
    network: str | None = None
    # True: a linked clone, as on Proxmox VE: the VM's disk only stores its own changes over the template's disk
    # (app/core/linked_disks.py). It is made in an instant and stays small, and the template cannot be deleted or
    # renamed while it exists. False (the default, as before): a full copy of the template's disk.
    linked: bool = False


@router.post("/{template_name}/deploy", status_code=201)
def deploy_template(
    template_name: str, payload: DeployRequest, user: dict = Depends(require_role("admin")), wait: bool = True
):
    """wait=false: every check is made, then the copy runs as a task and the answer is 202 with its id."""
    maintenance.refuse_if_in_maintenance("local", "Template deployment")
    # Two deployments under the same name would write the same disk file, as two clones would.
    claim = vm_locks.claim_or_409(payload.new_name, "a deployment")
    try:
        _deploy(template_name, payload, user, check_only=True)
        # A task as for a clone: the copy of a large disk takes minutes, and shows in Tasks while it runs.
        task_id = create_task("deploy_template", template_name, node=None, username=user["username"])
    except BaseException:
        claim.release()
        raise
    work = vm_locks.released_after(claim, lambda: _deploy(template_name, payload, user, task_id=task_id))
    if wait:
        return work()
    return answer_later(task_id, work, name=payload.new_name)


def _deploy(template_name, payload, user, check_only=False, task_id=None):
    made = _daemon(
        "template.deploy",
        user,
        "deploy_template",
        template_name,
        task_id=task_id,
        timeout=None if check_only else COPY_TIMEOUT_S,
        template=template_name,
        new_name=payload.new_name,
        network=payload.network,
        linked=payload.linked,
        check_only=check_only,
    )
    if check_only:
        return None
    # The source VM's account and system, which the copied disk holds (a template made before they were kept has
    # none: set them on the VM).
    tpl = templates_store.get_template(template_name) or {}
    if tpl.get("ssh_user"):
        vm_meta.set_vm_ssh_user(payload.new_name, tpl["ssh_user"])
    if tpl.get("os_label"):
        vm_meta.set_vm_os_label(payload.new_name, tpl["os_label"])
    log_action(
        user["username"],
        "deploy_template",
        template_name,
        "success",
        f"-> {payload.new_name}" + (" (linked clone)" if payload.linked else ""),
        task_id=task_id,
    )
    return {"template": template_name, "vm": made["vm"], "state": "stopped", "linked": payload.linked}


class TemplateRename(BaseModel):
    new_name: str


@router.post("/{template_name}/rename")
def rename_template_endpoint(template_name: str, payload: TemplateRename, user: dict = Depends(require_role("admin"))):
    new = payload.new_name
    _daemon("template.rename", user, "rename_template", template_name, template=template_name, new_name=new)
    log_action(user["username"], "rename_template", template_name, "success", f"-> {new}")
    return templates_store.get_template(new) | {"xml": None}


@router.delete("/{template_name}")
def delete_template_endpoint(template_name: str, confirm: bool = False, user: dict = Depends(require_role("admin"))):
    if templates_store.get_template(template_name) is None:
        raise HTTPException(status_code=404, detail=f"Template '{template_name}' not found")
    if not confirm:
        raise HTTPException(status_code=400, detail="Confirmation required (?confirm=true)")
    _daemon("template.delete", user, "delete_template", template_name, template=template_name)
    log_action(user["username"], "delete_template", template_name, "success")
    return {"template": template_name, "deleted": True}
