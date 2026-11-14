"""Protections on VMs, containers, pools and nodes (docs/design/access-2.md, section 4).

- "keep": the object is not deleted (nor its disks or snapshots) while it holds;
- "freeze": nothing changes at all (configuration, power, snapshots); reading and the consoles stay open.

A protection on a pool holds for its member VMs, on a node for every VM and container that node hosts. Administrators
are held by them too: that is their point (a deletion by mistake, a pipeline gone wrong); they and the delegates of
the perimeter lift them, which is journaled.
"""

import re
from datetime import UTC, datetime

from app.core.database import get_conn

LEVELS = ("keep", "freeze")
KINDS = ("vm", "container", "pool", "node")

# The requests that stay open on a frozen VM or container: its consoles, which change nothing.
_FROZEN_ALLOWED = re.compile(
    r"^/(console-ticket|terminal-ticket|serial-ticket|spice-display|spice-ticket|tunnel-ticket)$"
)
# The requests a "keep" refuses: deleting the object, one of its disks or one of its snapshots.
_DELETIONS = re.compile(r"^(/disks/[^/]+|/snapshots/[^/]+)?$")
_GUEST = re.compile(r"^/api/v1/(vms|containers)/([^/]+)(/.*)?$")


def _now():
    return datetime.now(UTC).isoformat()


def _key(resource_type, resource_id, level):
    return f"{resource_type}:{resource_id}:{level}"


def list_all():
    with get_conn() as db:
        return [dict(r) for r in db.execute("SELECT * FROM resource_protections ORDER BY resource_type, resource_id")]


def put(resource_type, resource_id, level, reason, username):
    if resource_type not in KINDS:
        raise ValueError(f"A protection is put on one of: {', '.join(KINDS)}")
    if level not in LEVELS:
        raise ValueError(f"level must be one of: {', '.join(LEVELS)}")
    row = (
        _key(resource_type, resource_id, level),
        resource_type,
        str(resource_id),
        level,
        reason or "",
        username,
        _now(),
    )
    with get_conn() as db:
        db.execute(
            "INSERT INTO resource_protections (id, resource_type, resource_id, level, reason, set_by, set_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET reason = excluded.reason, "
            "set_by = excluded.set_by, set_at = excluded.set_at",
            row,
        )
        db.commit()
    return dict(zip(("id", "resource_type", "resource_id", "level", "reason", "set_by", "set_at"), row, strict=True))


def lift(resource_type, resource_id, level):
    """True when there was one."""
    with get_conn() as db:
        cur = db.execute("DELETE FROM resource_protections WHERE id = ?", (_key(resource_type, resource_id, level),))
        db.commit()
    return cur.rowcount > 0


def forget(resource_type, resource_id):
    """The object is gone (a pool deleted): its protections with it."""
    with get_conn() as db:
        db.execute(
            "DELETE FROM resource_protections WHERE resource_type = ? AND resource_id = ?",
            (resource_type, str(resource_id)),
        )
        db.commit()


def holding(resource_type, resource_id):
    """The protections that hold on that object: its own, then its pools' (a VM) and its node's (the node that
    answers: a VM or a container is always asked about on the node that hosts it), each with `inherited_type`."""
    from app.core import permissions, self_node

    perimeters = [(resource_type, str(resource_id))]
    if resource_type == "vm":
        perimeters += [("pool", str(p)) for p in permissions.get_vm_pools(resource_id)]
    if resource_type in ("vm", "container"):
        perimeters.append(("node", self_node.name()))
    rows = list_all()
    found = []
    for kind, ident in perimeters:
        for r in rows:
            if (r["resource_type"], r["resource_id"]) == (kind, ident):
                own = (kind, ident) == (resource_type, str(resource_id))
                found.append({**r, "inherited_type": None if own else kind})
    return found


def refusal(resource_type, resource_id, deleting):
    """Why that object may not be changed (`deleting`: the change removes it, a disk or a snapshot), or None."""
    for p in holding(resource_type, resource_id):
        if p["level"] == "freeze" or (p["level"] == "keep" and deleting):
            where = "" if p["inherited_type"] is None else f" (from its {p['inherited_type']} {p['resource_id']})"
            what = "frozen" if p["level"] == "freeze" else "kept from deletion"
            reason = f": {p['reason']}" if p["reason"] else ""
            return f"{resource_id} is {what}{where}{reason}. Lift the protection first."
    return None


def request_refusal(method, path):
    """Why a request to the API may not run, from the protection of the VM or container it is about, or None.
    Reading is never refused, nor are the consoles of a frozen object."""
    if method in ("GET", "HEAD", "OPTIONS"):
        return None
    found = _GUEST.match(path)
    if not found:
        return None
    kind = "vm" if found.group(1) == "vms" else "container"
    rest = found.group(3) or ""
    if _FROZEN_ALLOWED.match(rest):
        return None
    deleting = method == "DELETE" and bool(_DELETIONS.match(rest))
    return refusal(kind, found.group(2), deleting)
