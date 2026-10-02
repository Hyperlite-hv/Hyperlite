"""Shadow mode of hyperlite-cfs (app/repositories/cfs/shadow.py): its state, the differences between SQLite and the
daemon, and the copy that makes them equal. Administrators only: the report names objects across the whole fleet."""

from fastapi import APIRouter, Depends, HTTPException

from app.core.audit import log_action
from app.core.cfs_client import CfsError
from app.core.security import require_role
from app.repositories.cfs import shadow

router = APIRouter(prefix="/cfs", tags=["cfs"])


@router.get("/shadow")
def shadow_report(user: dict = Depends(require_role("admin"))):
    return shadow.report()


@router.post("/shadow/seed")
def shadow_seed(user: dict = Depends(require_role("admin"))):
    try:
        done = shadow.seed()
    except shadow.ShadowDisabled as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except (CfsError, OSError) as e:
        log_action(user["username"], "cfs_shadow_seed", "hyperlite-cfs", "echec", str(e))
        raise HTTPException(status_code=503, detail=f"hyperlite-cfs did not take the copy: {e}") from e
    log_action(
        user["username"],
        "cfs_shadow_seed",
        "hyperlite-cfs",
        "succes",
        f"{done['ecrits']} written, {done['supprimes']} deleted",
    )
    return done
