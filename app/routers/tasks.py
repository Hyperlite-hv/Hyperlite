from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.database import get_conn
from app.core.security import get_current_user

router = APIRouter(prefix="/tasks", tags=["tasks"])

# Colonnes autorisees en tri : whitelist plutot que d'interpoler `tri`
# directement dans le SQL (c'est un parametre de requete arbitraire).
_SORTABLE = {"cree_le", "debut_le", "fin_le", "statut", "type", "cible", "username"}


@router.get("")
def list_tasks(
    statut: Optional[str] = None,
    type: Optional[str] = None,
    username: Optional[str] = None,
    cible: Optional[str] = None,
    node: Optional[str] = None,
    depuis: Optional[str] = None,  # ISO 8601, filtre cree_le >= depuis
    tri: str = "cree_le",
    ordre: str = "desc",
    limit: int = Query(200, ge=1, le=1000),
    user: dict = Depends(get_current_user),
):
    tri = tri if tri in _SORTABLE else "cree_le"
    ordre_sql = "ASC" if ordre.lower() == "asc" else "DESC"

    clauses, params = [], []
    if statut:
        clauses.append("statut = ?"); params.append(statut)
    if type:
        clauses.append("type = ?"); params.append(type)
    if username:
        clauses.append("username = ?"); params.append(username)
    if node:
        clauses.append("node = ?"); params.append(node)
    if cible:
        clauses.append("cible LIKE ?"); params.append(f"%{cible}%")
    if depuis:
        clauses.append("cree_le >= ?"); params.append(depuis)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM tasks {where} ORDER BY {tri} {ordre_sql} LIMIT ?", params,
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/{task_id}")
def get_task(task_id: str, user: dict = Depends(get_current_user)):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Tâche introuvable")
        task = dict(row)
        # Rattache les entrees audit_log de la meme cible pour le detail
        # complet au clic (raison precise d'un echec, actions liees...).
        logs = conn.execute(
            "SELECT timestamp, action, result, error_message FROM audit_log "
            "WHERE resource = ? ORDER BY id DESC LIMIT 20",
            (task["cible"],),
        ).fetchall()
    task["logs"] = [dict(l) for l in logs]
    return task
