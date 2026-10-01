"""The audit log as the API shows it (administrators only, checked by the router). Writes stay in app/core/audit.py,
whose single writer thread owns the table."""

from app.repositories import registry


def filters(action=None, result=None, username=None, resource=None, depuis=None, jusqu_a=None):
    return {
        "action": action,
        "result": result,
        "username": username,
        "resource": resource,
        "depuis": depuis,
        "jusqu_a": jusqu_a,
    }


async def query(f, limit):
    return await registry.audit().query(f, max(1, min(limit, 1000)))


async def count(f):
    return await registry.audit().count(f)


async def actions():
    return await registry.audit().actions()


def export_rows(f):
    return registry.audit().export_rows(f)
