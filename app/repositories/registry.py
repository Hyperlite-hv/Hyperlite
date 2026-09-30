"""Which backend each repository uses. SQLite only in phase 1; a setting chooses etcd from phase 5."""

from functools import cache

from app.repositories.sqlite.nodes import SqliteNodeRepository


@cache
def nodes():
    return SqliteNodeRepository()
