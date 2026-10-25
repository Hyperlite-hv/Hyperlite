"""Shadow mode of hyperlite-cfs (app/repositories/cfs/shadow.py): its state, the differences between SQLite and the
daemon, and the copy that makes them equal. Administrators only: the report names objects across the whole fleet.
hyperlite-daemon does each of them (app/daemon/ops/cfs.py): only root writes the cluster's tree and starts its
service."""

from fastapi import APIRouter, Depends, HTTPException

from app.core import privileged
from app.core.audit import log_action
from app.core.security import require_role
from app.daemon.registry import DaemonError
from app.repositories.cfs import shadow

router = APIRouter(prefix="/cfs", tags=["cfs"])


def _ask(op, user):
    return privileged.run(op, user=user["username"], timeout=shadow.START_WAIT_S + 60)


@router.get("/shadow")
def shadow_report(user: dict = Depends(require_role("admin"))):
    try:
        return _ask("cfs.report", user)
    except DaemonError as e:
        raise privileged.http_error(e) from e


@router.post("/shadow/seed")
def shadow_seed(user: dict = Depends(require_role("admin"))):
    try:
        done = _ask("cfs.seed", user)
    except DaemonError as e:
        if e.kind == "in_use":
            raise HTTPException(status_code=409, detail=e.detail) from e
        log_action(user["username"], "cfs_shadow_seed", "hyperlite-cfs", "failed", e.detail)
        raise HTTPException(status_code=503, detail=f"The copy did not complete: {e.detail}") from e
    log_action(
        user["username"],
        "cfs_shadow_seed",
        "hyperlite-cfs",
        "success",
        f"{done['written']} written, {done['deleted']} deleted",
    )
    return done


@router.post("/shadow/enable")
def shadow_turn_on(user: dict = Depends(require_role("admin"))):
    """Start hyperlite-cfs, turn shadow mode on and make the first copy."""
    try:
        done = _ask("cfs.enable", user)
    except DaemonError as e:
        log_action(user["username"], "cfs_shadow_on", "hyperlite-cfs", "failed", e.detail)
        raise HTTPException(status_code=503, detail=e.detail) from e
    log_action(user["username"], "cfs_shadow_on", "hyperlite-cfs", "success", f"{done['written']} written")
    return done


@router.post("/shadow/disable")
def shadow_turn_off(user: dict = Depends(require_role("admin"))):
    """Stop copying and stop hyperlite-cfs; SQLite was the source of truth throughout, so nothing else changes."""
    try:
        _ask("cfs.disable", user)
    except DaemonError as e:
        log_action(user["username"], "cfs_shadow_off", "hyperlite-cfs", "failed", e.detail)
        raise HTTPException(status_code=409 if e.kind == "in_use" else 503, detail=e.detail) from e
    log_action(user["username"], "cfs_shadow_off", "hyperlite-cfs", "success", "")
    return {"active": False}
