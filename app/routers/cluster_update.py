"""Rolling update of the cluster (app/core/rolling_update.py): the plan shown first, then the run."""

import threading
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import cluster_lead, rolling_update, version
from app.core.audit import log_action
from app.core.security import require_role
from app.core.tasks import create_task

router = APIRouter(prefix="/cluster/update", tags=["cluster"])

RUN_WINDOW = timedelta(hours=6)
_start_lock = threading.Lock()


def _local_check():
    """This node's update check and channel, as the other nodes answer them over their API."""
    from app.core import host_system
    from app.routers import update

    return update.check_update(lang="en", user={"username": "system", "role": "admin"}), host_system.channel()


def _running():
    from app.repositories import registry

    cutoff = (datetime.now(UTC) - RUN_WINDOW).isoformat()
    return registry.tasks().sync.latest_running("cluster_update", cutoff)


@router.get("/plan")
def get_plan(user: dict = Depends(require_role("admin"))):
    """What a rolling update would do on each node, and what prevents starting it. Each node reads its repositories:
    this takes a few seconds."""
    if cluster_lead.mode() != "cluster":
        raise HTTPException(status_code=409, detail="This node is not in a cluster")
    return rolling_update.cluster_plan(user["username"], _local_check)


class RollingRequest(BaseModel):
    # VMs that cannot move (local disks, passthrough): stopped for their node's reboot (True), or that node's reboot is
    # left for later (False). They keep running through the package update either way.
    arreter_bloquees: bool = False


@router.post("", status_code=202)
def start_rolling_update(payload: RollingRequest, user: dict = Depends(require_role("admin"))):
    if cluster_lead.mode() != "cluster":
        raise HTTPException(status_code=409, detail="This node is not in a cluster")
    from app.routers import update

    with _start_lock:
        if _running() is not None or update._update_in_progress():
            raise HTTPException(status_code=409, detail="An update is already running on this node")
        plan = rolling_update.cluster_plan(user["username"], _local_check)
        if plan["problemes"]:
            raise HTTPException(status_code=409, detail="; ".join(plan["problemes"]))
        if not plan["a_faire"]:
            raise HTTPException(status_code=409, detail="Every node is up to date")
        task_id = create_task("cluster_update", "cluster", node=None, username=user["username"])

    def start_local(username):
        return update._start_update({"username": username}, True)["task_id"]

    def restarts():
        return update._dpkg_installed_version() != version.STARTUP_VERSION

    log_action(user["username"], "cluster_update", "cluster", "success", f"started on {len(plan['noeuds'])} nodes")
    threading.Thread(
        target=rolling_update.run,
        args=(task_id, user["username"], payload.arreter_bloquees, start_local, restarts, _local_check),
        name="rolling-update",
        daemon=True,
    ).start()
    return {"task_id": task_id, "noeuds": [n["nom"] for n in plan["noeuds"]]}
