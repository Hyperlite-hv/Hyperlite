import logging

import libvirt
from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse

from app.core import cluster_view
from app.core.audit import log_action
from app.core.libvirt_utils import (
    open_conn,
)
from app.core.listing import listing
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

    from app.core import node_health, self_node, statd

    table = statd.table()
    # This node's own VMs from its status too, as Proxmox does (Antho, 2026-10-18): a change made through Hyperlite
    # is sent again within a second (statd.changed), one made outside it within statd.PERIOD_S. Read live when there
    # is no current status (outside a cluster, hyperlite-cfs not answering).
    own = table.get(self_node.name())
    if own and not own["stale"]:
        items = [{**v, "node": "local"} for v in own["vms"]]
    else:
        conn = open_conn()
        try:
            items = [{**v, "node": "local"} for v in _domain_summaries(conn, None)]
        finally:
            conn.close()
    stale, unreachable, ask = [], [], []
    members = cluster_view.members()
    current = {name for name, s in table.items() if not s["stale"]}
    seen_down = node_health.down(members, current)
    for member in members:
        status = table.get(member["name"])
        if status is None:
            # No status to read: asked live, unless the cluster sees it down (app/core/node_health.py).
            if member["name"] in seen_down:
                unreachable.append(member["name"])
            else:
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
        if code != 200:
            return member["name"], None
        vms = body.get("items") if isinstance(body, dict) else body  # a member's list (v1: {items, total})
        return member["name"], vms if isinstance(vms, list) else None

    if ask:
        with ThreadPoolExecutor(max_workers=min(16, len(ask))) as pool:
            for name, vms in pool.map(asked, ask):
                if vms is None:
                    unreachable.append(name)
                else:
                    items += [{**v, "node": name} for v in vms]
    return listing(items, stale=stale, unreachable=unreachable)


@router.get("", dependencies=[Depends(_on_member)])
def list_vms(node: str | None = None, cluster: bool = False, user: dict = Depends(get_current_user)):
    """node: the name of a registered remote node, to list ITS VMs instead of those
    of the local host. Omitted or None = unchanged behaviour (local host).
    The frontend used to have no way to query a remote node here, so the VMs of
    a registered node never appeared in the main tree (only the dedicated
    "Nodes" tab showed them, through /nodes/{name}/summary): a real bug reported
    when testing with a real second physical node.
    cluster=true: every member's VMs from the cluster's status (_cluster_vms, phase E)."""
    if cluster:
        result = _cluster_vms(user["username"])
        log_action(user["username"], "list_vms", "vms", "success")
        # Already JSON-ready (plain dicts from the summaries): FastAPI's generic encoder walked a thousand of them on
        # every request, the largest cost of this list under load (docs/scale-results.md).
        return JSONResponse(result)
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
    log_action(user["username"], "list_vms", "vms", "success")
    return listing(result)


@router.get("/{name}")
def get_vm(name: str, node: str | None = None, user: dict = Depends(get_current_user)):
    """node: the registered node the VM runs on (the local host by default), as for GET /vms."""
    conn = open_conn(None if node == "local" else node)
    try:
        domain = conn.lookupByName(name)
    except libvirt.libvirtError:
        log_action(user["username"], "get_vm", name, "failed", "VM not found")
        conn.close()
        raise HTTPException(status_code=404, detail=f"VM '{name}' not found") from None
    result = _domain_summary(domain)
    conn.close()
    log_action(user["username"], "get_vm", name, "success")
    return result
