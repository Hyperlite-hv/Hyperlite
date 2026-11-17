"""Costs and budgets, and guest updates (docs/design/governance.md, sections 10 and 11)."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import costs, guest_scripts, guest_updates, patch_plans
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


# ---- Patch plans (docs/design/console-2.md, section 4) ----


class PlanIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    target_kind: str
    target: list[str] | str
    days: list[int] = Field(default_factory=list, max_length=7)
    monthday: int | None = None
    at_time: str
    tz: str = "UTC"
    duration_min: int = Field(120, ge=30, le=1440)
    reboot: bool = False
    enabled: bool = True


def _plan(fn, *args):
    try:
        return fn(*args)
    except patch_plans.PlanError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None


@router.get("/guest-updates/plans")
def list_plans(user: dict = Depends(require_area("node.view"))):
    """Every patch plan of this node, with its next three starts."""
    return listing(patch_plans.plans())


@router.post("/guest-updates/plans", status_code=201)
def create_plan(payload: PlanIn, user: dict = Depends(require_area("node.manage"))):
    """A recurring window. It acts with its author's rights: at each start, only the VMs the author may run commands
    in (vm.exec) are updated."""
    if payload.target_kind == "vms":
        _refuse_unless_exec(user, payload.target if isinstance(payload.target, list) else [payload.target])
    plan = _plan(patch_plans.save, payload.model_dump(), user["username"])
    log_action(user["username"], "patch_plan_create", plan["name"], "success")
    return plan


@router.put("/guest-updates/plans/{plan_id}")
def update_plan(plan_id: str, payload: PlanIn, user: dict = Depends(require_area("node.manage"))):
    if payload.target_kind == "vms":
        _refuse_unless_exec(user, payload.target if isinstance(payload.target, list) else [payload.target])
    plan = _plan(patch_plans.save, payload.model_dump(), user["username"], plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Not found")
    log_action(user["username"], "patch_plan_change", plan["name"], "success")
    return plan


@router.delete("/guest-updates/plans/{plan_id}")
def delete_plan(plan_id: str, user: dict = Depends(require_area("node.manage"))):
    if not patch_plans.delete(plan_id):
        raise HTTPException(status_code=404, detail="Not found")
    log_action(user["username"], "patch_plan_delete", plan_id, "success")
    return {"message": "Plan deleted"}


# ---- Script library (docs/design/console-2.md, section 7) ----


class ScriptIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field("", max_length=500)
    shell: str
    body: str = Field(min_length=1, max_length=32000)
    params: list[dict] = Field(default_factory=list, max_length=20)


class ScriptRunIn(BaseModel):
    script_id: str = Field(min_length=1, max_length=64)
    vms: list[str] = Field(min_length=1, max_length=200)
    params: dict[str, str] = Field(default_factory=dict)


def _script(fn, *args):
    try:
        return fn(*args)
    except guest_scripts.ScriptError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from None


@router.get("/guest-scripts")
def list_scripts(user: dict = Depends(require_area("node.view"))):
    """The built-in scripts and the administrators' own."""
    return listing(guest_scripts.scripts())


@router.post("/guest-scripts", status_code=201)
def create_script(payload: ScriptIn, user: dict = Depends(require_role("admin"))):
    s = _script(guest_scripts.save, payload.model_dump(), user["username"])
    log_action(user["username"], "guest_script_create", s["name"], "success")
    return s


@router.put("/guest-scripts/{script_id}")
def update_script(script_id: str, payload: ScriptIn, user: dict = Depends(require_role("admin"))):
    if script_id.startswith("builtin:"):
        raise HTTPException(status_code=409, detail="A built-in script is not changed: copy it to make your own")
    s = _script(guest_scripts.save, payload.model_dump(), user["username"], script_id)
    if s is None:
        raise HTTPException(status_code=404, detail="Not found")
    log_action(user["username"], "guest_script_change", s["name"], "success")
    return s


@router.delete("/guest-scripts/{script_id}")
def delete_script(script_id: str, user: dict = Depends(require_role("admin"))):
    if script_id.startswith("builtin:") or not guest_scripts.delete(script_id):
        raise HTTPException(status_code=404, detail="Not found")
    log_action(user["username"], "guest_script_delete", script_id, "success")
    return {"message": "Script deleted"}


@router.get("/guest-scripts/runs")
def list_script_runs(user: dict = Depends(require_area("node.view"))):
    """The runs; a run's parameters and outputs only to who started it or may run commands in each of its VMs (they
    may hold what the script printed from those VMs)."""
    from app.core import permissions

    def visible(run):
        if user["role"] == "admin" or run.get("created_by") == user["username"]:
            return run
        if all(permissions.has_privilege(user, vm, "vm.exec") for vm in run.get("vms") or []):
            return run
        hidden = {vm: {k: v for k, v in (r or {}).items() if k not in ("output", "error")}
                  for vm, r in (run.get("results") or {}).items()}  # fmt: skip
        return {**run, "params": {}, "results": hidden}

    return listing([visible(r) for r in guest_scripts.runs()])


@router.post("/guest-scripts/runs", status_code=201)
def run_script(payload: ScriptRunIn, user: dict = Depends(require_area("node.view"))):
    """Run a script in each VM now (vm.exec on each: it runs as root in the guest), followed every minute."""
    _refuse_unless_exec(user, payload.vms)
    return _script(guest_scripts.run, payload.script_id, payload.vms, payload.params, user["username"])
