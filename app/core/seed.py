import logging
import os
import secrets

from app.core import paths
from app.core.database import init_db
from app.core.security import hash_password

logger = logging.getLogger(__name__)


def in_daemon():
    """Whether hyperlite-daemon does the accounts' start (first_start) at its own: always on a node. The API does it
    only when it runs the daemon's operations itself (HYPERLITE_DAEMON_INPROCESS: the test suite, the end-to-end
    backend), with no daemon of its own."""
    from app.core import privileged

    return os.environ.get(privileged.INPROCESS_ENV) != "1"


def first_start():
    """What a node does for its accounts at start: the admin account on a fresh node, its random password in a
    root-only file; the initial password's checks (app/core/initial_password.py); the second factors' secrets an
    older version stored in clear, sealed. hyperlite-daemon runs it (app/daemon/__main__.py), as only it writes the
    accounts (docs/design/privilege-separation.md, 14.1)."""
    from app.core import initial_password, twofa

    password = seed_admin()
    if password:
        # Never logged: a root-only file, and only where it is in the log.
        pw_file = paths.INITIAL_PASSWORD_FILE
        pw_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(pw_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(password + "\n")
        logger.warning("=== Admin account created: the initial password is in %s ===", pw_file)
    initial_password.start()
    twofa.encrypt_stored_secrets()


def _installation_password():
    """HYPERLITE_INITIAL_ADMIN_PASSWORD: the API's environment (its unit reads the node's env file), or that file
    itself, which hyperlite-daemon's unit does not load."""
    from app.core import initial_password

    if os.environ.get("HYPERLITE_INITIAL_ADMIN_PASSWORD"):
        return os.environ["HYPERLITE_INITIAL_ADMIN_PASSWORD"]
    try:
        lines = initial_password._paths()[1].read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith(initial_password.ENV_LINE):
            return line[len(initial_password.ENV_LINE) :].strip().strip("'\"") or None
    return None


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
    password = _installation_password() or secrets.token_urlsafe(9)
    accounts.create("admin", hash_password(password), "admin")
    if must_change:
        accounts.flag_must_change("admin")
    return password
