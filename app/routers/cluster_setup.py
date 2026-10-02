"""Creating and joining a cluster (app/core/cluster_setup.py). Administrators only, except POST /cluster/membres,
which a joining node calls with the one-time ticket of the join information an administrator copied."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import cluster_setup
from app.core.audit import log_action
from app.core.cfs_client import CfsError
from app.core.security import require_role

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cluster", tags=["cluster"])


class CreateRequest(BaseModel):
    nom: str = Field(min_length=1, max_length=15)
    adresse: str = Field(min_length=1, max_length=64)


class JoinRequest(BaseModel):
    information: str = Field(min_length=1, max_length=2000)
    adresse: str = Field(min_length=1, max_length=64)
    confirmation: str = Field(max_length=15)


class MemberRequest(BaseModel):
    ticket: str = Field(max_length=100)
    nom: str = Field(max_length=63)
    adresse: str = Field(max_length=64)
    cle_ssh: str = Field(max_length=200)


class RemoveRequest(BaseModel):
    confirmation: str = Field(max_length=63)


def _failed(user, action, target, error):
    if isinstance(error, cluster_setup.ClusterError):
        log_action(user["username"], action, target, "echec", str(error))
        raise HTTPException(status_code=409, detail=str(error)) from None
    logger.warning("%s failed: %s", action, error)
    log_action(user["username"], action, target, "echec", "hyperlite-cfs does not answer")
    raise HTTPException(
        status_code=503, detail="hyperlite-cfs does not answer: see journalctl -u hyperlite-cfs"
    ) from None


@router.get("")
def cluster_state(user: dict = Depends(require_role("admin"))):
    return cluster_setup.state()


@router.post("/creer")
def create_cluster(payload: CreateRequest, user: dict = Depends(require_role("admin"))):
    try:
        return cluster_setup.create(payload.nom, payload.adresse, user["username"])
    except (cluster_setup.ClusterError, CfsError, OSError) as e:
        _failed(user, "cluster_create", payload.nom, e)


@router.post("/adhesion")
def join_information(user: dict = Depends(require_role("admin"))):
    """A new ticket each time: the previous join information stops working."""
    try:
        info = cluster_setup.join_information()
    except (cluster_setup.ClusterError, CfsError, OSError) as e:
        _failed(user, "cluster_join_information", "cluster", e)
    log_action(user["username"], "cluster_join_information", info["cluster"], "succes", f"valid until {info['expire']}")
    return info


@router.post("/rejoindre")
def join_cluster(payload: JoinRequest, user: dict = Depends(require_role("admin"))):
    try:
        return cluster_setup.join(payload.information, payload.adresse, payload.confirmation, user["username"])
    except (cluster_setup.ClusterError, CfsError, OSError) as e:
        _failed(user, "cluster_join", "cluster", e)


@router.post("/membres")
def accept_member(payload: MemberRequest):
    """Called by a joining node, not by a browser: the ticket stands for the administrator who copied it."""
    try:
        return cluster_setup.accept_member(payload.ticket, payload.nom, payload.adresse, payload.cle_ssh)
    except PermissionError as e:
        log_action("system", "cluster_join", payload.nom[:63], "echec", str(e))
        raise HTTPException(status_code=403, detail=str(e)) from None
    except cluster_setup.ClusterError as e:
        log_action("system", "cluster_join", payload.nom[:63], "echec", str(e))
        raise HTTPException(status_code=409, detail=str(e)) from None
    except (CfsError, OSError) as e:
        logger.warning("Letting %s in failed: %s", payload.nom[:63], e)
        raise HTTPException(status_code=503, detail="hyperlite-cfs does not answer on this member") from None


@router.post("/membres/{nom}/retirer")
def remove_member(nom: str, payload: RemoveRequest, user: dict = Depends(require_role("admin"))):
    try:
        return cluster_setup.remove_member(nom, payload.confirmation, user["username"])
    except (cluster_setup.ClusterError, CfsError, OSError) as e:
        _failed(user, "cluster_remove_member", nom[:63], e)
