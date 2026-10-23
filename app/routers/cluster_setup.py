"""Creating and joining a cluster (app/core/cluster_setup.py). Administrators only, except POST /cluster/members,
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
    name: str = Field(min_length=1, max_length=15)
    address: str = Field(min_length=1, max_length=64)


class JoinRequest(BaseModel):
    information: str = Field(min_length=1, max_length=2000)
    address: str = Field(min_length=1, max_length=64)
    confirmation: str = Field(max_length=15)


class MemberRequest(BaseModel):
    ticket: str = Field(max_length=100)
    name: str = Field(max_length=63)
    address: str = Field(max_length=64)
    ssh_key: str = Field(max_length=200)
    # A node before 1.3 sends neither: it publishes them itself once it is up.
    fingerprint: str | None = Field(None, max_length=64)
    auth_keys: dict | None = None


class PrimaryRequest(BaseModel):
    name: str | None = Field(None, max_length=63)  # None: no primary node, one vote each


class RemoveRequest(BaseModel):
    confirmation: str = Field(max_length=63)


def _failed(user, action, target, error):
    if isinstance(error, cluster_setup.ClusterError):
        log_action(user["username"], action, target, "failed", str(error))
        raise HTTPException(status_code=409, detail=str(error)) from None
    logger.warning("%s failed: %s", action, error)
    log_action(user["username"], action, target, "failed", "hyperlite-cfs does not answer")
    raise HTTPException(
        status_code=503, detail="hyperlite-cfs does not answer: see journalctl -u hyperlite-cfs"
    ) from None


@router.get("")
def cluster_state(user: dict = Depends(require_role("admin"))):
    return cluster_setup.state()


@router.post("/create")
def create_cluster(payload: CreateRequest, user: dict = Depends(require_role("admin"))):
    try:
        return cluster_setup.create(payload.name, payload.address, user["username"])
    except (cluster_setup.ClusterError, CfsError, OSError) as e:
        _failed(user, "cluster_create", payload.name, e)


@router.post("/adhesion")
def join_information(user: dict = Depends(require_role("admin"))):
    """A new ticket each time: the previous join information stops working."""
    try:
        info = cluster_setup.join_information()
    except (cluster_setup.ClusterError, CfsError, OSError) as e:
        _failed(user, "cluster_join_information", "cluster", e)
    log_action(
        user["username"], "cluster_join_information", info["cluster"], "success", f"valid until {info['expire']}"
    )
    return info


@router.post("/join")
def join_cluster(payload: JoinRequest, user: dict = Depends(require_role("admin"))):
    try:
        return cluster_setup.join(payload.information, payload.address, payload.confirmation, user["username"])
    except (cluster_setup.ClusterError, CfsError, OSError) as e:
        _failed(user, "cluster_join", "cluster", e)


@router.post("/members")
def accept_member(payload: MemberRequest):
    """Called by a joining node, not by a browser: the ticket stands for the administrator who copied it."""
    try:
        return cluster_setup.accept_member(
            payload.ticket, payload.name, payload.address, payload.ssh_key, payload.fingerprint, payload.auth_keys
        )
    except PermissionError as e:
        log_action("system", "cluster_join", payload.name[:63], "failed", str(e))
        raise HTTPException(status_code=403, detail=str(e)) from None
    except cluster_setup.ClusterError as e:
        log_action("system", "cluster_join", payload.name[:63], "failed", str(e))
        raise HTTPException(status_code=409, detail=str(e)) from None
    except (CfsError, OSError) as e:
        logger.warning("Letting %s in failed: %s", payload.name[:63], e)
        raise HTTPException(status_code=503, detail="hyperlite-cfs does not answer on this member") from None


@router.post("/principal")
def set_primary(payload: PrimaryRequest, user: dict = Depends(require_role("admin"))):
    """The primary node counts twice in the quorum: with one other node, it keeps the quorum alone."""
    try:
        return cluster_setup.set_primary(payload.name, user["username"])
    except (cluster_setup.ClusterError, CfsError, OSError) as e:
        _failed(user, "cluster_primary", payload.name or "none", e)


@router.post("/members/{name}/remove")
def remove_member(name: str, payload: RemoveRequest, user: dict = Depends(require_role("admin"))):
    try:
        return cluster_setup.remove_member(name, payload.confirmation, user["username"])
    except (cluster_setup.ClusterError, CfsError, OSError) as e:
        _failed(user, "cluster_remove_member", name[:63], e)
