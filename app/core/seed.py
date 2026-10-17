import os
import secrets

from app.core.database import init_db
from app.core.security import hash_password


def seed_admin(must_change=None):
    """Create the admin account on a fresh node. `must_change`: its password is the random one of the installation,
    written in files and on the console banner, so it is changed at the first sign-in; the ISO's guided installation
    passes False when the administrator typed their own, and the end-to-end tests' throw-away backend sets
    HYPERLITE_INITIAL_ADMIN_MUST_CHANGE=0 (dashboard/e2e/start-backend.sh)."""
    from app.repositories import registry

    if must_change is None:
        must_change = os.environ.get("HYPERLITE_INITIAL_ADMIN_MUST_CHANGE", "1") != "0"

    init_db()
    accounts = registry.accounts().sync
    if accounts.get("admin"):
        return None
    # HYPERLITE_INITIAL_ADMIN_PASSWORD: set by the package post-install script
    # (a random value stored in .env). When it is not set, a random password is
    # generated here.
    password = os.environ.get("HYPERLITE_INITIAL_ADMIN_PASSWORD") or secrets.token_urlsafe(9)
    accounts.create("admin", hash_password(password), "admin")
    if must_change:
        accounts.flag_must_change("admin")
    return password
