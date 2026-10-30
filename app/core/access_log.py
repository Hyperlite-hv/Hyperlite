"""Hyperlite's own access log (#448, finding F4).

uvicorn wrote one line per request into the service journal: the client's address, which the GDPR counts as personal
data, kept as long as the system keeps its journal, which Hyperlite does not decide. The lines now go to
/var/log/hyperlite/access.log instead (the API's user and the adm group), one file per day, each removed after days() days:
Activity > Audit log > Retention, 30 by default, 0 for no access log at all. The setting is the cluster's; every node
applies it within REFRESH_S. Query strings are left out: they only carry one-time tickets, which have no place in a log.
"""

import contextlib
import grp
import logging
import logging.handlers
import os
import pwd
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core import api_group

logger = logging.getLogger(__name__)

KEY = "access_log_days"
DEFAULT_DAYS = 30
MAX_DAYS = 3650
REFRESH_S = 600
FILE_NAME = "access.log"
_UVICORN = "uvicorn.access"

_lock = threading.Lock()
_handler = None


def log_dir():
    # The test suite points this elsewhere (tests/conftest.py).
    return Path(os.environ.get("HYPERLITE_ACCESS_LOG_DIR") or "/var/log/hyperlite")


def _store():
    from app.repositories import registry

    return registry.settings().sync


def days():
    try:
        value = int(_store().app_setting(KEY) or DEFAULT_DAYS)
    except (TypeError, ValueError):
        return DEFAULT_DAYS
    return min(max(value, 0), MAX_DAYS)


def set_days(value):
    if not isinstance(value, int) or not 0 <= value <= MAX_DAYS:
        raise ValueError(f"The access log is kept between 0 (none) and {MAX_DAYS} days")
    _store().set_app_setting(KEY, str(value))
    apply()


class _WithoutQuery(logging.Filter):
    """uvicorn's record: (client, method, path with its query string, HTTP version, status). The query goes."""

    def filter(self, record):
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            record.args = (args[0], args[1], args[2].split("?", 1)[0], args[3], args[4])
        return True


def _owners():
    """(uid, gid) the log's files and directory belong to: the API's user, which writes them (www-data from 4e), and
    the adm group, which reads /var/log; None when this process may not give them (it is not root) or they are missing."""
    if os.geteuid() != 0:
        return None
    try:
        return pwd.getpwnam(api_group.NAME).pw_uid, grp.getgrnam("adm").gr_gid
    except KeyError:
        return None


class _PrivateFileHandler(logging.handlers.TimedRotatingFileHandler):
    """Daily files readable by their writer and the adm group only, as /var/log's own. The directory is setgid adm, so
    a file the API makes as www-data is adm's too."""

    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o640)
        if owners := _owners():
            with contextlib.suppress(OSError):
                os.fchown(fd, *owners)
        return os.fdopen(fd, "a", encoding=self.encoding or "utf-8")


def purge(keep_days=None, now=None):
    """Remove the daily files older than the retention (all of them for 0). Returns how many were removed."""
    keep = days() if keep_days is None else keep_days
    folder = log_dir()
    if not folder.is_dir():
        return 0
    limit = ((now or datetime.now(UTC)) - timedelta(days=keep)).timestamp()
    removed = 0
    for path in folder.glob(f"{FILE_NAME}*"):
        if keep == 0 or (path.name != FILE_NAME and path.stat().st_mtime < limit):
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def apply():
    """Point uvicorn's access lines at the daily files kept days() days, or nowhere for 0; never into the journal."""
    global _handler
    keep = days()
    access = logging.getLogger(_UVICORN)
    with _lock:
        for handler in list(access.handlers):
            access.removeHandler(handler)
            if handler is not _handler:
                handler.close()
        access.propagate = False
        if keep == 0:
            if _handler is not None:
                _handler.close()
                _handler = None
        else:
            if _handler is None:
                folder = log_dir()
                folder.mkdir(parents=True, exist_ok=True, mode=0o750)
                if owners := _owners():  # installer/hyperlite.tmpfiles makes it so at boot
                    with contextlib.suppress(OSError):
                        os.chown(folder, *owners)
                        os.chmod(folder, 0o2750)  # noqa: S103 (setgid adm; nobody else reads it)
                _handler = _PrivateFileHandler(folder / FILE_NAME, when="midnight", backupCount=keep, utc=True)
                _handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
                _handler.addFilter(_WithoutQuery())
            _handler.backupCount = keep
            access.addHandler(_handler)
            access.setLevel(logging.INFO)  # uvicorn's own setting, which a reconfiguration of logging would drop
    try:
        purge(keep)
    except OSError:
        logger.warning("Old access log files could not be removed", exc_info=True)


def _loop(stop):
    while not stop.wait(REFRESH_S):
        try:
            apply()  # a change made on another node of the cluster, and the daily removal
        except Exception:
            logger.exception("The access log could not be refreshed")


def start():
    """At start: never fails the service. Without its directory, the access log is off and the journal says why."""
    try:
        apply()
    except Exception:
        logging.getLogger(_UVICORN).handlers.clear()
        logging.getLogger(_UVICORN).propagate = False
        logger.warning("The access log could not be set up: no access log is kept", exc_info=True)
    stop = threading.Event()
    threading.Thread(target=_loop, args=(stop,), daemon=True, name="access-log").start()
    return stop
