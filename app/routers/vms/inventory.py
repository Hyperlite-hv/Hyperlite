import logging

import libvirt
from fastapi import Depends, HTTPException

from app.core import cluster_view
from app.core.audit import log_action
from app.core.libvirt_utils import (
    open_conn,
)
from app.core.security import get_current_user
from app.routers.vms._shared import _domain_summaries, _domain_summary, router

logger = logging.getLogger(__name__)


# Another member's VMs come from that member, in one call, out of its own inventory cache (docs/scale-results.md): read
# here through libvirt over SSH they cost one round trip per VM, 7 s for 333 VMs over Tailscale. A node registered over
# SSH only (no Hyperlite on it) is still read through libvirt below.
_on_member = cluster_view.forward_to("node")


def _cluster_vms(username):
    """Every member's VMs, each marked with its node ("local" for this one), from the cluster's status (phase E,
    app/core/statd.py): nothing is asked of the other nodes, whatever their number. This node's own VMs are read here,
    current. A member without a status (an older version during a rolling update) is asked as before; one that does
    not answer is named."""
    from concurrent.futures import ThreadPoolExecutor

    from app.core import statd

    conn = open_conn()
    try:
        items = [{**v, "node": "local"} for v in _domain_summaries(conn, None)]
    finally:
        conn.close()
    table = statd.table()
    stale, unreachable, ask = [], [], []
    for member in cluster_view.members():
        status = table.get(member["name"])
        if status is None:
            ask.append(member)
            continue
        items += [{**v, "node": member["name"]} for v in status["vms"]]
        if status["stale"]:
            stale.append(member["name"])

    def asked(member):
        try:
            code, body = cluster_view.forward(member, username, "GET", "/vms")
        except cluster_view.PeerError:
            return member["name"], None
        return member["name"], body if code == 200 and isinstance(body, list) else None

    if ask:
        with ThreadPoolExecutor(max_workers=min(16, len(ask))) as pool:
            for name, vms in pool.map(asked, ask):
                if vms is None:
                    unreachable.append(name)
                else:
                    items += [{**v, "node": name} for v in vms]
    return {"items": items, "stale": stale, "unreachable": unreachable}


@router.get("", dependencies=[Depends(_on_member)])
def list_vms(
    node: str | None = None, cluster: bool = False, user: dict = Depends(get_current_user)
):  # fmt: skip
    if cluster:
        result = _cluster_vms(user["username"])
        log_action(user["username"], "list_vms", "vms", "succes")
        return result
    """node: the name of a registered remote node, to list ITS VMs instead of those
    of the local host. Omitted or None = unchanged behaviour (local host).
    The frontend used to have no way to query a remote node here, so the VMs of
    a registered node never appeared in the main tree (only the dedicated
    "Nodes" tab showed them, through /nodes/{name}/summary): a real bug reported
    when testing with a real second physical node."""
    try:
        conn = open_conn(node)
    except libvirt.libvirtError as e:
        # A node that does not answer is named, and the dashboard still lists the others (no unhandled 500).
        raise HTTPException(status_code=502, detail=f"{node or 'This node'} does not answer: {e}") from None
    try:
        result = _domain_summaries(conn, node)
    except libvirt.libvirtError as e:  # the connection dropped while reading (seen under load: sshd's MaxStartups)
        raise HTTPException(status_code=502, detail=f"{node or 'This node'} does not answer: {e}") from None
    finally:
        conn.close()
    log_action(user["username"], "list_vms", "vms", "succes")
    return result


@router.get("/{name}")
def get_vm(name: str, node: str | None = None, user: dict = Depends(get_current_user)):
    """node: the registered node the VM runs on (the local host by default), as for GET /vms."""
    conn = open_conn(None if node == "local" else node)
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
