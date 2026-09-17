"""Endpoints HA (chantier 17). Logique dans app/core/ha.py -- voir sa
docstring de module pour le scope volontairement prudent (pas de fencing,
recuperation toujours declenchee par un admin, jamais automatique)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import ha
from app.core.database import get_conn
from app.core.security import get_current_user, require_role

router = APIRouter(prefix="/ha", tags=["ha"])


def _node_statut(node_label):
    if node_label == "kvm-lab":
        return "en_ligne"  # l'hote local, ou tourne Hyperlite lui-meme, est par definition joignable
    with get_conn() as conn:
        row = conn.execute("SELECT statut FROM nodes WHERE name = ?", (node_label,)).fetchone()
    return row["statut"] if row else "inconnu"


@router.get("")
def list_protected(user: dict = Depends(get_current_user)):
    return [
        {**row, "statut_noeud": _node_statut(row["node"])}
        for row in ha.list_protected()
    ]


class EnableRequest(BaseModel):
    node: str | None = None


@router.post("/{vm_name}/enable", status_code=201)
def enable(vm_name: str, payload: EnableRequest, user: dict = Depends(require_role("admin"))):
    try:
        ha.enable_protection(vm_name, payload.node, user["username"])
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return ha.get_protected(vm_name)


@router.delete("/{vm_name}")
def disable(vm_name: str, user: dict = Depends(require_role("admin"))):
    if not ha.get_protected(vm_name):
        raise HTTPException(status_code=404, detail=f"'{vm_name}' n'est pas protégée par la HA")
    ha.disable_protection(vm_name, user["username"])
    return {"message": f"Protection HA désactivée pour '{vm_name}'"}


class RecoverRequest(BaseModel):
    target_node: str


@router.post("/{vm_name}/recover", status_code=202)
def recover(vm_name: str, payload: RecoverRequest, user: dict = Depends(require_role("admin"))):
    try:
        ha.recover(vm_name, payload.target_node, user["username"])
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"message": f"'{vm_name}' récupérée sur '{payload.target_node}'"}
