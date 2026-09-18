"""Endpoints de gestion multi-nœuds (chantier 15). Logique de connexion
distante dans app/core/cluster.py."""
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.cluster import get_cluster_pubkey, node_summary, register_node, remove_node
from app.core.database import get_conn
from app.core.security import get_current_user, require_role
from app.core.host_capabilities import get_remote_capabilities
from app.core.error_messages import describe_exception

router = APIRouter(prefix="/nodes", tags=["nodes"])

NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.-]{1,62}$")


@router.get("")
def list_nodes(user: dict = Depends(get_current_user)):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM nodes ORDER BY name").fetchall()
    return [dict(r) for r in rows]


@router.get("/cluster-pubkey")
def get_cluster_key(user: dict = Depends(require_role("admin"))):
    """Clé publique à installer dans ~/.ssh/authorized_keys du nœud distant
    avant de l'enregistrer -- affichée côté UI pour copier/coller."""
    return {"public_key": get_cluster_pubkey()}


class NodeCreate(BaseModel):
    name: str
    hostname: str
    ssh_user: str = "root"
    ssh_port: int = Field(22, ge=1, le=65535)


@router.post("", status_code=201)
def add_node(payload: NodeCreate, user: dict = Depends(require_role("admin"))):
    if not NAME_RE.match(payload.name):
        raise HTTPException(status_code=422, detail="Nom de nœud invalide (lettres/chiffres/-/., 2-63 caractères)")
    try:
        node = register_node(payload.name, payload.hostname, payload.ssh_user, payload.ssh_port, user["username"])
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return node


@router.get("/{name}/summary")
def get_node_summary(name: str, user: dict = Depends(get_current_user)):
    with get_conn() as conn:
        node = conn.execute("SELECT id FROM nodes WHERE name = ?", (name,)).fetchone()
    if not node:
        raise HTTPException(status_code=404, detail=f"Nœud '{name}' introuvable")
    try:
        return node_summary(name)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Nœud injoignable : {e}")


@router.get("/{name}/capabilities")
def get_node_capabilities(name: str, user: dict = Depends(get_current_user)):
    """Profil de capacites d'un nœud DISTANT enregistre (mandat
    portabilite 2026-09-18, chantier 1) -- equivalent de GET
    /host/capabilities pour l'hote local. Voir app/core/
    host_capabilities.py::get_remote_capabilities pour les limites
    connues (certaines informations OS necessitent la confiance SSH
    cluster deja etablie, degradees a `null` sinon plutot qu'un echec)."""
    with get_conn() as conn:
        node = conn.execute("SELECT id FROM nodes WHERE name = ?", (name,)).fetchone()
    if not node:
        raise HTTPException(status_code=404, detail=f"Nœud '{name}' introuvable")
    try:
        return get_remote_capabilities(name)
    except Exception as e:
        msg = describe_exception(e)
        raise HTTPException(status_code=502, detail=f"Nœud injoignable : {msg}")


@router.delete("/{name}")
def delete_node(name: str, user: dict = Depends(require_role("admin"))):
    with get_conn() as conn:
        node = conn.execute("SELECT id FROM nodes WHERE name = ?", (name,)).fetchone()
    if not node:
        raise HTTPException(status_code=404, detail=f"Nœud '{name}' introuvable")
    remove_node(name, user["username"])
    return {"message": f"Nœud '{name}' retiré"}
