from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.core import k8s_cluster
from app.core.audit import log_action
from app.core.security import get_current_user, require_role

router = APIRouter(prefix="/kubernetes", tags=["kubernetes"])


class ClusterCreate(BaseModel):
    name: str
    workers: int = Field(2, ge=1, le=k8s_cluster.MAX_WORKERS)
    vcpu: int = Field(2, ge=1)
    memory_mb: int = Field(2048, ge=1)
    disk_gb: int = Field(20, ge=1)
    network: str = "default"


def _fail(e):
    raise HTTPException(status_code=e.status, detail=str(e)) from e


@router.get("/clusters")
def list_clusters(user: dict = Depends(get_current_user)):
    return k8s_cluster.list_clusters()


@router.post("/clusters", status_code=202)
def create_cluster(payload: ClusterCreate, user: dict = Depends(require_role("admin"))):
    """Checks everything first, then builds the cluster in the background (one create_k8s_cluster task)."""
    try:
        task_id = k8s_cluster.start_create(
            payload.name, payload.workers, payload.vcpu, payload.memory_mb, payload.disk_gb, payload.network,
            user["username"],
        )  # fmt: skip
    except k8s_cluster.ClusterError as e:
        _fail(e)
    return {"name": payload.name, "task_id": task_id}


@router.get("/clusters/{name}/kubeconfig", response_class=PlainTextResponse)
def download_kubeconfig(name: str, user: dict = Depends(require_role("admin"))):
    """The cluster's admin credentials: administrators only, and every download is audited."""
    try:
        config = k8s_cluster.get_kubeconfig(name)
    except k8s_cluster.ClusterError as e:
        _fail(e)
    log_action(user["username"], "download_kubeconfig", name, "success")
    return PlainTextResponse(
        config,
        media_type="application/yaml",
        headers={"Content-Disposition": f'attachment; filename="{name}.kubeconfig"'},
    )


@router.delete("/clusters/{name}")
def delete_cluster(name: str, confirm: bool = False, user: dict = Depends(require_role("admin"))):
    if not confirm:
        raise HTTPException(
            status_code=400, detail="Irreversible action: add ?confirm=true to delete the cluster and its VMs"
        )
    try:
        k8s_cluster.delete_cluster(name, user["username"])
    except k8s_cluster.ClusterError as e:
        _fail(e)
    return {"name": name, "deleted": True}
