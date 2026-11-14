"""Dashboards and saved queries (docs/design/console-2.md, sections 2 and 3).

A dashboard is a named list of tiles; a saved query is a search by query with a name and a view. Both belong to an
account and are private, shared with every account, or shared with a group. Reading one grants nothing: a tile and a
query are drawn in the browser with the reader's own rights. Their owner and administrators change them. Both tables
are replicated (configuration, the same on every node).
"""

import json
import re
import uuid
from datetime import UTC, datetime

from app.core.database import get_conn

TILE_TYPES = {"chart", "query", "availability", "alerts", "note", "resources"}
MAX_TILES = 40
MAX_BYTES = 64 * 1024
VIEWS = {"list", "count", "bars"}
GROUP_BY = {"state", "node", "os", "pool", "tag", "kind"}
SHARED_RE = re.compile(r"^(private|everyone|group:\d{1,9})$")


class DashboardError(ValueError):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _now():
    return datetime.now(UTC).isoformat()


def _groups_of(username):
    from app.core import permissions

    return {str(g) for g in permissions.get_user_groups(username)}


def may_read(row, user):
    shared = row["shared"]
    if row["owner"] == user["username"] or shared == "everyone":
        return True
    return shared.startswith("group:") and shared[6:] in _groups_of(user["username"])


def may_change(row, user):
    return row["owner"] == user["username"] or (user["role"] == "admin" and may_read(row, user))


def _name(value):
    name = (value or "").strip()
    if not 1 <= len(name) <= 80:
        raise DashboardError(422, "A name of 1 to 80 characters")
    return name


def _shared(value):
    if not SHARED_RE.match(value or ""):
        raise DashboardError(422, "Shared with: private, everyone or group:<id>")
    return value


def _tiles(tiles):
    if not isinstance(tiles, list) or len(tiles) > MAX_TILES:
        raise DashboardError(422, f"At most {MAX_TILES} tiles")
    out = []
    for t in tiles:
        if not isinstance(t, dict) or t.get("type") not in TILE_TYPES:
            raise DashboardError(422, f"A tile is one of: {', '.join(sorted(TILE_TYPES))}")
        size = t.get("size", 1)
        if size not in (1, 2, 3):
            raise DashboardError(422, "A tile spans 1, 2 or 3 columns")
        config = t.get("config") or {}
        if not isinstance(config, dict):
            raise DashboardError(422, "A tile's config is an object")
        out.append(
            {
                "id": str(t.get("id") or uuid.uuid4().hex[:10])[:24],
                "type": t["type"],
                "title": str(t.get("title") or "")[:120],
                "size": size,
                "config": config,
            }
        )
    if len(json.dumps(out)) > MAX_BYTES:
        raise DashboardError(422, "This dashboard is too large")
    return out


def _dash(row):
    d = dict(row)
    d["tiles"] = json.loads(d["tiles"] or "[]")
    return d


# ---- Dashboards ----


def dashboards(user):
    with get_conn() as db:
        rows = [_dash(r) for r in db.execute("SELECT * FROM dashboards ORDER BY name COLLATE NOCASE")]
    return [{**d, "editable": may_change(d, user)} for d in rows if may_read(d, user)]


def get(dash_id, user):
    with get_conn() as db:
        row = db.execute("SELECT * FROM dashboards WHERE id = ?", (dash_id,)).fetchone()
    if row is None or not may_read(row, user):
        raise DashboardError(404, "No such dashboard")
    d = _dash(row)
    return {**d, "editable": may_change(d, user)}


def create(fields, user):
    dash_id = uuid.uuid4().hex
    now = _now()
    with get_conn() as db:
        db.execute(
            "INSERT INTO dashboards (id, name, owner, shared, tiles, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                dash_id,
                _name(fields.get("name")),
                user["username"],
                _shared(fields.get("shared") or "private"),
                json.dumps(_tiles(fields.get("tiles") or [])),
                now,
                now,
            ),
        )
        db.commit()
    return get(dash_id, user)


