"""Advanced hardware settings of a VM of this host (app/core/vm_hardware_opts.py)."""

import libvirt
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import vm_hardware_opts as opts
from app.core import vm_locks
from app.core.audit import log_action
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import open_conn
from app.core.security import require_vm_privilege
from app.routers.vms._shared import router


def _lookup(conn, name):
    try:
        return conn.lookupByName(name)
    except libvirt.libvirtError:
        raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None


def _apply(user, name, action, detail, change):
    """Run a change under the VM's lock, report refused values as 422 and libvirt failures as 500."""
    with vm_locks.claim_or_409(name, "a hardware change"):
        conn = open_conn()
        try:
            domain = _lookup(conn, name)
            try:
                result = change(conn, domain)
            except opts.OptionError as e:
                log_action(user["username"], action, name, "echec", str(e))
                raise HTTPException(status_code=422, detail=str(e)) from e
            except libvirt.libvirtError as e:
                msg = describe_exception(e)
                log_action(user["username"], action, name, "echec", msg)
                raise HTTPException(status_code=500, detail=msg) from e
            log_action(user["username"], action, name, "succes", detail)
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
            "machines_plus_recentes": opts.newer_machines(conn, described["machine"]),
            "en_marche": bool(domain.isActive()),
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
        lambda conn, d: opts.set_disk_options(conn, d, target_dev, payload.model_dump()),
    )


class BootOrder(BaseModel):
    ordre: list[str] = Field(min_length=1, max_length=32)


@router.put("/{name}/boot-order")
def set_boot_order(name: str, payload: BootOrder, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    return _apply(
        user,
        name,
        "set_boot_order",
        " > ".join(payload.ordre),
        lambda conn, d: opts.set_boot_order(conn, d, payload.ordre),
    )


class Balloon(BaseModel):
    actif: bool
    minimum_mo: int | None = Field(default=None, ge=256)


@router.put("/{name}/balloon")
def set_balloon(name: str, payload: Balloon, user: dict = Depends(require_vm_privilege("vm.resize"))):
    detail = f"on, minimum {payload.minimum_mo} MB" if payload.actif else "off"
    return _apply(
        user, name, "set_balloon", detail, lambda conn, d: opts.set_balloon(conn, d, payload.actif, payload.minimum_mo)
    )


class Machine(BaseModel):
    machine: str = Field(min_length=1, max_length=64)


@router.put("/{name}/machine")
def set_machine(name: str, payload: Machine, user: dict = Depends(require_vm_privilege("vm.hardware"))):
    return _apply(
        user, name, "set_machine", payload.machine, lambda conn, d: opts.set_machine(conn, d, payload.machine)
    )
