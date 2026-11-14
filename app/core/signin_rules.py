"""Sign-in rules (docs/design/governance.md, section 4): where from, on which days and hours, and with which factor an
account may sign in and use the API.

A rule applies to everyone, a global role, a group or one account. It holds in one of two modes: "watch" journals what
it would have refused and lets it through (to try a rule before enforcing it), "enforce" refuses. The source networks
and the hours are checked at sign-in and on every request; the second factor at a password sign-in (a session made with
one is proof enough; API tokens are for scripts and carry none). Root of a node (hyperlite-admin) is never held: it is
the way back in when a rule locks everyone out.
"""

import ipaddress
import json
import logging
import threading
import time
import uuid
from datetime import UTC, datetime

from app.core.database import get_conn

logger = logging.getLogger(__name__)

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_cache = {"at": 0.0, "rules": []}
_lock = threading.Lock()
_watched = {}  # (rule id, username, what) -> last journaled, so that a watched rule does not flood the journal
CACHE_S = 10
WATCH_EVERY_S = 3600


def _rule(row):
    r = dict(row)
    r["networks"] = json.loads(r["networks"]) if r["networks"] else []
    r["days"] = json.loads(r["days"]) if r["days"] else []
    r["require_2fa"] = bool(r["require_2fa"])
    return r


def list_rules():
    with get_conn() as db:
        return [_rule(r) for r in db.execute("SELECT * FROM signin_rules ORDER BY name")]


def _rules():
    with _lock:
        if time.monotonic() - _cache["at"] > CACHE_S:
            _cache.update(at=time.monotonic(), rules=list_rules())
        return _cache["rules"]


def _forget():
    with _lock:
        _cache["at"] = 0.0


def validate(rule):
    for net in rule.get("networks") or []:
        try:
            ipaddress.ip_network(net, strict=False)
        except ValueError:
            raise ValueError(f"Not a network: {net}") from None
    for day in rule.get("days") or []:
        if day not in DAYS:
            raise ValueError(f"Days are named {', '.join(DAYS)}")
    hours = rule.get("hours")
    if hours:
        try:
            start, end = (datetime.strptime(x.strip(), "%H:%M") for x in hours.split("-"))
        except ValueError:
            raise ValueError("Hours are written 08:00-19:00") from None
        if start == end:
            raise ValueError("The hours must not start and end at the same time")
    if rule.get("subject_type") != "all" and not rule.get("subject_id"):
        raise ValueError("Name the role, group or account the rule is for")
    if not (rule.get("networks") or rule.get("days") or rule.get("hours") or rule.get("require_2fa")):
        raise ValueError("A rule needs at least one condition: networks, days, hours or a second factor")


def save(rule, username, rule_id=None):
    validate(rule)
    row = (
        rule["name"],
        rule["subject_type"],
        None if rule["subject_type"] == "all" else str(rule["subject_id"]),
        json.dumps(rule.get("networks") or []) if rule.get("networks") else None,
        1 if rule.get("require_2fa") else 0,
        json.dumps(rule.get("days") or []) if rule.get("days") else None,
        rule.get("hours") or None,
        rule.get("mode", "watch"),
    )
    with get_conn() as db:
        if rule_id:
            cur = db.execute(
                "UPDATE signin_rules SET name = ?, subject_type = ?, subject_id = ?, networks = ?, require_2fa = ?, "
                "days = ?, hours = ?, mode = ? WHERE id = ?",
                (*row, rule_id),
            )
            if cur.rowcount == 0:
                return None
        else:
            rule_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO signin_rules (name, subject_type, subject_id, networks, require_2fa, days, hours, mode, "
                "id, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*row, rule_id, username, datetime.now(UTC).isoformat()),
            )
        db.commit()
    _forget()
    return next(r for r in list_rules() if r["id"] == rule_id)


def delete(rule_id):
    with get_conn() as db:
        cur = db.execute("DELETE FROM signin_rules WHERE id = ?", (rule_id,))
        db.commit()
    _forget()
    return cur.rowcount > 0


def _applies(rule, user):
    kind = rule["subject_type"]
    if kind == "all":
        return True
    if kind == "role":
        return user.get("owner_role", user["role"]) == rule["subject_id"]
    if kind == "user":
        return user["username"] == rule["subject_id"]
    from app.core import permissions

    return rule["subject_id"].isdigit() and int(rule["subject_id"]) in set(
        permissions.get_user_groups(user["username"])
    )


def _in_hours(hours, now):
    start, end = (datetime.strptime(x.strip(), "%H:%M").time() for x in hours.split("-"))
    t = now.time()
    return start <= t < end if start < end else (t >= start or t < end)  # 22:00-06:00 crosses midnight


def broken(rule, ip, now, has_second_factor=None):
    """What the rule finds wrong, or None. `has_second_factor`: None when it is not a sign-in."""
    if rule["networks"]:
        try:
            addr = ipaddress.ip_address(ip) if ip else None
        except ValueError:
            addr = None
        if addr is None or not any(addr in ipaddress.ip_network(n, strict=False) for n in rule["networks"]):
            return f"from {ip or 'an unknown address'}, outside the networks allowed"
    if rule["days"] and DAYS[now.weekday()] not in rule["days"]:
        return "on a day not allowed"
    if rule["hours"] and not _in_hours(rule["hours"], now):
        return f"outside the hours allowed ({rule['hours']})"
    if rule["require_2fa"] and has_second_factor is False:
        return "without a second factor"
    return None


def refusal(user, ip, has_second_factor=None, now=None):
    """Why `user` may not sign in or go on from `ip` now, or None. A rule in watch mode journals and lets through."""
    from app.core import security

    if user.get(security.LOCAL_ROOT_CLAIM):
        return None
    now = now or datetime.now().astimezone()
    for rule in _rules():
        if not _applies(rule, user):
            continue
        what = broken(rule, ip, now, has_second_factor)
        if what is None:
            continue
        if rule["mode"] == "enforce":
            return f"Sign-in rule '{rule['name']}': not allowed {what}"
        _journal_watch(rule, user["username"], what)
    return None


def _journal_watch(rule, username, what):
    key = (rule["id"], username, what)
    if time.monotonic() - _watched.get(key, -WATCH_EVERY_S) < WATCH_EVERY_S:
        return
    _watched[key] = time.monotonic()
    from app.core.audit import log_action

    log_action(username, "signin_rule_watch", rule["name"], "success", f"would be refused {what}")
