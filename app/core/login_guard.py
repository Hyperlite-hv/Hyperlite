"""Brute-force lock shared by every check of a password or a 2FA code (sign-in, 2FA step, password change,
2FA removal). Failures are kept in the database, not in memory, so restarting the service no longer wipes
them: an attacker cannot reset the lock by making the service restart (an update, a crash, a reboot)."""

import time

from app.core.database import get_conn

# Rows older than this are deleted when a new failure is written. It must stay longer than every window
# the callers pass to recent_failures().
KEEP_S = 3600


def recent_failures(kind, key, window_s):
    """Number of failures recorded for (kind, key) within the last window_s seconds."""
    since = time.time() - window_s
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM login_failures WHERE kind = ? AND key = ? AND at > ?", (kind, key, since)
        ).fetchone()
    return row[0]


def record_failure(kind, key):
    now = time.time()
    with get_conn() as conn:
        conn.execute("INSERT INTO login_failures (kind, key, at) VALUES (?, ?, ?)", (kind, key, now))
        conn.execute("DELETE FROM login_failures WHERE at < ?", (now - KEEP_S,))
        conn.commit()


def clear_failures(kind, key):
    with get_conn() as conn:
        conn.execute("DELETE FROM login_failures WHERE kind = ? AND key = ?", (kind, key))
        conn.commit()
