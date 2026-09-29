"""Notes and tags on VMs, containers and nodes: the operating memory of a fleet (what this VM is for, who to ask,
what not to touch) and a way to group objects across nodes ("prod", "db", "client-x").

Notes are plain text: the dashboard shows them as text, never as HTML, so a note cannot inject a script. Tags are
short lowercase words, so the same tag typed twice is the same tag.
"""

import json
import re

from app.core.database import get_conn

KINDS = ("vm", "container", "node")
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,31}$")
MAX_TAGS = 16
MAX_NOTES = 20_000
LOCAL = "local"


class MetaError(ValueError):
    pass


def normalize_tags(tags):
    """Lowercased, stripped, without duplicates, in the order given; refused when a tag is not a short word."""
    seen = []
    for raw in tags or []:
        tag = str(raw).strip().lower()
        if not tag:
            continue
        if not TAG_RE.match(tag):
            raise MetaError(
                f"Invalid tag '{raw}': 1 to 32 characters, lowercase letters, digits, '-', '_' or '.', "
                "starting with a letter or a digit"
            )
        if tag not in seen:
            seen.append(tag)
    if len(seen) > MAX_TAGS:
        raise MetaError(f"At most {MAX_TAGS} tags per object")
    return seen


def _key(kind, name, node):
    if kind not in KINDS:
        raise MetaError(f"Unknown object kind '{kind}'")
    # A node is identified by its own name; VMs and containers by their node and name.
    return kind, "" if kind == "node" else (node or LOCAL), name


def get(kind, name, node=None):
    k, n, nm = _key(kind, name, node)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT notes, tags FROM object_meta WHERE kind = ? AND node = ? AND name = ?", (k, n, nm)
        ).fetchone()
    if not row:
        return {"notes": "", "tags": []}
    return {"notes": row["notes"] or "", "tags": json.loads(row["tags"] or "[]")}


def put(kind, name, notes, tags, node=None):
    k, n, nm = _key(kind, name, node)
    notes = (notes or "").replace("\r\n", "\n")
    if len(notes) > MAX_NOTES:
        raise MetaError(f"Notes are limited to {MAX_NOTES} characters")
    tags = normalize_tags(tags)
    with get_conn() as conn:
        if not notes.strip() and not tags:
            conn.execute("DELETE FROM object_meta WHERE kind = ? AND node = ? AND name = ?", (k, n, nm))
        else:
            conn.execute(
                "INSERT INTO object_meta (kind, node, name, notes, tags) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(kind, node, name) DO UPDATE SET notes = excluded.notes, tags = excluded.tags",
                (k, n, nm, notes, json.dumps(tags)),
            )
        conn.commit()
    return {"notes": notes, "tags": tags}


def list_all(kind=None):
    """Every object's tags and whether it has notes (not the notes themselves: the lists stay light)."""
    sql = "SELECT kind, node, name, notes, tags FROM object_meta"
    params = ()
    if kind:
        sql += " WHERE kind = ?"
        params = (kind,)
    with get_conn() as conn:
        rows = conn.execute(sql + " ORDER BY kind, node, name", params).fetchall()
    return [
        {
            "kind": r["kind"],
            "node": r["node"] or None,
            "nom": r["name"],
            "tags": json.loads(r["tags"] or "[]"),
            "a_des_notes": bool((r["notes"] or "").strip()),
        }
        for r in rows
    ]


def delete(kind, name, node=None):
    k, n, nm = _key(kind, name, node)
    with get_conn() as conn:
        conn.execute("DELETE FROM object_meta WHERE kind = ? AND node = ? AND name = ?", (k, n, nm))
        conn.commit()


def follow_migration(vm_name, source_node, target_node):
    """A VM's notes and tags move with it to the node it was live-migrated to."""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE OR REPLACE object_meta SET node = ? WHERE kind = 'vm' AND node = ? AND name = ?",
            (target_node or LOCAL, source_node or LOCAL, vm_name),
        )
        conn.commit()
    return cur.rowcount > 0
