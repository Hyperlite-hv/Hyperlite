from datetime import datetime, timezone
from app.core.database import get_conn
from app.core.tasks import _finish_task_in


def log_action(username: str, action: str, resource: str, result: str, error_message: str = None, task_id: str = None):
    """task_id : quand fourni (voir app.core.tasks.create_task), cloture aussi
    la tache correspondante dans le meme commit -- un log_action(..., "succes")
    ou (..., "echec") represente deja la fin de la tache pour tous les
    endpoints synchrones actuels, pas la peine de dupliquer l'appel."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO audit_log (timestamp, username, action, resource, result, error_message) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), username, action, resource, result, error_message),
        )
        if task_id:
            _finish_task_in(conn, task_id, "termine" if result == "succes" else "echec", error_message)
        conn.commit()

    # Notifications sortantes (chantier 28) : point d'entree UNIQUE plutot
    # que d'appeler notify() a chaque site d'appel de log_action() dans
    # tout le code -- couvre automatiquement toute action deja loggee
    # (node_statut_change, ha_alert, create_vm...) sans y toucher. Import
    # tardif : evite tout risque de cycle (notifications.py ne depend que
    # de database.py, mais log_action() est appelee depuis a peu pres
    # partout dans l'app -- plus sur de ne pas l'importer au niveau module).
    from app.core.notifications import notify, NOTIFY_EVENTS
    if action in NOTIFY_EVENTS:
        title = f"{NOTIFY_EVENTS[action]} — {resource}"
        message = error_message or f"{action} sur '{resource}' : {result}"
        notify(action, title, message, result)
