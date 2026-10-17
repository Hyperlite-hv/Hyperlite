"""Backups being read right now (a restore, a verification, a file-restore session), so they are not deleted from
under the reader: by hand (refused, with what holds it) or by a schedule's retention (kept until its next run).

Found in the lab during the audit before 1.0.0 (#354): a backup deleted while it was being restored made the restore
fail half way; deleted during the copy, it could leave a VM half restored."""

import contextlib
import threading

_lock = threading.Lock()
_readers = {}  # backup id -> {what: count}


class BackupInUse(Exception):
    pass


@contextlib.contextmanager
def reading(backup_id, what):
    """Mark the backup as read for the duration of the block (`what`: "a restore", "a verification")."""
    with _lock:
        _readers.setdefault(backup_id, {})
        _readers[backup_id][what] = _readers[backup_id].get(what, 0) + 1
    try:
        yield
    finally:
        with _lock:
            left = _readers.get(backup_id, {})
            left[what] = left.get(what, 1) - 1
            if left.get(what, 0) <= 0:
                left.pop(what, None)
            if not left:
                _readers.pop(backup_id, None)


def holder(backup_id):
    """What reads this backup now ("a restore", "a file-restore session"…), or None."""
    with _lock:
        reasons = sorted(_readers.get(backup_id, {}))
    if not reasons:
        from app.core import file_restore

        if file_restore.is_open(backup_id):
            reasons = ["a file-restore session"]
    return " and ".join(reasons) or None


def refuse_if_in_use(backup_id):
    reason = holder(backup_id)
    if reason:
        raise BackupInUse(f"This backup is being read by {reason}: wait for it to end, then delete it")
