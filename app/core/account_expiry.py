"""Accounts that end (a contractor, an intern): from `expires_at` on, the account is refused at sign-in and on every
request, its sessions and API tokens with it. Its rights and files stay, so moving the date later brings it back."""

from datetime import UTC, datetime

from app.core.database import get_conn


def all_dates():
    with get_conn() as db:
        return {r["username"]: r["expires_at"] for r in db.execute("SELECT username, expires_at FROM account_expiry")}


def set_date(username, expires_at, by):
    with get_conn() as db:
        if expires_at is None:
            db.execute("DELETE FROM account_expiry WHERE username = ?", (username,))
        else:
            db.execute(
                "INSERT INTO account_expiry (username, expires_at, set_by) VALUES (?, ?, ?) ON CONFLICT(username) DO "
                "UPDATE SET expires_at = excluded.expires_at, set_by = excluded.set_by",
                (username, expires_at, by),
            )
        db.commit()


def expired(username, now=None):
    """The date the account ended, when it has; None otherwise (or when the table is not there yet)."""
    import sqlite3

    try:
        with get_conn() as db:
            row = db.execute("SELECT expires_at FROM account_expiry WHERE username = ?", (username,)).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    return row["expires_at"] if datetime.fromisoformat(row["expires_at"]) <= (now or datetime.now(UTC)) else None
