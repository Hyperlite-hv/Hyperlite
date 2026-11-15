"""Connection diagnosis (app/core/net_diagnose.py, docs/design/console-2.md section 9)."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.core import net_diagnose
from app.core.audit import log_action
from app.core.security import get_current_user

router = APIRouter(prefix="/network", tags=["network"])


class DiagnoseIn(BaseModel):
    from_vm: str = Field(min_length=1, max_length=64)
    from_node: str | None = Field(None, max_length=64)
    to: str = Field(min_length=1, max_length=64)
    to_node: str | None = Field(None, max_length=64)
    port: int = Field(ge=1, le=65535)
    protocol: str = "tcp"


@router.post("/diagnose")
async def diagnose(payload: DiagnoseIn, user: dict = Depends(get_current_user)):
    """Can `from_vm` reach `to` (a VM or an IPv4 address) on `port`? Hyperlite's firewalls on the path, where both are,
    a real try from inside the VM (vm.exec on it) and whether the target listens (vm.exec on it)."""
    node = lambda n: None if not n or n == "local" else n  # noqa: E731
    try:
        result = await run_in_threadpool(
            net_diagnose.diagnose, user, payload.from_vm, payload.to, payload.port, payload.protocol,
            node(payload.from_node), node(payload.to_node),
        )  # fmt: skip
    except net_diagnose.DiagnoseError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from None
    log_action(user["username"], "network_diagnose", payload.from_vm, "success",
               f"-> {payload.to}:{payload.port}/{payload.protocol}: {result['verdict']}")  # fmt: skip
    return result
