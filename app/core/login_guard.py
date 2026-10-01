"""Brute-force lock shared by every check of a password or a 2FA code (sign-in, 2FA step, password change,
2FA removal). Failures are kept in the database, not in memory, so restarting the service no longer wipes
them: an attacker cannot reset the lock by making the service restart (an update, a crash, a reboot)."""

import time

# Rows older than this are deleted when a new failure is written. It must stay longer than every window
# the callers pass to recent_failures().
KEEP_S = 3600


def _accounts():
    # The account repository's synchronous bridge: signing in runs in FastAPI dependencies and sync endpoints.
    from app.repositories import registry

    return registry.accounts().sync


def recent_failures(kind, key, window_s):
    """Number of failures recorded for (kind, key) within the last window_s seconds."""
    return _accounts().failures_since(kind, key, time.time() - window_s)


def record_failure(kind, key):
    now = time.time()
    _accounts().record_failure(kind, key, now, now - KEEP_S)


def clear_failures(kind, key):
    _accounts().clear_failures(kind, key)
