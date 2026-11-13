"""A VM's schedules (app/core/vm_schedules.py): power actions need vm.power on the VM, snapshots vm.snapshot."""

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import vm_schedules
from app.core.audit import log_action
from app.core.permissions import has_privilege
from app.core.security import get_current_user, require_vm_privilege
from app.routers.vms._shared import router


class VmSchedule(BaseModel):
    action: str
    days: list[int] = Field(min_length=1, max_length=7)
    time: str
    keep: int = 7
    enabled: bool = True


def _allowed(user, name, action):
    privilege = "vm.snapshot" if action == "snapshot" else "vm.power"
    if not has_privilege(user, name, privilege):
        raise HTTPException(
            status_code=403, detail=f"Insufficient rights on VM '{name}' (required privilege: {privilege})"
        )


@router.get("/{name}/schedules")
def list_vm_schedules(name: str, user: dict = Depends(require_vm_privilege("vm.view"))):
    return {"items": vm_schedules.schedules(name)}


@router.post("/{name}/schedules", status_code=201)
def create_vm_schedule(name: str, payload: VmSchedule, user: dict = Depends(get_current_user)):
    _allowed(user, name, payload.action)
    try:
        s = vm_schedules.create({**payload.model_dump(), "vm_name": name}, user["username"])
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    log_action(user["username"], "create_vm_schedule", name, "success", f"{s['action']} at {s['time']} on {s['days']}")
    return s


def _own(name, schedule_id):
    s = vm_schedules.schedule(schedule_id)
    if not s or s["vm_name"] != name:
        raise HTTPException(status_code=404, detail="No such schedule on this VM")
    return s


@router.put("/{name}/schedules/{schedule_id}")
def update_vm_schedule(name: str, schedule_id: str, payload: VmSchedule, user: dict = Depends(get_current_user)):
    current = _own(name, schedule_id)
    _allowed(user, name, current["action"])
    _allowed(user, name, payload.action)
    try:
        s = vm_schedules.update(schedule_id, payload.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    log_action(user["username"], "update_vm_schedule", name, "success", f"{s['action']} at {s['time']} on {s['days']}")
    return s


@router.delete("/{name}/schedules/{schedule_id}", status_code=204)
def delete_vm_schedule(name: str, schedule_id: str, user: dict = Depends(get_current_user)):
    current = _own(name, schedule_id)
    _allowed(user, name, current["action"])
    vm_schedules.delete(schedule_id)
    log_action(user["username"], "delete_vm_schedule", name, "success", f"{current['action']} at {current['time']}")
