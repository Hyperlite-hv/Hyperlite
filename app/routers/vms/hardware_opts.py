"""Advanced hardware settings of a VM of this host (app/core/vm_hardware_opts.py)."""

import libvirt
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import privileged, vm_locks
from app.core import vm_hardware_opts as opts
from app.core.audit import log_action
from app.core.libvirt_utils import open_conn
from app.core.security import require_vm_privilege
from app.routers.vms._shared import router


def _lookup(conn, name):
    try:
        return conn.lookupByName(name)
    except libvirt.libvirtError:
        raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None


def _apply(user, name, action, detail, op, **fields):
    """A change written by hyperlite-daemon (`op`, app/daemon/ops/vm.py) under the VM's lock: refused values 422,
    libvirt failures 500."""
    with vm_locks.claim_or_409(name, "a hardware change"):
        conn = open_conn()
        try:
            _lookup(conn, name)
            result = privileged.ask(op, user, action, name, vm=name, **fields)
            log_action(user["username"], action, name, "success", detail)
            return {**opts.describe(conn.lookupByName(name)), **(result or {})}
        finally:
            conn.close()


@router.get("/{name}/hardware-options")
def get_hardware_options(name: str, user: dict = Depends(require_vm_privilege("vm.view"))):
    conn = open_conn()
    try:
        domain = _lookup(conn, name)
        described = opts.describe(domain)
        return {
            **described,
            "newer_machines": opts.newer_machines(conn, described["machine"]),
            "running": bool(domain.isActive()),
        }
    finally:
        conn.close()


class DiskOptions(BaseModel):
    cache: str | None = None
    discard: str | None = None
    io: str | None = None
    iothread: bool = False
    iops: int | None = Field(default=None, ge=0, le=opts.MAX_IOPS)
    mbps: int | None = Field(default=None, ge=0, le=opts.MAX_MBPS)


@router.put("/{name}/disks/{target_dev}/options")
def set_disk_options(
    name: str, target_dev: str, payload: DiskOptions, user: dict = Depends(require_vm_privilege("vm.hardware"))
):
    detail = f"{target_dev}: " + ", ".join(
        f"{k}={v}" for k, v in payload.model_dump().items() if v not in (None, False)
    )
    return _apply(
        user,
        name,
        "set_disk_options",
        detail,
        "vm.set_disk_options",
        target=target_dev,
        **payload.model_dump(),
    )


class BootOrder(BaseModel):
    position: list[str] = Field(min_length=1, max_length=32)


@router.put("/{name}/boot-order")
def set_boot_order(name: str, payload: BootOrder, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    return _apply(
        user,
        name,
        "set_boot_order",
        " > ".join(payload.position),
        "vm.set_boot_order",
        position=payload.position,
    )


class Balloon(BaseModel):
    active: bool
    minimum_mb: int | None = Field(default=None, ge=256)


@router.put("/{name}/balloon")
def set_balloon(name: str, payload: Balloon, user: dict = Depends(require_vm_privilege("vm.resize"))):
    detail = f"on, minimum {payload.minimum_mb} MB" if payload.active else "off"
    return _apply(
        user, name, "set_balloon", detail, "vm.set_balloon", active=payload.active, minimum_mb=payload.minimum_mb
    )


class Machine(BaseModel):
    machine: str = Field(min_length=1, max_length=64)


@router.put("/{name}/machine")
def set_machine(name: str, payload: Machine, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    return _apply(user, name, "set_machine", payload.machine, "vm.set_machine", machine=payload.machine)
