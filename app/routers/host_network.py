"""The node's own bridges, bonds and VLAN interfaces (docs/design/network.md, section 8): read, previewed as a diff,
applied with a rollback timer, then confirmed or rolled back. hyperlite-daemon does every step as root
(app/core/host_network.py); `?node=` sends the request to that member (app/core/cluster_view.py)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core import privileged
from app.core.audit import log_action
from app.core.security import require_role

router = APIRouter(prefix="/host/network", tags=["host"])


class NetworkChange(BaseModel):
    interfaces: list[dict] = Field(max_length=64, description="The whole definition of the managed interfaces")
    timeout_s: int = Field(60, ge=30, le=600, description="Seconds to confirm before the change is rolled back")


class ChangeId(BaseModel):
    id: str = Field(pattern=r"^[0-9a-f]{16}$")


@router.get("")
def network_state(user: dict = Depends(require_role("admin"))):
    """The host's interfaces (`configured_by`: hyperlite, system or none), the managed ones, the change waiting for
    its confirmation and the tools the node has."""
    return privileged.ask("host.network_state", user, "host_network_read", "host")


@router.post("/preview")
def network_preview(payload: NetworkChange, user: dict = Depends(require_role("admin"))):
    """The diff of the managed file and the interfaces that would come down and up; nothing changes."""
    return privileged.ask("host.network_preview", user, "host_network_preview", "host", interfaces=payload.interfaces)


@router.post("/apply")
def network_apply(payload: NetworkChange, user: dict = Depends(require_role("admin"))):
    """Apply, then confirm within `timeout_s` (POST /host/network/confirm) or the previous configuration comes back."""
    result = privileged.ask(
        "host.network_apply",
        user,
        "host_network_apply",
        "host",
        interfaces=payload.interfaces,
        timeout_s=payload.timeout_s,
    )
    if result.get("id"):
        log_action(user["username"], "host_network_apply", "host", "success", ", ".join(result["changes"]))
    return result


@router.post("/confirm")
def network_confirm(payload: ChangeId, user: dict = Depends(require_role("admin"))):
    result = privileged.ask("host.network_confirm", user, "host_network_confirm", "host", id=payload.id)
    log_action(user["username"], "host_network_confirm", "host", "success", payload.id)
    return result


@router.post("/rollback")
def network_rollback(payload: ChangeId, user: dict = Depends(require_role("admin"))):
    result = privileged.ask("host.network_rollback", user, "host_network_rollback", "host", id=payload.id)
    log_action(user["username"], "host_network_rollback", "host", "success", payload.id)
    return result
