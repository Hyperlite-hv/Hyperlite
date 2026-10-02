"""Shadow mode of hyperlite-cfs (docs/design/hyperlite-cfs.md, section 8, phase C).

With HYPERLITE_CFS_SHADOW=1, each write to a mirrored table is copied into the local hyperlite-cfs daemon right after
SQLite committed it. SQLite stays the source of truth: a copy that fails is logged and counted, never raised, so the
request that wrote succeeds as it did before. report() compares the two entry by entry; no difference over two weeks
on a real cluster is the phase's exit criterion, before the daemon becomes the source of truth (phase D).

A copy re-reads the row from SQLite rather than taking the value the caller wrote, so what is copied is what SQLite
holds. A write path that forgets to call the mirror shows up in the report as a difference.

Mirrored so far: the notes and tags of VMs, containers and nodes (table object_meta), at /meta/<kind>/<node>/<name>.
"""

import contextlib
import json
import logging
import os
import re
import threading
from datetime import UTC, datetime

from app.core.cfs_client import DEFAULT_SOCKET, CfsClient, CfsError, NotFound

logger = logging.getLogger(__name__)

# The daemon is local; a copy waits at most this long, so a stuck daemon slows a write by seconds, not minutes.
TIMEOUT_S = 2.0
EXAMPLES = 20  # paths listed per kind of difference in the report

_PLAIN = re.compile(r"^[A-Za-z0-9-][A-Za-z0-9._-]*$")


class ShadowDisabled(Exception):
    pass


def enabled():
    return os.environ.get("HYPERLITE_CFS_SHADOW", "") == "1"


def socket_path():
    return os.environ.get("HYPERLITE_CFS_SOCKET") or DEFAULT_SOCKET


def component(name):
    """A path component for any name: the name itself when the daemon accepts it as it is, otherwise '_' followed by
    its UTF-8 bytes in hex. A name that starts with '_' is always encoded, so an encoded name never equals a real one."""
    name = str(name)
    if _PLAIN.match(name):
        return name
    return "_" + name.encode().hex()


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class _Meta:
    """Notes and tags (app/core/object_meta.py)."""

    prefix = "/meta"

    def _store(self):
        from app.repositories.sqlite.objects import SqliteObjectStore

        return SqliteObjectStore()

    def path(self, kind, node, name):
        return f"{self.prefix}/{component(kind)}/{component(node)}/{component(name)}"

    def _value(self, row):
        return _encode({"notes": row["notes"], "tags": json.loads(row["tags"] or "[]")})

    def entry(self, kind, node, name):
        row = self._store().meta(kind, node, name)
        return self.path(kind, node, name), (self._value(row) if row else None)

    def all(self):
        return {self.path(r["kind"], r["node"], r["name"]): self._value(r) for r in self._store().all_meta()}


META = _Meta()
DOMAINS = {"meta": META}


class _Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.copies = 0
        self.failures = 0
        self.last_error = None
        self.last_error_at = None


_stats = _Stats()
_client = None
_client_lock = threading.Lock()


def _get_client():
    global _client
    with _client_lock:
        if _client is None or _client.path != socket_path():
            _client = CfsClient(socket_path(), timeout=TIMEOUT_S)
        return _client


def _failed(what, error):
    with _stats.lock:
        _stats.failures += 1
        _stats.last_error = f"{what}: {error}"
        _stats.last_error_at = datetime.now(UTC).isoformat()
    logger.warning("hyperlite-cfs shadow: %s not copied: %s", what, error)


def _write(client, path, data):
    if data is None:
        with contextlib.suppress(NotFound):  # already absent: the state wanted
            client.delete(path)
    else:
        client.put(path, data)


def _copy(path, data):
    try:
        _write(_get_client(), path, data)
    except (CfsError, OSError) as e:
        _failed(path, e)
        return
    with _stats.lock:
        _stats.copies += 1


def mirror_meta(kind, node, name):
    """Copy one object's notes and tags, as SQLite now holds them (deleted when SQLite has no row)."""
    if enabled():
        _copy(*META.entry(kind, node, name))


def refresh(domain):
    """Copy a whole domain after a write that touched many rows at once (a rename)."""
    if not enabled():
        return
    try:
        _sync(_get_client(), DOMAINS[domain])
    except (CfsError, OSError) as e:
        _failed(DOMAINS[domain].prefix, e)


def _read_tree(client, prefix):
    """{path: data} of every file under `prefix`."""
    out = {}
    try:
        children = client.list(prefix)
    except NotFound:
        return out
    for child in children:
        path = f"{prefix}/{child.name}"
        if child.is_dir:
            out.update(_read_tree(client, path))
        else:
            try:
                out[path] = client.get(path).data
            except NotFound:
                continue  # deleted between the listing and the read: absent, as the next report will show
    return out


def _sync(client, domain):
    expected = domain.all()
    actual = _read_tree(client, domain.prefix)
    written = deleted = 0
    for path, data in expected.items():
        if actual.get(path) != data:
            _write(client, path, data)
            written += 1
    for path in actual.keys() - expected.keys():
        _write(client, path, None)
        deleted += 1
    return written, deleted


def seed():
    """Make the daemon hold exactly what SQLite holds, for every mirrored domain: the first copy when shadow mode is
    turned on, or a repair after the daemon was down. Raises ShadowDisabled, CfsError or OSError."""
    if not enabled():
        raise ShadowDisabled("Shadow mode is off: set HYPERLITE_CFS_SHADOW=1 in .env and restart Hyperlite")
    client = _get_client()
    written = deleted = 0
    for domain in DOMAINS.values():
        w, d = _sync(client, domain)
        written += w
        deleted += d
    return {"ecrits": written, "supprimes": deleted}


def _compare(expected, actual):
    missing = sorted(expected.keys() - actual.keys())
    extra = sorted(actual.keys() - expected.keys())
    different = sorted(p for p in expected.keys() & actual.keys() if expected[p] != actual[p])
    return {
        "entrees": len(expected),
        "manquants": len(missing),
        "en_trop": len(extra),
        "differents": len(different),
        "exemples": {"manquants": missing[:EXAMPLES], "en_trop": extra[:EXAMPLES], "differents": different[:EXAMPLES]},
    }


def report():
    """Shadow mode's state and, when the daemon answers, the differences between SQLite and the daemon per domain."""
    with _stats.lock:
        out = {
            "actif": enabled(),
            "socket": socket_path(),
            "copies": _stats.copies,
            "echecs": _stats.failures,
            "derniere_erreur": _stats.last_error,
            "derniere_erreur_le": _stats.last_error_at,
        }
    try:
        client = _get_client()
        status = client.status()
        domains = {name: _compare(d.all(), _read_tree(client, d.prefix)) for name, d in DOMAINS.items()}
    except (CfsError, OSError) as e:
        out.update(joignable=False, erreur=str(e))
        return out
    out.update(
        joignable=True,
        demon={"mode": status.mode, "quorum": status.quorate, "version": status.version, "entrees": status.entries},
        domaines=domains,
        ecarts=sum(d["manquants"] + d["en_trop"] + d["differents"] for d in domains.values()),
    )
    return out
