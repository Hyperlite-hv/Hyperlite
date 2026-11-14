"""Notes and tags on VMs, containers, nodes, pools, storage pools and networks: the operating memory of a fleet (what this VM is for, who to ask,
what not to touch) and a way to group objects across nodes ("prod", "db", "client-x").

Notes are plain text: the dashboard shows them as text, never as HTML, so a note cannot inject a script. Tags are
short lowercase words, so the same tag typed twice is the same tag.
"""

import json
import re


def _store():
    from app.repositories import registry

    return registry.objects().sync


KINDS = ("vm", "container", "node", "pool", "storage", "network")
# Known by their own name (a pool by its id) across the cluster, not by a node and a name.
CLUSTER_WIDE = ("node", "pool", "storage", "network")
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
    return kind, "" if kind in CLUSTER_WIDE else (node or LOCAL), name


def get(kind, name, node=None):
    k, n, nm = _key(kind, name, node)
    row = _store().meta(k, n, nm)
    if not row:
        return {"notes": "", "tags": []}
    return {"notes": row["notes"] or "", "tags": json.loads(row["tags"] or "[]")}


def put(kind, name, notes, tags, node=None):
    k, n, nm = _key(kind, name, node)
    notes = (notes or "").replace("\r\n", "\n")
    if len(notes) > MAX_NOTES:
        raise MetaError(f"Notes are limited to {MAX_NOTES} characters")
    tags = normalize_tags(tags)
    if not notes.strip() and not tags:
        _store().delete_meta(k, n, nm)
    else:
        _store().put_meta(k, n, nm, notes, json.dumps(tags))
    return {"notes": notes, "tags": tags}


def list_all(kind=None):
    """Every object's tags and whether it has notes (not the notes themselves: the lists stay light)."""
    return [
        {
            "kind": r["kind"],
            "node": r["node"] or None,
            "name": r["name"],
            "tags": json.loads(r["tags"] or "[]"),
            "has_notes": bool((r["notes"] or "").strip()),
        }
        for r in _store().all_meta(kind)
    ]


def effective_tags(kind, name, node=None):
    """An object's tags with those it inherits: a VM carries the tags of the pools it belongs to."""
    tags = list(get(kind, name, node)["tags"])
    if kind == "vm":
        from app.core import permissions

        for pool_id in permissions.get_vm_pools(name):
            tags += [t for t in get("pool", str(pool_id))["tags"] if t not in tags]
    return tags


def delete(kind, name, node=None):
    _store().delete_meta(*_key(kind, name, node))


def follow_migration(vm_name, source_node, target_node):
    """A VM's notes and tags move with it to the node it was live-migrated to."""
    return _store().move_vm_meta(vm_name, source_node or LOCAL, target_node or LOCAL)


def retag(old, new=None):
    """Rename the tag `old` to `new` on every object (merging it into `new` where both are there), or remove it when
    `new` is None. Returns how many objects changed."""
    names = normalize_tags([old])
    if not names:
        raise MetaError("Name the tag to change")
    old = names[0]
    new = (normalize_tags([new]) or [None])[0] if new else None
    changed = 0
    for row in _store().all_meta(None):
        tags = json.loads(row["tags"] or "[]")
        if old not in tags:
            continue
        out = []
        for tag in tags:
            replaced = new if tag == old else tag
            if replaced and replaced not in out:
                out.append(replaced)
        if not (row["notes"] or "").strip() and not out:
            _store().delete_meta(row["kind"], row["node"], row["name"])
        else:
            _store().put_meta(row["kind"], row["node"], row["name"], row["notes"] or "", json.dumps(out))
        changed += 1
    return changed


def tag_counts():
    """Every tag in use: how many objects carry it, by kind."""
    counts = {}
    for row in _store().all_meta(None):
        for tag in json.loads(row["tags"] or "[]"):
            entry = counts.setdefault(tag, {"tag": tag, "total": 0, "kinds": {}})
            entry["total"] += 1
            entry["kinds"][row["kind"]] = entry["kinds"].get(row["kind"], 0) + 1
    return sorted(counts.values(), key=lambda e: (-e["total"], e["tag"]))
