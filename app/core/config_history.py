"""Configuration history: each VM's definition as it was after every change, and what changed between two versions.

The metrics collector shows every VM to observe() at each tick (app/core/metrics.py); once a minute per VM, its
persistent definition (libvirt's inactive XML, without secrets) is compared with the last one recorded, and a new
version is kept when it differs, whoever changed it: the console, the API, a migration or virsh by hand. Who did it is
read from the journal: the actions on that VM between the two versions.

Kept per node, like the metrics: each node records the VMs it samples. The last KEEP versions of each VM are kept.
"""

import difflib
import hashlib
import logging
import threading
import time
from datetime import UTC, datetime

import libvirt

logger = logging.getLogger(__name__)

KEEP = 50
CHECK_EVERY_S = 60
_lock = threading.Lock()
_seen = {}  # target -> (hash, last check, monotonic)


def _store():
    from app.repositories.sqlite.config_history import SqliteConfigHistoryStore

    return SqliteConfigHistoryStore()


def normalize(xml):
    """The definition without blank lines or trailing spaces, so formatting alone is no change."""
    return "\n".join(line.rstrip() for line in xml.splitlines() if line.strip()) + "\n"


def observe(domain, target, monotonic=None):
    """Records the definition of `domain` (sampled as `target`) when it changed. Never raises: the collector goes on."""
    monotonic = time.monotonic() if monotonic is None else monotonic
    with _lock:
        seen = _seen.get(target)
        if seen and monotonic - seen[1] < CHECK_EVERY_S:
            return
    try:
        xml = normalize(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    except libvirt.libvirtError:
        return  # gone meanwhile
    digest = hashlib.sha256(xml.encode()).hexdigest()
    try:
        # The database is read only on a change seen here, or the first time after a restart.
        if (not seen or seen[0] != digest) and _store().last_hash(target) != digest:
            _store().add(target, datetime.now(UTC).isoformat(), digest, xml, KEEP)
        with _lock:
            _seen[target] = (digest, monotonic)
    except Exception:
        logger.warning("The definition of %s was not recorded", target, exc_info=True)


def target_of(name, node=None):
    """The sampling target of a VM, as the metrics name it: its bare name here, "node:name" on a registered node."""
    return name if not node or node == "local" else f"{node}:{name}"


def _resource(target):
    return target.rsplit(":", 1)[-1]


def versions(target):
    """The versions, newest first, each with the journal's actions on the VM since the version before it."""
    from app.repositories import registry

    rows = _store().versions(target)
    out = []
    for i, v in enumerate(rows):
        since = rows[i + 1]["ts"] if i + 1 < len(rows) else None
        actions = []
        if since:
            entries = registry.audit().sync.query({"resource": _resource(target), "since": since, "until": v["ts"]}, 20)
            actions = [
                {"timestamp": e["timestamp"], "username": e["username"], "action": e["action"], "result": e["result"]}
                for e in entries
                if e["username"] not in ("system",) and not e["action"].startswith(("get_", "list_"))
            ]
        out.append({**v, "first": since is None, "actions": actions})
    return out


def diff(target, version_id):
    """The unified diff of a version against the one before it (None when there is no such version), plus its XML."""
    v = _store().version(target, version_id)
    if not v:
        return None
    prev = _store().previous(target, version_id)
    before = prev["xml"] if prev else ""
    lines = list(difflib.unified_diff(before.splitlines(), v["xml"].splitlines(), "before", "after", lineterm="", n=3))
    return {
        "id": v["id"],
        "ts": v["ts"],
        "previous_id": prev["id"] if prev else None,
        "diff": "\n".join(lines),
        "xml": v["xml"],
    }


def forget():
    with _lock:
        _seen.clear()
