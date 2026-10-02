"""Shadow mode of hyperlite-cfs (app/repositories/cfs/shadow.py): its state, the differences between SQLite and the
daemon, and the copy that makes them equal. Administrators only: the report names objects across the whole fleet."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.core.audit import log_action
from app.core.cfs_client import CfsError
from app.core.security import require_role
from app.repositories.cfs import shadow

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cfs", tags=["cfs"])


@router.get("/shadow")
def shadow_report(user: dict = Depends(require_role("admin"))):
    return shadow.report()


@router.post("/shadow/seed")
def shadow_seed(user: dict = Depends(require_role("admin"))):
    try:
        done = shadow.seed()
    except shadow.ShadowDisabled as e:
        raise HTTPException(status_code=409, detail=shadow.DISABLED) from e
    except (CfsError, OSError) as e:
        logger.warning("hyperlite-cfs shadow seed failed: %s", e)
        reason = shadow.describe(e)
        log_action(user["username"], "cfs_shadow_seed", "hyperlite-cfs", "echec", reason)
        raise HTTPException(status_code=503, detail=f"The copy did not complete: {reason}") from e
    log_action(
        user["username"],
        "cfs_shadow_seed",
        "hyperlite-cfs",
        "succes",
        f"{done['ecrits']} written, {done['supprimes']} deleted",
    )
    return done


@router.post("/shadow/activer")
def shadow_turn_on(user: dict = Depends(require_role("admin"))):
    """Start hyperlite-cfs, turn shadow mode on and make the first copy."""
    try:
        done = shadow.turn_on()
    except shadow.ShadowError as e:
        log_action(user["username"], "cfs_shadow_on", "hyperlite-cfs", "echec", str(e))
        raise HTTPException(status_code=503, detail=str(e)) from e
    log_action(user["username"], "cfs_shadow_on", "hyperlite-cfs", "succes", f"{done['ecrits']} written")
    return done


@router.post("/shadow/desactiver")
def shadow_turn_off(user: dict = Depends(require_role("admin"))):
    """Stop copying and stop hyperlite-cfs; SQLite was the source of truth throughout, so nothing else changes."""
    try:
        shadow.turn_off()
    except shadow.ShadowError as e:
        log_action(user["username"], "cfs_shadow_off", "hyperlite-cfs", "echec", str(e))
        raise HTTPException(status_code=409 if shadow.forced() else 503, detail=str(e)) from e
    log_action(user["username"], "cfs_shadow_off", "hyperlite-cfs", "succes", "")
    return {"actif": False}
