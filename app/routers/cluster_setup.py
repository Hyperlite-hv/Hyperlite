"""Creating and joining a cluster (app/core/cluster_setup.py). Administrators only, except POST /cluster/members,
which a joining node calls with the one-time ticket of the join information an administrator copied."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core import cluster_setup, privileged
from app.core.audit import log_action
from app.core.security import require_role
from app.daemon.registry import DaemonError

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


# Creating, joining and leaving are hyperlite-daemon's (item 1 of #298): Corosync's key and configuration, its services,
# root's authorized_keys and the node's env file are root's. Each waits for Corosync and hyperlite-cfs.
SETUP_TIMEOUT_S = 180
JOIN_TIMEOUT_S = cluster_setup.JOIN_WAIT_S + 180


def _ask(op, user, action, target, timeout=SETUP_TIMEOUT_S, **args):
    """`op`'s result; what it refused (a ClusterError) 409, hyperlite-cfs not answering 503."""
    return privileged.ask(op, user, action, target, refused=409, timeout=timeout, **args)


@router.get("")
def cluster_state(user: dict = Depends(require_role("admin"))):
    return cluster_setup.state()


@router.post("/create")
def create_cluster(payload: CreateRequest, user: dict = Depends(require_role("admin"))):
    return _ask("cluster.create", user, "cluster_create", payload.name, name=payload.name, address=payload.address)


@router.post("/adhesion")
def join_information(user: dict = Depends(require_role("admin"))):
    """A new ticket each time: the previous join information stops working."""
    info = _ask("cluster.join_information", user, "cluster_join_information", "cluster")
    log_action(
        user["username"], "cluster_join_information", info["cluster"], "success", f"valid until {info['expire']}"
    )
    return info


@router.post("/join")
def join_cluster(payload: JoinRequest, user: dict = Depends(require_role("admin"))):
    return _ask(
        "cluster.join",
        user,
        "cluster_join",
        "cluster",
        timeout=JOIN_TIMEOUT_S,
        information=payload.information,
        address=payload.address,
        confirmation=payload.confirmation,
    )


@router.post("/members")
def accept_member(payload: MemberRequest):
    """Called by a joining node, not by a browser: the ticket stands for the administrator who copied it."""
    try:
        return privileged.run("cluster.accept_member", timeout=SETUP_TIMEOUT_S, **payload.model_dump())
    except DaemonError as e:
        if e.kind in ("forbidden", "refused"):
            log_action("system", "cluster_join", payload.name[:63], "failed", e.detail)
        raise privileged.http_error(e, refused=409) from None


@router.post("/principal")
def set_primary(payload: PrimaryRequest, user: dict = Depends(require_role("admin"))):
    """The primary node counts twice in the quorum: with one other node, it keeps the quorum alone."""
    return _ask("cluster.set_primary", user, "cluster_primary", payload.name or "none", name=payload.name)


@router.post("/members/{name}/remove")
def remove_member(name: str, payload: RemoveRequest, user: dict = Depends(require_role("admin"))):
    return _ask(
        "cluster.remove_member", user, "cluster_remove_member", name[:63], name=name, confirmation=payload.confirmation
    )
