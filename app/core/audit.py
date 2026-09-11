from datetime import datetime, timezone
from app.core.database import get_conn


def log_action(username: str, action: str, resource: str, result: str, error_message: str = None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO audit_log (timestamp, username, action, resource, result, error_message) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), username, action, resource, result, error_message),
        )
        conn.commit()
