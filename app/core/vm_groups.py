"""VM groups (docs/design/governance.md, section 13): N identical VMs made from one template, with a count to raise
or lower.

The members are the VMs of this node named <prefix>-<n>. Raising the count deploys the missing ones from the template
(each its own task, as a deployment by hand); lowering it deletes the highest-numbered ones (to the recycle bin when it
is on), stopped ones first, and running ones only when asked. Each new member joins the group's pool and carries its
tags.
"""

import json
import re
import uuid
from datetime import UTC, datetime

from app.core.database import get_conn

PREFIX_RE = re.compile(r"^[a-z][a-z0-9-]{0,40}$")


class GroupError(ValueError):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _row(r):
    g = dict(r)
    g["tags"] = json.loads(g["tags"] or "[]")
    g["linked"] = bool(g["linked"])
    return g


def _domains():
    from app.core.libvirt_utils import open_conn

    conn = open_conn(readonly=True)
    try:
        return {d.name(): bool(d.isActive()) for d in conn.listAllDomains()}
    finally:
        conn.close()


def members(group, domains=None):
    """[(n, name, running)] of the group's VMs on this node, by number."""
    domains = _domains() if domains is None else domains
    pattern = re.compile(rf"^{re.escape(group['prefix'])}-(\d+)$")
    found = [(int(m.group(1)), name, running) for name, running in domains.items() if (m := pattern.match(name))]
    return sorted(found)


def groups():
    with get_conn() as db:
        rows = [_row(r) for r in db.execute("SELECT * FROM vm_groups ORDER BY name")]
    domains = _domains() if rows else {}
    for g in rows:
        g["members"] = [{"name": n, "running": r} for _, n, r in members(g, domains)]
    return rows


def _get(group_id):
    with get_conn() as db:
        r = db.execute("SELECT * FROM vm_groups WHERE id = ?", (group_id,)).fetchone()
    if r is None:
        raise GroupError(404, "Not found")
    return _row(r)


def create(fields, username):
    from app.core import object_meta

    if not PREFIX_RE.match(fields["prefix"]):
        raise GroupError(422, "The prefix is lowercase letters, digits and dashes, starting with a letter")
    try:
        tags = object_meta.normalize_tags(fields.get("tags") or [])
    except object_meta.MetaError as e:
        raise GroupError(422, str(e)) from None
    group_id = uuid.uuid4().hex
    import sqlite3

    try:
        with get_conn() as db:
            db.execute(
                "INSERT INTO vm_groups (id, name, template, network, linked, pool_id, tags, prefix, count, created_by, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (
                    group_id,
                    fields["name"],
                    fields["template"],
                    fields.get("network"),
                    1 if fields.get("linked", True) else 0,
                    fields.get("pool_id"),
                    json.dumps(tags),
                    fields["prefix"],
                    username,
                    datetime.now(UTC).isoformat(),
                ),
            )
            db.commit()
    except sqlite3.IntegrityError:
        raise GroupError(409, "A group of that name or prefix already exists") from None
    return _get(group_id)


def delete(group_id):
    with get_conn() as db:
        cur = db.execute("DELETE FROM vm_groups WHERE id = ?", (group_id,))
        db.commit()
    return cur.rowcount > 0


def scale(group_id, count, user, force=False):
    """Bring the group to `count` members; returns {"made": [names], "removed": [names]}."""
    from app.core import object_meta, recycle_bin
    from app.core import permissions as perm
    from app.routers.templates import DeployRequest, deploy_template
    from app.routers.vms import lifecycle

    g = _get(group_id)
    have = members(g)
    made, removed = [], []
    if count > len(have):
        taken = {n for n, _, _ in have}
        n = 0
        while len(have) + len(made) < count:
            n += 1
            name = f"{g['prefix']}-{n}"
            if n in taken or recycle_bin.holds(name):
                continue
            deploy_template(g["template"], DeployRequest(new_name=name, network=g["network"], linked=g["linked"]), user,
                            wait=False)  # fmt: skip
            if g["pool_id"] is not None:
                perm.add_pool_member(g["pool_id"], name)
            if g["tags"]:
                object_meta.put("vm", name, "", g["tags"])
            made.append(name)
    elif count < len(have):
        # The highest numbers go first, stopped ones before running ones.
        extra = sorted(have, key=lambda m: (m[2], -m[0]))[: len(have) - count]
        running = [name for _, name, r in extra if r]
        if running and not force:
            raise GroupError(
                409, f"{', '.join(running)} still run{'s' if len(running) == 1 else ''}: stop them, or ask with force"
            )
        for _, name, r in extra:
            if r:
                from app.core import privileged

                privileged.run("vm.power", user=user["username"], vm=name, action="stop")
            lifecycle.delete_vm(name, confirm=True, node=None, purge=False, user=user)
            removed.append(name)
    with get_conn() as db:
        db.execute("UPDATE vm_groups SET count = ? WHERE id = ?", (count, group_id))
        db.commit()
    return {"made": made, "removed": removed}
