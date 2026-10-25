"""Several clusters and sites from one view (app/core/sites.py, docs/design/multi-cluster.md): what this cluster tells
another one that has it as a remote, and the remotes this cluster views."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import sites
from app.core.audit import log_action
from app.core.listing import listing
from app.core.security import get_current_user, require_role

router = APIRouter()


def _error(e):
    return HTTPException(status_code=e.status, detail=e.message)


# ---- This cluster, as a remote's view reads it ----


@router.get("/sites/summary")
def site_summary(user: dict = Depends(get_current_user)):
    """This cluster in one call: its nodes, their load, its VMs by state and its problems."""
    return sites.summary()


@router.get("/sites/fingerprints")
def site_fingerprints(user: dict = Depends(get_current_user)):
    """The HTTPS certificate fingerprint of every member, pinned by a view that adds this cluster."""
    return sites.fingerprints()


# ---- The remotes this cluster views ----


class SiteProbe(BaseModel):
    address: str
    port: int = Field(8000, ge=1, le=65535)


class SiteCreate(SiteProbe):
    name: str
    fingerprint: str = Field(description="SHA-256 of the node's HTTPS certificate, as its own dashboard shows it")
    view_token: str = Field(max_length=4096, description="An API token of a viewer account on the remote")
    manage_token: str | None = Field(None, max_length=4096, description="A token for the actions (step 2), optional")


@router.get("/sites")
def site_overview(user: dict = Depends(require_role("admin"))):
    """This cluster and every remote, each with its summary or why it is missing."""
    return listing(sites.overview())


@router.get("/sites/search")
def site_search(q: str, user: dict = Depends(require_role("admin"))):
    """The VMs of every remote whose name, tag or address holds `q`."""
    try:
        return sites.search(q)
    except sites.SiteError as e:
        raise _error(e) from None


@router.post("/sites/probe")
def site_probe(payload: SiteProbe, user: dict = Depends(require_role("admin"))):
    """The certificate fingerprint an address presents, to be compared with the one its dashboard shows."""
    try:
        return {"fingerprint": sites.probe(payload.address, payload.port)}
    except sites.SiteError as e:
        raise _error(e) from None


@router.get("/sites/remotes")
def site_remotes(user: dict = Depends(require_role("admin"))):
    return listing(sites.list_remotes())


@router.post("/sites/remotes", status_code=201)
def site_add(payload: SiteCreate, user: dict = Depends(require_role("admin"))):
    """Record a remote once its certificate matches and its token is accepted; its other members are read from it."""
    try:
        remote = sites.add(
            payload.name,
            payload.address,
            payload.port,
            payload.fingerprint,
            payload.view_token,
            user["username"],
            payload.manage_token or None,
        )
    except sites.SiteError as e:
        log_action(user["username"], "create_site", payload.name, "failed", e.message)
        raise _error(e) from None
    log_action(user["username"], "create_site", payload.name, "success", f"{payload.address}:{payload.port}")
    return remote


@router.delete("/sites/remotes/{name}")
def site_remove(name: str, user: dict = Depends(require_role("admin"))):
    try:
        sites.remove(name)
    except sites.SiteError as e:
        raise _error(e) from None
    log_action(user["username"], "delete_site", name, "success", "")
    return {"deleted": name}


@router.get("/sites/{remote}/{what}")
def site_list(remote: str, what: str, user: dict = Depends(require_role("admin"))):
    """One remote's VMs, nodes or storage pools, as its own API answers them."""
    try:
        return sites.remote_list(remote, what)
    except sites.SiteError as e:
        raise _error(e) from None
