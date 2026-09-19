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
        # HYPERLITE_INITIAL_ADMIN_PASSWORD: used by the bootable installer (see
        # installer/postinstall.sh) so that the Hyperlite admin account shares the
        # password of the Linux root account created at install time, rather than
        # having two secrets to keep track of. Not set in normal use: behaviour is
        # unchanged and a random password is generated here.
        password = os.environ.get("HYPERLITE_INITIAL_ADMIN_PASSWORD") or secrets.token_urlsafe(9)
        conn.execute(
            "INSERT INTO users (username, hashed_password, role) VALUES (?, ?, ?)",
            ("admin", hash_password(password), "admin"),
        )
        conn.commit()
        return password
