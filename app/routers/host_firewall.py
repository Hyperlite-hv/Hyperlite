"""The nodes' own firewall, for the cluster and for each node, with security groups and IP sets
(docs/design/host-firewall.md). A change is applied on the node it is sent to (`?node=` sends it to that member),
must be confirmed from the browser before its delay ends, and is saved for the whole cluster only then: every other
member applies it within seconds. hyperlite-daemon does every step as root (app/core/host_firewall.py)."""

import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core import privileged
from app.core.audit import log_action
from app.core.security import require_role

router = APIRouter(prefix="/cluster/firewall", tags=["cluster"])


class FirewallChange(BaseModel):
    document: dict = Field(description="The whole firewall: enabled, policy, rules, nodes, groups, ipsets")
    timeout_s: int = Field(60, ge=30, le=600, description="Seconds to confirm before the change is rolled back")


class ChangeId(BaseModel):
    id: str = Field(pattern=r"^[0-9a-f]{16}$")


@router.get("")
def firewall_state(user: dict = Depends(require_role("admin"))):
    """The saved firewall, this node's name, the change waiting here for its confirmation, and whether the rules are
    in place on this node."""
    return privileged.ask("host.firewall_state", user, "firewall_read", "cluster")


@router.post("/preview")
def firewall_preview(payload: FirewallChange, user: dict = Depends(require_role("admin"))):
    """The nftables ruleset this node would run, checked by nftables; nothing changes."""
    return privileged.ask("host.firewall_preview", user, "firewall_preview", "cluster", document=payload.document)


@router.post("/apply")
def firewall_apply(payload: FirewallChange, user: dict = Depends(require_role("admin"))):
    """Apply on this node; POST /cluster/firewall/confirm within `timeout_s` saves it for the cluster."""
    result = privileged.ask(
        "host.firewall_apply", user, "firewall_apply", "cluster", document=payload.document, timeout_s=payload.timeout_s
    )
    log_action(user["username"], "firewall_apply", "cluster", "success", result["id"])
    return result


@router.post("/confirm")
def firewall_confirm(payload: ChangeId, user: dict = Depends(require_role("admin"))):
    from app.core import host_firewall
    from app.repositories import registry

    result = privileged.ask("host.firewall_confirm", user, "firewall_confirm", "cluster", id=payload.id)
    # Saved once this browser reached the node through the new rules: the other members follow it.
    registry.settings().sync.set_app_setting(host_firewall.SETTING, json.dumps(result["document"]))
    log_action(user["username"], "firewall_confirm", "cluster", "success", payload.id)
    return {"confirmed": payload.id}


@router.post("/rollback")
def firewall_rollback(payload: ChangeId, user: dict = Depends(require_role("admin"))):
    result = privileged.ask("host.firewall_rollback", user, "firewall_rollback", "cluster", id=payload.id)
    log_action(user["username"], "firewall_rollback", "cluster", "success", payload.id)
    return result
