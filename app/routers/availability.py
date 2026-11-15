"""Availability of VMs and nodes over time (app/core/availability.py, docs/design/console-2.md section 1)."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core import availability, permissions
from app.core.listing import listing
from app.core.security import get_current_user

router = APIRouter(prefix="/availability", tags=["availability"])


def _may_read(user, kind, name):
    if kind == "vm":
        return permissions.has_privilege(user, name, "vm.view")
    return permissions.has_area_privilege(user, "node.view") or user["role"] == "viewer"


@router.get("/summary")
def availability_summary(days: int = Query(7, ge=1, le=90), user: dict = Depends(get_current_user)):
    """Every VM and node this node watches: its state now and its share available over `days`. A VM the account may
    not read is left out."""
    from app.core import self_node

    rows = []
    for r in availability.summary(days):
        target = r["target"]
        if target == "host":
            kind, name, node = "node", self_node.name(), self_node.name()
        elif target.startswith("node:"):
            kind, name, node = "node", target[5:], target[5:]
        elif ":" in target:
            node, _, name = target.partition(":")
            kind = "vm"
        else:
            kind, name, node = "vm", target, self_node.name()
        if _may_read(user, kind, name):
            rows.append({**r, "kind": kind, "name": name, "node": node})
    return listing(rows)


@router.get("/{kind}/{name}")
def availability_report(
    kind: str,
    name: str,
    node: str | None = None,
    days: int = Query(7, ge=1, le=90),
    user: dict = Depends(get_current_user),
):
    """The Availability tab of a VM (`node`: its node, omitted or "local" for this one) or a node: its state now, the
    share available over 1, 7 and 30 days, its states over the period and the interruptions with their cause."""
    if kind not in ("vm", "node"):
        raise HTTPException(status_code=404, detail="Availability is kept for VMs and nodes")
    if not _may_read(user, kind, name):
        raise HTTPException(status_code=403, detail="Insufficient rights")
    return availability.report(kind, name, node, days)
