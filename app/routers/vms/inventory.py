import logging

import libvirt
from fastapi import Depends, HTTPException

from app.core.audit import log_action
from app.core.libvirt_utils import (
    open_conn,
)
from app.core.security import get_current_user
from app.routers.vms._shared import _domain_summary, router

logger = logging.getLogger(__name__)


@router.get("")
def list_vms(node: str | None = None, user: dict = Depends(get_current_user)):
    """node: the name of a registered remote node, to list ITS VMs instead of those
    of the local host. Omitted or None = unchanged behaviour (local host).
    The frontend used to have no way to query a remote node here, so the VMs of
    a registered node never appeared in the main tree (only the dedicated
    "Nodes" tab showed them, through /nodes/{name}/summary): a real bug reported
    when testing with a real second physical node."""
    conn = open_conn(node)
    try:
        domains = conn.listAllDomains()
        result = [_domain_summary(d) for d in domains]
        log_action(user["username"], "list_vms", "vms", "succes")
        return result
    finally:
        conn.close()


@router.get("/{name}")
def get_vm(name: str, user: dict = Depends(get_current_user)):
    conn = open_conn()
    try:
        domain = conn.lookupByName(name)
    except libvirt.libvirtError:
        log_action(user["username"], "get_vm", name, "echec", "VM not found")
        conn.close()
        raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
    result = _domain_summary(domain)
    conn.close()
    log_action(user["username"], "get_vm", name, "succes")
    return result
