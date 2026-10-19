"""A cluster updated to API v1 (docs/design/api-v1.md, "The cluster update"): the names of the replicated
configuration change with 1.0, so the cluster changes nothing while some members run 1.0 and others an older version,
and the leader rewrites the replicated configuration once every member runs 1.0.

Each node says which API it writes in the status it sends every 10 s (statd.summary, "api"). A member that sends one
without it (a version before 1.0) holds the cluster read-only, with its name in the reason (cluster_lead): the members
on 1.0 would otherwise write rows it cannot apply. A member that sends nothing (down, cut off) holds nothing back: the
rows it wrote before are read with the new names (schema_v1.row), and it is updated when it comes back.

Once no member is behind, the leader rewrites every row of the tree still written with the old names, each with
compare-and-set against the version it read, then records it under DONE_PATH: done once for the cluster.
"""

import json
import logging
import threading
import time

from app.core.cfs_client import CfsError, Conflict, NotFound

logger = logging.getLogger(__name__)

API_VERSION = 1
DONE_PATH = "/cluster/api-version"
CHECK_S = 30
_TTL_S = 5.0
_lock = threading.Lock()
_behind = (0.0, [])


def behind():
    """The members that send a status written by a version before 1.0, by name (cached a few seconds)."""
    global _behind
    now = time.monotonic()
    with _lock:
        at, names = _behind
        if now - at < _TTL_S:
            return names
    from app.core import cluster_view, statd

    try:
        members = {m["name"] for m in cluster_view.members()}
        names = sorted(name for name, s in statd.table().items() if name in members and s.get("api", 0) < API_VERSION)
    except Exception as e:  # nothing known: nothing held back on a guess
        logger.debug("Members' versions not read: %s", e)
        names = []
    with _lock:
        _behind = (now, names)
    return names


def read_only_reason():
    names = behind()
    if not names:
        return None
    return (
        f"The cluster is being updated to Hyperlite 1.0: {', '.join(names)} still "
        f"{'runs' if len(names) == 1 else 'run'} an older version. Nothing can be changed until every node runs 1.0."
    )


def _client():
    from app.repositories.cfs import shadow

    return shadow._get_client()


def rewritten():
    try:
        return int(_client().get(DONE_PATH).data) >= API_VERSION
    except NotFound:
        return False


def rewrite():
    """Rewrite the rows of the tree still written with the old names. Returns how many were rewritten; a row another
    node changed meanwhile is left to the next round (and is then already in the new names)."""
    from app.core import schema_v1
    from app.repositories.cfs import shadow

    client = _client()
    done = 0
    moved = []  # (path, version before, version after)
    for name, table in shadow.tables().items():
        for path, (data, version) in shadow._read_tree_versions(client, table.prefix).items():
            try:
                row = json.loads(data)
            except ValueError:
                continue
            new = schema_v1.row(name, row)
            if new == row:
                continue
            try:
                moved.append((path, version, client.put(path, shadow._encode(new), version)))
                done += 1
            except (Conflict, NotFound):
                logger.info("%s changed meanwhile: rewritten at the next round", path)
    from app.core import cluster_setup

    try:
        entry = client.get(cluster_setup.CONF_PATH)
        conf = json.loads(entry.data)
        new = schema_v1.cluster_conf(conf)
        if new != conf:
            client.put(cluster_setup.CONF_PATH, json.dumps(new).encode(), entry.version)
            done += 1
    except NotFound:
        pass  # not a cluster (yet)
    except Conflict:
        logger.info("The cluster's configuration changed meanwhile: rewritten at the next round")
    if moved:
        # Only the names changed: a node that held a row at its version before holds it at its version after, and its
        # next change of it starts from there (never taken for another node's change, shadow._write_checked).
        with shadow._db() as db:
            db.executemany(
                "UPDATE cfs_base SET version = ? WHERE path = ? AND version = ?", [(a, p, b) for p, b, a in moved]
            )
            db.commit()
    return done


def step():
    """One round: rewrite the tree when this node leads, every member runs 1.0, and it was not done yet. Returns True
    once it is done (nothing more to do on this node)."""
    from app.core import cluster_lead

    if cluster_lead.mode(record=False) not in ("cluster", "local"):
        return False  # the replicated configuration is off: nothing to rewrite
    if rewritten():
        return True
    if not cluster_lead.is_leader() or behind():
        return False
    if rewrite():
        return False  # read again at the next round: what changed meanwhile, if anything
    _client().put(DONE_PATH, str(API_VERSION).encode())
    logger.info("The replicated configuration is written with the names of API v%d", API_VERSION)
    return True


def _loop():
    while True:
        try:
            if step():
                return
        except (CfsError, OSError) as e:
            logger.debug("API v1 rewrite not made yet: %s", e)
        except Exception:  # the thread must survive anything; the rows are read with the new names meanwhile
            logger.exception("API v1 rewrite failed; retrying")
        time.sleep(CHECK_S)


def start():
    threading.Thread(target=_loop, daemon=True, name="api-v1-rewrite").start()
