"""Swagger (the API's interactive documentation) for the users an administrator chose, as in enterprise products:
a button in Administration > API opens /docs in a new tab; there, "Authorize" signs in (user name and password, or an
API token) and every call runs with that account's rights.

/docs and /openapi.json are never public. The button asks for a single-use ticket (only for an allowed account),
which /docs exchanges for a cookie of its own, valid a few hours and checked again at each page: an account whose
access is withdrawn loses it at once. The public pages of HYPERLITE_API_DOCS (app/core/http_headers.py) stay for
development only.
"""

import secrets
import threading
import time


def _store():
    from app.repositories import registry

    return registry.settings().sync


ACCESS = ("desactive", "admins", "tous")
DEFAULT = "admins"
_KEY = "api_docs_access"

COOKIE = "hl_docs"
TICKET_TTL_S = 60
SESSION_TTL_S = 8 * 3600

_lock = threading.Lock()
_tickets = {}  # ticket -> (username, expiry)
_sessions = {}  # cookie value -> (username, expiry)


def access():
    value = _store().app_setting(_KEY) or DEFAULT
    return value if value in ACCESS else DEFAULT


def set_access(value):
    if value not in ACCESS:
        raise ValueError(f"Access must be one of {', '.join(ACCESS)}")
    _store().set_app_setting(_KEY, value)
    return value


def allowed(user, value=None):
    value = value or access()
    return bool(user) and (value == "tous" or (value == "admins" and user.get("role") == "admin"))


def _role_of(username):
    from app.repositories import registry

    return registry.accounts().sync.role_of(username)


def _purge(store, now):
    for key, (_user, expiry) in list(store.items()):
        if expiry < now:
            store.pop(key, None)


def new_ticket(username):
    now = time.time()
    ticket = secrets.token_urlsafe(32)
    with _lock:
        _purge(_tickets, now)
        _tickets[ticket] = (username, now + TICKET_TTL_S)
    return ticket


def open_session(ticket):
    """The cookie value for a valid ticket (used once), else None."""
    now = time.time()
    with _lock:
        entry = _tickets.pop(ticket or "", None)
        if entry is None or entry[1] < now:
            return None
        _purge(_sessions, now)
        value = secrets.token_urlsafe(32)
        _sessions[value] = (entry[0], now + SESSION_TTL_S)
    return value


def session_user(cookie):
    """The account behind a docs cookie, still allowed by the current setting; else None."""
    now = time.time()
    with _lock:
        entry = _sessions.get(cookie or "")
    if entry is None or entry[1] < now:
        return None
    user = {"username": entry[0], "role": _role_of(entry[0])}
    if not allowed(user):
        with _lock:
            _sessions.pop(cookie, None)
        return None
    return user


def with_token_auth(schema):
    """The OpenAPI schema with an HTTP bearer scheme next to the password flow, so that "Authorize" also takes an
    API token (hlt_...): the same Authorization header, which the API already accepts."""
    components = schema.setdefault("components", {}).setdefault("securitySchemes", {})
    components["JetonAPI"] = {"type": "http", "scheme": "bearer", "description": "API token (hlt_…) or session token"}
    for path in schema.get("paths", {}).values():
        for operation in path.values():
            security = operation.get("security") if isinstance(operation, dict) else None
            if security and {"JetonAPI": []} not in security:
                security.append({"JetonAPI": []})
    return schema
