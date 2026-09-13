"""Suivi persiste des taches (creation VM, demarrage/arret, upload ISO, ...),
sur le modele du "Recent Tasks" de vCenter : chaque tache porte une heure de
creation, une heure de debut et une heure de fin distinctes, avec la duree
totale derivable des deux dernieres.

Aujourd'hui tous les endpoints sont synchrones (la requete HTTP fait le
travail elle-meme) : cree_le == debut_le au moment de create_task(). Le
decoupage existe des maintenant pour que les futurs jobs asynchrones
(kickstart, backups planifies, moteur d'automation) puissent poser cree_le a
la soumission puis debut_le plus tard, quand un worker prend reellement la
main dessus -- sans avoir a retoucher ce schema.
"""
import uuid
from datetime import datetime, timezone

from app.core.database import get_conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def create_task(type_, cible=None, node=None, username=None):
    """Cree une tache et la marque immediatement 'en_cours' (voir docstring
    du module : pas encore de vraie file d'attente aujourd'hui)."""
    task_id = str(uuid.uuid4())
    now = _now()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO tasks (id, type, cible, node, username, statut, progres, cree_le, debut_le) "
            "VALUES (?, ?, ?, ?, ?, 'en_cours', 0, ?, ?)",
            (task_id, type_, cible, node, username, now, now),
        )
        conn.commit()
    return task_id


def update_task_progress(task_id, progres):
    with get_conn() as conn:
        conn.execute("UPDATE tasks SET progres = ? WHERE id = ?", (progres, task_id))
        conn.commit()


def _finish_task_in(conn, task_id, statut, error_message=None):
    """Cloture une tache sur une connexion deja ouverte -- utilise par
    log_action() pour que l'ecriture audit_log + tasks reste dans le meme
    commit plutot que d'ouvrir une seconde connexion SQLite par appel."""
    conn.execute(
        "UPDATE tasks SET statut = ?, progres = 100, fin_le = ?, erreur = ? WHERE id = ?",
        (statut, _now(), error_message, task_id),
    )


def finish_task(task_id, statut, error_message=None):
    with get_conn() as conn:
        _finish_task_in(conn, task_id, statut, error_message)
        conn.commit()
