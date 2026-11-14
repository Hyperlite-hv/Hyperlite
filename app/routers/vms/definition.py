"""A VM's definition, to make it again elsewhere: JSON for POST /vms, or Terraform / OpenTofu (app/core/vm_definition.py)."""

from typing import Literal

import libvirt
from fastapi import Depends, HTTPException
from fastapi.responses import PlainTextResponse

from app.core import vm_definition
from app.core.libvirt_utils import open_conn
from app.core.security import require_vm_privilege
from app.routers.vms._shared import router


@router.get("/{name}/definition")
def vm_definition_export(
    name: str,
    format: Literal["json", "create", "terraform"] = "json",
    user: dict = Depends(require_vm_privilege("vm.view")),
):
    """format=json: what the VM is made of; create: a body for POST /vms; terraform: a `hyperlite_vm` resource."""
    conn = open_conn(readonly=True)
    try:
        try:
            domain = conn.lookupByName(name)
        except libvirt.libvirtError:
            raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
        found = vm_definition.read(conn, domain)
    finally:
        conn.close()
    if format == "terraform":
        return PlainTextResponse(vm_definition.as_terraform(found), media_type="text/plain")
    return vm_definition.as_create_body(found) if format == "create" else found
