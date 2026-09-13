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
