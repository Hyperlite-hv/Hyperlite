import os
import secrets

from app.core.database import init_db
from app.core.security import hash_password


def seed_admin():
    from app.repositories import registry

    init_db()
    accounts = registry.accounts().sync
    if accounts.get("admin"):
        return None
    # HYPERLITE_INITIAL_ADMIN_PASSWORD: set by the package post-install script
    # (a random value stored in .env). When it is not set, a random password is
    # generated here.
    password = os.environ.get("HYPERLITE_INITIAL_ADMIN_PASSWORD") or secrets.token_urlsafe(9)
    accounts.create("admin", hash_password(password), "admin")
    return password
