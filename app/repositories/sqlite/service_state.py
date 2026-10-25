"""The service_state table (app/core/service_state.py): what one of this node's services publishes for the others."""

import json
from datetime import UTC, datetime

from app.core.database import get_conn


class SqliteServiceStateStore:
    def publish(self, name, data):
        with get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO service_state (name, data, at) VALUES (?, ?, ?)",
                (name, json.dumps(data), datetime.now(UTC).isoformat()),
            )
            conn.commit()

    def read(self, name):
        with get_conn() as conn:
            row = conn.execute("SELECT data, at FROM service_state WHERE name = ?", (name,)).fetchone()
        return (json.loads(row["data"]), row["at"]) if row else (None, None)
