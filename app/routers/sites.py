"""Several clusters and sites from one view (app/core/sites.py, docs/design/multi-cluster.md): what this cluster tells
another one that has it as a remote, and the remotes this cluster views."""

import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.core import privileged, sites
from app.core.audit import log_action
from app.core.listing import listing
from app.core.security import get_current_user, require_role
from app.daemon.registry import DaemonError

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


class SiteToken(BaseModel):
    token: str = Field(min_length=1, max_length=4096)


@router.put("/sites/remotes/{name}/tokens/{use}")
def site_set_token(name: str, use: str, payload: SiteToken, user: dict = Depends(require_role("admin"))):
    """Give the remote a token for a use (`view`, `manage`), once the remote accepts it."""
    try:
        remote = sites.set_token(name, use, payload.token)
    except sites.SiteError as e:
        log_action(user["username"], "site_token", f"{name}/{use}", "failed", e.message)
        raise _error(e) from None
    log_action(user["username"], "site_token", f"{name}/{use}", "success", "set")
    return remote


@router.delete("/sites/remotes/{name}/tokens/{use}")
def site_remove_token(name: str, use: str, user: dict = Depends(require_role("admin"))):
    try:
        remote = sites.set_token(name, use, None)
    except sites.SiteError as e:
        raise _error(e) from None
    log_action(user["username"], "site_token", f"{name}/{use}", "success", "removed")
    return remote


class SiteAction(BaseModel):
    on: str | None = Field(None, max_length=63, description="The remote node the VM runs on, as its list says")


class SiteMigrate(SiteAction):
    target_node: str = Field(min_length=1, max_length=63)


@router.post("/sites/{remote}/vms/{vm}/migrate", status_code=202)
def site_migrate(remote: str, vm: str, payload: SiteMigrate, user: dict = Depends(require_role("admin"))):
    """Move a VM of the remote to another of its nodes, with the remote's manage token."""
    target = f"{remote}/{vm}"
    try:
        answer = sites.migrate(remote, vm, payload.target_node, payload.on)
    except sites.SiteError as e:
        log_action(user["username"], "site_vm_migrate", target, "failed", e.message)
        raise _error(e) from None
    log_action(user["username"], "site_vm_migrate", target, "success", f"{payload.on or '?'} -> {payload.target_node}")
    return answer


@router.post("/sites/{remote}/vms/{vm}/{action}", status_code=202)
def site_action(remote: str, vm: str, action: str, payload: SiteAction, user: dict = Depends(require_role("admin"))):
    """A power action (start, shutdown, stop, reboot, reset) on a VM of the remote, with its manage token."""
    target, what = f"{remote}/{vm}", f"site_vm_{action if action in sites.ACTIONS else 'unknown'}"
    try:
        answer = sites.act(remote, vm, action, payload.on)
    except sites.SiteError as e:
        log_action(user["username"], what, target, "failed", e.message)
        raise _error(e) from None
    log_action(user["username"], what, target, "success", payload.on or "")
    return answer


# ---- A VM moved to another cluster (docs/design/multi-cluster.md, section 9): the source's side ----


@router.get("/sites/outgoing/{vm}")
def site_outgoing(vm: str, user: dict = Depends(require_role("admin"))):
    """What the move dialog shows: the stopped VM's disks and cards, and what stays behind; a VM that cannot go (it
    runs, has a snapshot or a device of this host) is refused with why."""
    from app.core import site_move

    try:
        return site_move.describe(vm, user["username"])
    except DaemonError as e:
        raise privileged.http_error(e, refused=409) from None


class SiteMove(BaseModel):
    remote: str = Field(min_length=1, max_length=63)
    node: str = Field(min_length=1, max_length=63, description="The remote node the VM goes to")
    pools: list[str] = Field(min_length=1, max_length=16, description="A storage pool of that node for each disk")
    networks: list[str] = Field(default_factory=list, max_length=16, description="A network of that node per card")
    name: str | None = Field(None, max_length=63, description="Its name there; the same by default")
    start: bool = False
    limit_mib: int | None = Field(None, ge=1, le=100000, description="Bandwidth limit, MiB/s")


