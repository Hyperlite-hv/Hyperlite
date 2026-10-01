"""Which backend each repository uses. SQLite only in phase 1; a setting chooses etcd from phase 5."""

from functools import cache

from app.repositories.sqlite.audit import SqliteAuditRepository
from app.repositories.sqlite.automation import SqliteAutomationRepository
from app.repositories.sqlite.backups import SqliteBackupRepository
from app.repositories.sqlite.metrics import SqliteMetricsRepository
from app.repositories.sqlite.nodes import SqliteNodeRepository
from app.repositories.sqlite.tasks import SqliteTaskRepository


@cache
def nodes():
    return SqliteNodeRepository()


@cache
def tasks():
    return SqliteTaskRepository()


@cache
def audit():
    return SqliteAuditRepository()


@cache
def metrics():
    return SqliteMetricsRepository()


@cache
def automation():
    return SqliteAutomationRepository()


@cache
def backups():
    return SqliteBackupRepository()
