"""Creating and joining a cluster (app/core/cluster_setup.py). Administrators only, except POST /cluster/members,
which a joining node calls with the one-time ticket of the join information an administrator copied."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import cluster_setup, privileged
from app.core.audit import log_action
from app.core.security import require_role
from app.daemon.registry import DaemonError

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
    """The answer to a step hyperlite-daemon refused or could not make (app/daemon/ops/cluster.py, cluster.setup_*), as
    the routes gave them before the daemon ran them: a cluster's refusal 409, hyperlite-cfs not answering 503."""
    if error.kind in ("refused", "invalid_arguments", "in_use", "not_found"):
        log_action(user["username"], action, target, "failed", error.detail)
        raise HTTPException(status_code=409, detail=error.detail) from None
    logger.warning("%s failed: %s", action, error.detail)
    log_action(user["username"], action, target, "failed", "hyperlite-cfs does not answer")
    raise HTTPException(
        status_code=503, detail="hyperlite-cfs does not answer: see journalctl -u hyperlite-cfs"
    ) from None


# A join waits for the cluster (cluster_setup.JOIN_WAIT_S) before it answers; a creation, for the new cluster.
JOIN_TIMEOUT_S = cluster_setup.JOIN_WAIT_S + 120
CREATE_TIMEOUT_S = 300


@router.get("")
def cluster_state(user: dict = Depends(require_role("admin"))):
    return cluster_setup.state()


@router.post("/create")
def create_cluster(payload: CreateRequest, user: dict = Depends(require_role("admin"))):
    try:
        return privileged.run(
            "cluster.setup_create", user=user["username"], timeout=CREATE_TIMEOUT_S, name=payload.name,
            address=payload.address,
        )  # fmt: skip
    except DaemonError as e:
        _failed(user, "cluster_create", payload.name, e)


@router.post("/adhesion")
def join_information(user: dict = Depends(require_role("admin"))):
    """A new ticket each time: the previous join information stops working."""
    try:
        info = privileged.run("cluster.setup_join_information", user=user["username"])
    except DaemonError as e:
        _failed(user, "cluster_join_information", "cluster", e)
    log_action(
        user["username"], "cluster_join_information", info["cluster"], "success", f"valid until {info['expire']}"
    )
    return info


@router.post("/join")
def join_cluster(payload: JoinRequest, user: dict = Depends(require_role("admin"))):
    try:
        return privileged.run(
            "cluster.setup_join", user=user["username"], timeout=JOIN_TIMEOUT_S, information=payload.information,
            address=payload.address, confirmation=payload.confirmation,
        )  # fmt: skip
    except DaemonError as e:
        _failed(user, "cluster_join", "cluster", e)


@router.post("/members")
def accept_member(payload: MemberRequest):
    """Called by a joining node, not by a browser: the ticket stands for the administrator who copied it."""
    try:
        return privileged.run(
            "cluster.setup_accept_member", timeout=CREATE_TIMEOUT_S, ticket=payload.ticket, name=payload.name,
            address=payload.address, ssh_key=payload.ssh_key, fingerprint=payload.fingerprint,
            auth_keys=payload.auth_keys,
        )  # fmt: skip
    except DaemonError as e:
        if e.kind in ("forbidden", "refused", "invalid_arguments"):
            log_action("system", "cluster_join", payload.name[:63], "failed", e.detail)
            raise HTTPException(status_code=403 if e.kind == "forbidden" else 409, detail=e.detail) from None
        logger.warning("Letting %s in failed: %s", payload.name[:63], e.detail)
        raise HTTPException(status_code=503, detail="hyperlite-cfs does not answer on this member") from None


@router.post("/principal")
def set_primary(payload: PrimaryRequest, user: dict = Depends(require_role("admin"))):
    """The primary node counts twice in the quorum: with one other node, it keeps the quorum alone."""
    try:
        return privileged.run(
            "cluster.setup_primary", user=user["username"], timeout=CREATE_TIMEOUT_S, name=payload.name
        )
    except DaemonError as e:
        _failed(user, "cluster_primary", payload.name or "none", e)


@router.post("/members/{name}/remove")
def remove_member(name: str, payload: RemoveRequest, user: dict = Depends(require_role("admin"))):
    try:
        return privileged.run(
            "cluster.setup_remove_member", user=user["username"], timeout=CREATE_TIMEOUT_S, name=name[:63],
            confirmation=payload.confirmation,
        )  # fmt: skip
    except DaemonError as e:
        _failed(user, "cluster_remove_member", name[:63], e)
