"""The Terminal's sessions (docs/design/workbench.md, sections 5, 7 and 13), on the API's side.

A session is a box hyperlite-daemon runs (app/daemon/ops/workbench.py) with a token of its own: an API token of kind
"workbench", owned by the account, ending with the session, revoked when it ends. The API keeps the sessions of this
node, opens and ends them, hands out attach tickets, and ends the ones left idle, past their time, or no longer allowed
by a sign-in rule.
"""

import json
import logging
import secrets
from datetime import UTC, datetime, timedelta

from app.core import privileged
from app.core.database import get_conn

logger = logging.getLogger(__name__)

SETTINGS = "workbench_settings"
DEFAULTS = {
    "everyone": False,  # every account signed in, not only those holding workbench.use
    "per_account": 2,
    "per_node": 20,
    "idle_min": 30,  # a session left without a terminal attached this long ends
    "max_hours": 8,
    "memory_mb": 512,
    "cpu_percent": 50,
    "tasks": 128,
    "home_mb": 1024,
    "recording": False,
}
LIMITS = {"everyone": (bool,), "per_account": (1, 10), "per_node": (1, 200), "idle_min": (5, 1440), "max_hours": (1, 72),
          "memory_mb": (128, 16384), "cpu_percent": (10, 400), "tasks": (32, 4096), "home_mb": (128, 65536),
          "recording": (bool,)}  # fmt: skip
TICKET_TTL_S = 30
_tickets = {}  # ticket -> (session id, username, expiry)


class WorkbenchError(ValueError):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _now():
    return datetime.now(UTC)


def _settings_store():
    from app.repositories import registry

    return registry.settings().sync


def settings():
    raw = _settings_store().app_setting(SETTINGS)
    try:
        return {**DEFAULTS, **(json.loads(raw) if raw else {})}
    except ValueError:
        return dict(DEFAULTS)


def set_settings(changes):
    current = settings()
    for key, value in changes.items():
        if key not in LIMITS:
            raise WorkbenchError(422, f"Unknown setting: {key}")
        bounds = LIMITS[key]
        if bounds == (bool,):
            current[key] = bool(value)
        else:
            if not isinstance(value, int) or not bounds[0] <= value <= bounds[1]:
                raise WorkbenchError(422, f"{key} must be between {bounds[0]} and {bounds[1]}")
            current[key] = value
    _settings_store().set_app_setting(SETTINGS, json.dumps(current))
    return current


def may_use(user):
    """An administrator; an account holding workbench.use where this node answers; everyone when the setting says so."""
    from app.core import permissions

    if user["role"] == "admin":
        return True
    return settings()["everyone"] or permissions.has_area_privilege(user, "workbench.use")


def uid_for(account):
    """The account's stable uid on this node (its home's owner), made the first time."""
    from app.core import workbench_box

    with get_conn() as db:
        row = db.execute("SELECT uid FROM workbench_uids WHERE account = ?", (account,)).fetchone()
        if row:
            return row["uid"]
        top = db.execute("SELECT MAX(uid) FROM workbench_uids").fetchone()[0]
        uid = (top or workbench_box.UID_MIN - 1) + 1
        if uid > workbench_box.UID_MAX:
            raise WorkbenchError(507, "No Terminal uid left on this node")
        db.execute("INSERT INTO workbench_uids (account, uid) VALUES (?, ?)", (account, uid))
        db.commit()
    return uid


def sessions(account=None, state="open"):
    with get_conn() as db:
        sql = "SELECT * FROM workbench_sessions WHERE state = ?"
        params = [state]
        if account:
            sql += " AND account = ?"
            params.append(account)
        return [dict(r) for r in db.execute(sql + " ORDER BY opened_at", params)]


def get(sid):
    with get_conn() as db:
        row = db.execute("SELECT * FROM workbench_sessions WHERE id = ?", (sid,)).fetchone()
    return dict(row) if row else None


def opened_from(token_id):
    """The address an open session's token was given to (the browser's), for the sign-in rules."""
    with get_conn() as db:
        row = db.execute(
            "SELECT from_address FROM workbench_sessions WHERE token_id = ? AND state = 'open'", (token_id,)
        ).fetchone()
    return row["from_address"] if row else None


