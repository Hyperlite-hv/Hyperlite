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
