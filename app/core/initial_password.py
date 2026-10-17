"""The admin account's random initial password (installer/deb/postinst, app/core/seed.py). Until it is changed it is
kept where the administrator of a fresh node finds it: a root-only file, the service's .env and the console banner
(scripts/write-motd.sh). Once the admin account has a password of its own, every copy goes: a password left in a file
is one more way in for anyone who later reads that file or a backup of it."""

import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parents[2]
ENV_LINE = "HYPERLITE_INITIAL_ADMIN_PASSWORD="


def _root():
    # The test suite points this elsewhere (tests/conftest.py): never the host's own files.
    return Path(os.environ.get("HYPERLITE_INITIAL_PASSWORD_ROOT", "/"))


def _paths():
    root = _root()
    app = root / APP_DIR.relative_to("/")
    return [root / "root/.hyperlite-initial-password", app / "data/initial-admin-password.txt"], app / ".env"


def forget():
    """Remove every copy of the initial password. Never raises: a password change must not fail on it."""
    files, env = _paths()
    for path in files:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "A copy of the initial password could not be removed", exc_info=True
            )  # the error names the file
    try:
        lines = env.read_text().splitlines(keepends=True)
    except FileNotFoundError:
        return
    except OSError:
        logger.warning("The initial password could not be removed from the service's .env", exc_info=True)
        return
    kept = [line for line in lines if not line.startswith(ENV_LINE)]
    if kept == lines:
        return
    tmp = env.with_name(env.name + ".tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.writelines(kept)
        os.replace(tmp, env)
    except OSError:
        logger.warning("The initial password could not be removed from the service's .env", exc_info=True)
        tmp.unlink(missing_ok=True)


CHECK_S = 600


def settle():
    """At start, then every CHECK_S: an installation from before these rules may still keep the initial password.
    Already changed, its copies go; still the admin account's password, it is changed at the next sign-in. In a
    cluster the password is changed on one node: the others find their own copy stale at their next check (seen in
    the lab: hlc1 kept its file after the change made on hlc2). Never raises: a node without the quorum cannot write
    the flag, and must run all the same (it is tried again at the next check)."""
    try:
        _settle()
    except Exception:
        logger.warning("The initial admin password could not be checked", exc_info=True)


def start():
    settle()

    def loop():
        while not _stop.wait(CHECK_S):
            if not _paths()[0][0].exists():
                return  # nothing left to check on this node
            settle()

    threading.Thread(target=loop, daemon=True, name="initial-password").start()


_stop = threading.Event()


def _settle():
    files, _env = _paths()
    try:
        password = files[0].read_text().strip()
    except OSError:
        return
    from app.core.security import verify_password
    from app.repositories import registry

    accounts = registry.accounts().sync
    admin = accounts.get("admin")
    if admin is None:
        return
    if password and verify_password(password, admin["hashed_password"]):
        if not admin.get("must_change_password"):
            accounts.flag_must_change("admin")
            logger.warning("The admin account still has its initial password: it is changed at the next sign-in")
    else:
        forget()
        logger.info("The initial admin password, already changed, was removed from the disk")