def open_session(user, ip, as_scope=None):
    """Open a session for `user` from `ip`; its row. `as_scope`: {role, resource_type, resource_id} to act with that
    role only (a delegated token)."""
    from app.core import api_tokens
    from app.core import permissions as perm
    from app.daemon.registry import DaemonError

    if not may_use(user):
        raise WorkbenchError(403, "The Terminal is not open to this account (required privilege: workbench.use)")
    s = settings()
    if len(sessions(user["username"])) >= s["per_account"]:
        raise WorkbenchError(409, f"At most {s['per_account']} Terminal sessions per account: end one first")
    if len(sessions()) >= s["per_node"]:
        raise WorkbenchError(503, "This node runs as many Terminal sessions as it may: try again later")
    if as_scope is not None and (
        not perm.role_exists(as_scope["role"]) or as_scope["resource_type"] not in perm.RESOURCE_TYPES
    ):
        raise WorkbenchError(422, "Name a known role and kind of perimeter")
    sid = secrets.token_hex(8)
    uid = uid_for(user["username"])
    expires = (_now() + timedelta(hours=s["max_hours"], minutes=1)).isoformat()
    token_id, token = api_tokens.create_token(user["username"], f"terminal:{sid}", expires_at=expires, kind="workbench")
    if as_scope is not None:
        api_tokens.set_scope(token_id, as_scope["role"], as_scope["resource_type"], as_scope["resource_id"])
    with get_conn() as db:
        db.execute(
            "INSERT INTO workbench_sessions (id, account, uid, opened_at, from_address, token_id, as_role, state, "
            "attached, detached_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', 0, ?)",
            (
                sid,
                user["username"],
                uid,
                _now().isoformat(),
                ip,
                token_id,
                json.dumps(as_scope) if as_scope else None,
                _now().isoformat(),
            ),
        )
        db.commit()
    try:
        privileged.run("workbench.open", user=user["username"], sid=sid, token=token, timeout=120)
    except DaemonError as e:
        _ended(sid, "failed to start")
        api_tokens.revoke_token(user["username"], token_id)
        raise WorkbenchError(502, e.detail) from None
    return get(sid)


def _ended(sid, why):
    with get_conn() as db:
        db.execute(
            "UPDATE workbench_sessions SET state = 'ended', attached = 0, ended_at = ?, ended_why = ? WHERE id = ?",
            (_now().isoformat(), why, sid),
        )
        db.commit()


def close(sid, why, by="system"):
    """End a session: its box stopped, its token revoked, the row closed, journaled."""
    from app.core import api_tokens
    from app.core.audit import log_action
    from app.daemon.registry import DaemonError

    row = get(sid)
    if row is None or row["state"] != "open":
        return False
    try:
        privileged.run("workbench.close", user=by if by != "system" else None, sid=sid, timeout=60)
    except DaemonError as e:
        logger.warning("Terminal %s not stopped cleanly: %s", sid, e.detail)
    _ended(sid, why)
    if row["token_id"]:
        api_tokens.revoke_token(row["account"], row["token_id"])
    log_action(by if by != "system" else "system", "terminal_close", row["account"], "success", f"{sid}: {why}")
    return True


def ticket(sid, user):
    """A single-use attach ticket for the owner's open session."""
    import time

    row = get(sid)
    if row is None or row["state"] != "open" or row["account"] != user["username"]:
        raise WorkbenchError(404, "No such open Terminal session of yours")
    now = time.time()
    for t, (_s, _u, exp) in list(_tickets.items()):
        if exp < now:
            _tickets.pop(t, None)
    value = secrets.token_urlsafe(24)
    _tickets[value] = (sid, user["username"], now + TICKET_TTL_S)
    return value


def take_ticket(value):
    """(session id, username) for a valid ticket, consumed; None otherwise."""
    import time

    entry = _tickets.pop(value, None) if value else None
    if entry is None or entry[2] < time.time():
        return None
    return entry[0], entry[1]


def mark_attached(sid, attached):
    with get_conn() as db:
        db.execute(
            "UPDATE workbench_sessions SET attached = ?, detached_at = CASE WHEN ? THEN detached_at ELSE ? END "
            "WHERE id = ?",
            (1 if attached else 0, 1 if attached else 0, _now().isoformat(), sid),
        )
        db.commit()


def sweep(now=None):
    """End the sessions left detached past the idle time, past their longest time, or that a sign-in rule no longer
    allows (hyperlite-scheduler's access sweep, every minute)."""
    from app.core import signin_rules
    from app.services import account_service

    now = now or _now()
    s = settings()
    open_rows = sessions()
    running = None
    if open_rows:
        from app.daemon.registry import DaemonError

        try:
            running = set(privileged.run("workbench.list")["sessions"])
        except DaemonError as e:
            logger.warning("Terminal boxes not listed: %s", e.detail)
    for row in open_rows:
        why = None
        if running is not None and row["id"] not in running:
            why = "shell exited"
        elif (
            not row["attached"]
            and row["detached_at"]
            and datetime.fromisoformat(row["detached_at"]) <= now - timedelta(minutes=s["idle_min"])
        ):
            why = "idle"
        elif datetime.fromisoformat(row["opened_at"]) <= now - timedelta(hours=s["max_hours"]):
            why = "time limit"
        else:
            account = account_service.get(row["account"])
            if account is None:
                why = "account deleted"
            elif signin_rules.refusal(account, row["from_address"]):
                why = "sign-in rule"
        if why:
            close(row["id"], why)


def adopt():
    """At the API's start: keep the sessions whose box still runs, end the others, stop boxes nobody holds."""
    from app.daemon.registry import DaemonError

    try:
        running = set(privileged.run("workbench.list")["sessions"])
    except DaemonError as e:
        logger.warning("Terminal boxes not listed at start: %s", e.detail)
        return
    for row in sessions():
        if row["id"] not in running:
            _ended(row["id"], "node restarted")
        else:
            mark_attached(row["id"], False)
    known = {r["id"] for r in sessions()}
    for sid in running - known:
        try:
            privileged.run("workbench.close", sid=sid)
        except DaemonError:
            logger.warning("A Terminal box without a session was not stopped: %s", sid)
