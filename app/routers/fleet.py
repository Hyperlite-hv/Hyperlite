"""Costs and budgets, and guest updates (docs/design/governance.md, sections 10 and 11)."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import costs, guest_updates
from app.core import permissions as perm
from app.core.audit import log_action
from app.core.listing import listing
from app.core.security import require_area, require_role

router = APIRouter(tags=["fleet"])


class PricesIn(BaseModel):
    currency: str = Field("EUR", min_length=1, max_length=8)
    vcpu: float = Field(ge=0, le=100000)
    memory_gb: float = Field(ge=0, le=100000)
    disk_gb: float = Field(ge=0, le=100000)


class BudgetIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scope: str = Field(min_length=1, max_length=64)
    monthly: float = Field(gt=0, le=10**9)
    alert_percent: int = Field(80, ge=1, le=100)


@router.get("/costs")
def get_costs(user: dict = Depends(require_area("cluster.view"))):
    rep = costs.report()
    return {**rep, "budgets": costs.budgets(rep)}


@router.put("/costs/prices")
def put_prices(payload: PricesIn, user: dict = Depends(require_role("admin"))):
    costs.set_prices(payload.model_dump())
    log_action(user["username"], "cost_prices", payload.currency, "success")
    return costs.prices()


@router.post("/costs/budgets", status_code=201)
def create_budget(payload: BudgetIn, user: dict = Depends(require_area("cluster.manage"))):
    try:
        return costs.save_budget(payload.model_dump(), user["username"])
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None


@router.put("/costs/budgets/{budget_id}")
def update_budget(budget_id: str, payload: BudgetIn, user: dict = Depends(require_area("cluster.manage"))):
    try:
        found = costs.save_budget(payload.model_dump(), user["username"], budget_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    if found is None:
        raise HTTPException(status_code=404, detail="Not found")
    return found


@router.delete("/costs/budgets/{budget_id}")
def delete_budget(budget_id: str, user: dict = Depends(require_area("cluster.manage"))):
    if not costs.delete_budget(budget_id):
        raise HTTPException(status_code=404, detail="Not found")
    return {"message": "Budget deleted"}


# ---- Guest updates ----


def _refuse_unless_exec(user, vms):
    refused = [vm for vm in vms if not perm.has_privilege(user, vm, "vm.exec")]
    if refused:
        raise HTTPException(
            status_code=403, detail=f"Insufficient rights on VM '{refused[0]}' (required privilege: vm.exec)"
        )


class ScanIn(BaseModel):
    vms: list[str] = Field(min_length=1, max_length=200)


@router.get("/guest-updates")
def list_guest_updates(user: dict = Depends(require_area("node.view"))):
    return {"states": guest_updates.states(), "runs": guest_updates.runs()}


@router.post("/guest-updates/scan")
def scan(payload: ScanIn, user: dict = Depends(require_area("node.view"))):
    """Ask each guest what it has pending (vm.exec on each: it runs a command as root in the guest)."""
    _refuse_unless_exec(user, payload.vms)
    return listing([guest_updates.scan(vm, user["username"]) for vm in payload.vms])


class RunIn(BaseModel):
    vms: list[str] = Field(min_length=1, max_length=200)
    window_start: datetime
    window_end: datetime
    reboot: bool = False


@router.post("/guest-updates/runs", status_code=201)
def schedule_run(payload: RunIn, user: dict = Depends(require_area("node.view"))):
    _refuse_unless_exec(user, payload.vms)
    start = payload.window_start if payload.window_start.tzinfo else payload.window_start.replace(tzinfo=UTC)
    end = payload.window_end if payload.window_end.tzinfo else payload.window_end.replace(tzinfo=UTC)
    if end <= start or end <= datetime.now(UTC):
        raise HTTPException(status_code=422, detail="The window must end after it starts, and in the future")
    run = guest_updates.schedule(payload.vms, start.isoformat(), end.isoformat(), payload.reboot, user["username"])
    log_action(
        user["username"], "guest_updates_scheduled", ", ".join(run["vms"]), "success", f"from {start.isoformat()}"
    )
    return run


@router.post("/guest-updates/runs/{run_id}/cancel")
def cancel_run(run_id: str, user: dict = Depends(require_area("node.manage"))):
    if not guest_updates.cancel(run_id):
        raise HTTPException(status_code=409, detail="Only a run that has not started can be cancelled")
    return {"message": "Run cancelled"}
