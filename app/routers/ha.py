"""High-availability endpoints. Logic lives in app/core/ha.py (protection, manual recovery), ha_fencing.py (per-node
fencing settings, status test only) and ha_watch.py (the dry-run watcher). Recovery stays manual: the watcher only
says what automatic HA would have done (see docs/design/ha-automatic.md)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import ha, ha_fencing, ha_watch, maintenance
from app.core.audit import log_action
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
    maintenance.refuse_if_in_maintenance(payload.target_node, "HA recovery")
    try:
        ha.recover(vm_name, payload.target_node, user["username"])
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"message": f"'{vm_name}' recovered on '{payload.target_node}'"}


# --- Dry-run watcher, fencing settings, leases.


@router.get("/status")
def watch_status(user: dict = Depends(get_current_user)):
    return ha_watch.status()


class HaSettings(BaseModel):
    temoin: str = Field("", max_length=260)
    seuil_suspect: int = 3
    seuil_panne: int = 6


@router.put("/settings")
def put_settings(payload: HaSettings, user: dict = Depends(require_role("admin"))):
    try:
        result = ha_watch.save_settings(payload.temoin, payload.seuil_suspect, payload.seuil_panne)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    log_action(user["username"], "ha_settings", "ha", "succes", f"witness={payload.temoin or 'none'}")
    return result


def _known_node(node):
    if node != "local":
        from app.core.cluster import get_node

        if get_node(node) is None:
            raise HTTPException(status_code=404, detail=f"Node '{node}' not found")


@router.get("/fencing")
def list_fencing(user: dict = Depends(require_role("admin"))):
    return ha_fencing.list_all()


class FencingSettings(BaseModel):
    methode: str
    adresse: str | None = Field(None, max_length=253)
    port: int | None = None
    utilisateur: str | None = Field(None, max_length=64)
    # Absent or empty: keep the stored password.
    secret: str | None = Field(None, max_length=256)
    tls_non_verifie: bool = False


@router.put("/fencing/{node}")
def put_fencing(node: str, payload: FencingSettings, user: dict = Depends(require_role("admin"))):
    _known_node(node)
    try:
        result = ha_fencing.save(
            node,
            payload.methode,
            payload.adresse,
            payload.port,
            payload.utilisateur,
            payload.secret or None,
            payload.tls_non_verifie,
            user["username"],
        )
    except ha_fencing.FencingError as e:
        raise HTTPException(status_code=e.status, detail=e.message) from e
    log_action(user["username"], "ha_fencing_set", node, "succes", payload.methode)
    return result


@router.delete("/fencing/{node}")
def delete_fencing(node: str, user: dict = Depends(require_role("admin"))):
    if not ha_fencing.delete(node):
        raise HTTPException(status_code=404, detail="No fencing is set for this node")
    log_action(user["username"], "ha_fencing_delete", node, "succes")
    return {"message": "Fencing settings removed"}


@router.post("/fencing/{node}/test")
def test_fencing(node: str, user: dict = Depends(require_role("admin"))):
    """Reads the power state through the BMC or AMT. Never powers anything off."""
    try:
        result = ha_fencing.test(node)
    except ha_fencing.FencingError as e:
        raise HTTPException(status_code=e.status, detail=e.message) from e
    log_action(user["username"], "ha_fencing_test", node, "succes" if result["ok"] else "echec", result["detail"][:300])
    return result


@router.get("/leases/{node}")
def lease_check(node: str, user: dict = Depends(require_role("admin"))):
    _known_node(node)
    return ha_watch.lease_check(node)