@router.post("/sites/outgoing/{vm}", status_code=202)
def site_move_vm(vm: str, payload: SiteMove, user: dict = Depends(require_role("admin"))):
    """Move the stopped VM to a node of another cluster, as `qm remote-migrate --delete`: a task (202), at the end of
    which the VM is defined there and deleted here. Needs the remote's manage token."""
    from app.core import site_move, vm_locks
    from app.core.tasks import answer_later, create_task

    if not sites._repo().get_remote(payload.remote):
        raise HTTPException(status_code=404, detail=f"No remote '{payload.remote}'")
    if "manage" not in sites._repo().token_uses(payload.remote):
        raise HTTPException(status_code=409, detail=f"Give {payload.remote} a manage token to move a VM there")
    claim = vm_locks.claim_or_409(vm, "a move to another cluster")
    try:
        task_id = create_task("site_move", vm, username=user["username"])
    except BaseException:
        claim.release()
        raise
    target = f"{vm} -> {payload.remote}/{payload.node}"

    def work():
        try:
            moved = site_move.move(
                task_id, user["username"], vm, payload.remote, payload.node, payload.pools, payload.networks,
                start=payload.start, limit_mib=payload.limit_mib, name=payload.name,
            )  # fmt: skip
        except (site_move.MoveError, sites.SiteError, DaemonError) as e:
            detail = getattr(e, "message", None) or getattr(e, "detail", None) or str(e)
            log_action(user["username"], "site_move", target, "failed", detail, task_id=task_id)
            raise HTTPException(status_code=409, detail=detail) from None
        log_action(
            user["username"], "site_move", target, "success", f"defined there as {moved['name']}", task_id=task_id
        )
        return moved

    return answer_later(task_id, vm_locks.released_after(claim, work), name=vm)


# ---- The target's side: an incoming VM, asked by another cluster with an administrator's token ----


class Incoming(BaseModel):
    spec: dict = Field(description="The VM in Hyperlite's terms (app/core/site_spec.py), checked as a new VM")
    pools: list[str] = Field(min_length=1, max_length=16)
    allocated: list[int] = Field(min_length=1, max_length=16)


@router.post("/sites/incoming", status_code=201)
def site_incoming(payload: Incoming, user: dict = Depends(require_role("admin"))):
    """Prepare a VM another cluster sends: its specification checked, a new volume for each disk. {"incoming", "disks"}."""
    answer = privileged.ask(
        "transfer.prepare", user, "site_incoming", str(payload.spec.get("name"))[:64], refused=422,
        spec=payload.spec, pools=payload.pools, allocated=payload.allocated,
    )  # fmt: skip
    log_action(user["username"], "site_incoming", str(payload.spec.get("name"))[:64], "success", answer["incoming"])
    return answer


@router.put("/sites/incoming/{incoming}/disks/{dev}")
async def site_incoming_disk(incoming: str, dev: str, request: Request, user: dict = Depends(require_role("admin"))):
    """One disk of an incoming VM, as the stream of app/core/site_stream.py: every frame checked before it is
    written; refused whole (422) on the first one that breaks a rule. {"digest"}."""
    from app.core import site_receive

    try:
        result, fds = await run_in_threadpool(
            privileged.run_fds, "transfer.import", user=user["username"], incoming=incoming, dev=dev
        )
    except DaemonError as e:
        raise privileged.http_error(e) from None
    try:
        digest = await site_receive.receive(request.stream(), fds[0], result["size"])
    except site_receive.ReceiveError as e:
        log_action(user["username"], "site_incoming_disk", f"{incoming[:8]}/{dev}", "failed", str(e))
        raise HTTPException(status_code=422, detail=str(e)) from None
    finally:
        for fd in fds[1:]:
            os.close(fd)
    return {"digest": digest}


class IncomingFinish(BaseModel):
    networks: list[str] = Field(default_factory=list, max_length=16)
    start: bool = False


@router.post("/sites/incoming/{incoming}/finish")
def site_incoming_finish(incoming: str, payload: IncomingFinish, user: dict = Depends(require_role("admin"))):
    """Define the incoming VM once its disks were written, with the networks chosen for its cards."""
    answer = privileged.ask(
        "transfer.define", user, "site_incoming_finish", incoming[:8], refused=409,
        incoming=incoming, networks=payload.networks, start=payload.start,
    )  # fmt: skip
    log_action(user["username"], "site_incoming_finish", answer["name"], "success", "defined from another cluster")
    return answer


@router.delete("/sites/incoming/{incoming}")
def site_incoming_cancel(incoming: str, user: dict = Depends(require_role("admin"))):
    """The incoming VM cancelled by the cluster that sent it: its volumes removed."""
    privileged.ask("transfer.abort", user, "site_incoming_cancel", incoming[:8], incoming=incoming)
    log_action(user["username"], "site_incoming_cancel", incoming[:8], "success", "")
    return {"cancelled": incoming}


# Last: /sites/<anything>/<anything> would take the routes above.
@router.get("/sites/{remote}/{what}")
def site_list(remote: str, what: str, node: str | None = None, user: dict = Depends(require_role("admin"))):
    """One remote's VMs, nodes, storage pools or networks (`node`: those of that node), as its own API answers them."""
    try:
        return sites.remote_list(remote, what, node)
    except sites.SiteError as e:
        raise _error(e) from None
