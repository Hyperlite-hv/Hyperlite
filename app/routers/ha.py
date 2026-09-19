"""High-availability endpoints. Logic lives in app/core/ha.py; see its module
docstring for the deliberately cautious scope (no fencing, recovery is always
triggered by an admin and never automatic)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import ha
from app.core.database import get_conn
from app.core.security import get_current_user, require_role

router = APIRouter(prefix="/ha", tags=["ha"])


def _node_statut(node_label):
    if node_label == "local":
        return "en_ligne"  # the local host, which runs Hyperlite itself, is reachable by definition
    with get_conn() as conn:
        row = conn.execute("SELECT statut FROM nodes WHERE name = ?", (node_label,)).fetchone()
    return row["statut"] if row else "inconnu"


@router.get("")
def list_protected(user: dict = Depends(get_current_user)):
    return [{**row, "statut_noeud": _node_statut(row["node"])} for row in ha.list_protected()]


class EnableRequest(BaseModel):
    node: str | None = None


@router.post("/{vm_name}/enable", status_code=201)
def enable(vm_name: str, payload: EnableRequest, user: dict = Depends(require_role("admin"))):
    try:
        ha.enable_protection(vm_name, payload.node, user["username"])
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return ha.get_protected(vm_name)


@router.delete("/{vm_name}")
def disable(vm_name: str, user: dict = Depends(require_role("admin"))):
    if not ha.get_protected(vm_name):
        raise HTTPException(status_code=404, detail=f"'{vm_name}' is not HA-protected")
    ha.disable_protection(vm_name, user["username"])
    return {"message": f"HA protection disabled for '{vm_name}'"}


class RecoverRequest(BaseModel):
    target_node: str


@router.post("/{vm_name}/recover", status_code=202)
def recover(vm_name: str, payload: RecoverRequest, user: dict = Depends(require_role("admin"))):
    try:
        ha.recover(vm_name, payload.target_node, user["username"])
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"message": f"'{vm_name}' recovered on '{payload.target_node}'"}