def replace(dash_id, fields, user):
    current = get(dash_id, user)
    if not current["editable"]:
        raise DashboardError(403, "Only its owner or an administrator changes this dashboard")
    with get_conn() as db:
        db.execute(
            "UPDATE dashboards SET name = ?, shared = ?, tiles = ?, updated_at = ? WHERE id = ?",
            (
                _name(fields.get("name", current["name"])),
                _shared(fields.get("shared", current["shared"])),
                json.dumps(_tiles(fields.get("tiles", current["tiles"]))),
                _now(),
                dash_id,
            ),
        )
        db.commit()
    return get(dash_id, user)


def delete(dash_id, user):
    current = get(dash_id, user)
    if not current["editable"]:
        raise DashboardError(403, "Only its owner or an administrator deletes this dashboard")
    with get_conn() as db:
        db.execute("DELETE FROM dashboards WHERE id = ?", (dash_id,))
        db.commit()
    return current


def copy(dash_id, user, name=None):
    source = get(dash_id, user)
    return create(
        {"name": name or f"{source['name']} (copy)"[:80], "shared": "private", "tiles": source["tiles"]}, user
    )


# ---- Saved queries ----


def _query_fields(fields, current=None):
    current = current or {}
    query = str(fields.get("query", current.get("query", ""))).strip()
    if not 1 <= len(query) <= 500:
        raise DashboardError(422, "A query of 1 to 500 characters")
    view = fields.get("view", current.get("view", "list"))
    if view not in VIEWS:
        raise DashboardError(422, f"A view is one of: {', '.join(sorted(VIEWS))}")
    group_by = fields.get("group_by", current.get("group_by"))
    if view == "bars" and group_by not in GROUP_BY:
        raise DashboardError(422, f"Bars are grouped by one of: {', '.join(sorted(GROUP_BY))}")
    return {
        "name": _name(fields.get("name", current.get("name"))),
        "query": query,
        "view": view,
        "group_by": group_by if view == "bars" else None,
        "shared": _shared(fields.get("shared", current.get("shared", "private"))),
    }


def queries(user):
    with get_conn() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM saved_queries ORDER BY name COLLATE NOCASE")]
    return [{**q, "editable": may_change(q, user)} for q in rows if may_read(q, user)]


def get_query(query_id, user):
    with get_conn() as db:
        row = db.execute("SELECT * FROM saved_queries WHERE id = ?", (query_id,)).fetchone()
    if row is None or not may_read(row, user):
        raise DashboardError(404, "No such saved query")
    return {**dict(row), "editable": may_change(row, user)}


def save_query(fields, user):
    f = _query_fields(fields)
    query_id = uuid.uuid4().hex
    with get_conn() as db:
        db.execute(
            "INSERT INTO saved_queries (id, name, query, view, group_by, owner, shared, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (query_id, f["name"], f["query"], f["view"], f["group_by"], user["username"], f["shared"], _now()),
        )
        db.commit()
    return get_query(query_id, user)


def change_query(query_id, fields, user):
    current = get_query(query_id, user)
    if not current["editable"]:
        raise DashboardError(403, "Only its owner or an administrator changes this query")
    f = _query_fields(fields, current)
    with get_conn() as db:
        db.execute(
            "UPDATE saved_queries SET name = ?, query = ?, view = ?, group_by = ?, shared = ? WHERE id = ?",
            (f["name"], f["query"], f["view"], f["group_by"], f["shared"], query_id),
        )
        db.commit()
    return get_query(query_id, user)


def delete_query(query_id, user):
    current = get_query(query_id, user)
    if not current["editable"]:
        raise DashboardError(403, "Only its owner or an administrator deletes this query")
    with get_conn() as db:
        db.execute("DELETE FROM saved_queries WHERE id = ?", (query_id,))
        db.commit()
    return current
