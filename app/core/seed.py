import os
import secrets

from app.core.database import get_conn, init_db
from app.core.security import hash_password


def seed_admin():
    init_db()
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
        if row:
            return None
        # HYPERLITE_INITIAL_ADMIN_PASSWORD: set by the package post-install script
        # (a random value stored in .env). When it is not set, a random password is
        # generated here.
        password = os.environ.get("HYPERLITE_INITIAL_ADMIN_PASSWORD") or secrets.token_urlsafe(9)
        conn.execute(
            "INSERT INTO users (username, hashed_password, role) VALUES (?, ?, ?)",
            ("admin", hash_password(password), "admin"),
        )
        conn.commit()
        return password
