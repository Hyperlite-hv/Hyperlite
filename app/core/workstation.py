"""Workstation access: the `hyperlite` client installed on a user's computer.

Two mechanisms live here, both kept in memory like the other short-lived tickets
(console, terminal, export download):

- Device authorization (the flow of RFC 8628): `hyperlite login` asks for a code,
  the user approves it in the web interface (so SSO and two-factor sign-in apply
  unchanged), and the client then receives an expiring API token of its own. No
  password is ever typed into the terminal.
- Tunnel tickets: a single-use, 30-second ticket that lets the client open a
  WebSocket relay to one allowed TCP port of one VM (SSH, remote desktop...). The
  bytes are relayed as they are: SSH or RDP stay encrypted end to end between the
  workstation and the VM.

Settings (environment of the service, see docs/configuration.md):
- HYPERLITE_TUNNEL_PORTS: allowed guest ports, comma separated (default "22,3389",
  empty to disable tunnels);
- HYPERLITE_TUNNEL_IDLE_TIMEOUT_S: a tunnel without traffic is closed after this
  many seconds (default 3600);
- HYPERLITE_TUNNEL_MAX_PER_USER: open tunnels per user (default 20);
- HYPERLITE_CLI_TOKEN_DAYS: lifetime of a workstation token (default 30).
"""

import hashlib
import os
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta

from app.core.api_tokens import create_token

TUNNEL_TICKET_TTL = 30
CLI_REQUEST_TTL = 600
CLI_POLL_INTERVAL_S = 2
_MAX_PENDING_REQUESTS = 200
_MAX_PENDING_PER_IP = 10
# No vowels: a code cannot spell a word, and no 0/O or 1/I confusion.
_USER_CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"


def _int_env(name, default, low, high):
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(max(value, low), high)


def tunnel_ports():
    """Allowed guest ports. Unset means the defaults; set but empty disables tunnels."""
    raw = os.environ.get("HYPERLITE_TUNNEL_PORTS")
    if raw is None:
        return [22, 3389]
    ports = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= 65535 and int(part) not in ports:
            ports.append(int(part))
    return ports


def tunnel_idle_timeout_s():
    return _int_env("HYPERLITE_TUNNEL_IDLE_TIMEOUT_S", 3600, 60, 86400)


def tunnel_max_per_user():
    return _int_env("HYPERLITE_TUNNEL_MAX_PER_USER", 20, 1, 1000)


def cli_token_days():
    return _int_env("HYPERLITE_CLI_TOKEN_DAYS", 30, 1, 365)


# ---- Tunnel tickets and open tunnels ----

_lock = threading.Lock()
_tunnel_tickets = {}  # ticket -> dict(vm, ip, port, username, expiry)
_open_tunnels = {}  # username -> count


def issue_tunnel_ticket(vm, ip, port, username):
    now = time.time()
    with _lock:
        for key, entry in list(_tunnel_tickets.items()):
            if entry["expiry"] < now:
                _tunnel_tickets.pop(key, None)
        ticket = secrets.token_urlsafe(24)
        _tunnel_tickets[ticket] = {
            "vm": vm,
            "ip": ip,
            "port": port,
            "username": username,
            "expiry": now + TUNNEL_TICKET_TTL,
        }
    return ticket


def take_tunnel_ticket(ticket, vm):
    """Single use: the ticket is removed whatever the outcome."""
    with _lock:
        entry = _tunnel_tickets.pop(ticket, None) if ticket else None
    if entry is None or entry["vm"] != vm or entry["expiry"] < time.time():
        return None
    return entry


def open_tunnel_count(username):
    with _lock:
        return _open_tunnels.get(username, 0)


def tunnel_opened(username):
    """Counts an open tunnel; False when the user already has the maximum."""
    with _lock:
        if _open_tunnels.get(username, 0) >= tunnel_max_per_user():
            return False
        _open_tunnels[username] = _open_tunnels.get(username, 0) + 1
        return True


def tunnel_closed(username):
    with _lock:
        left = _open_tunnels.get(username, 0) - 1
        if left > 0:
            _open_tunnels[username] = left
        else:
            _open_tunnels.pop(username, None)


# ---- Device authorization of the workstation client ----

_requests = {}  # sha256(device_code) -> request dict


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _purge(now):
    for key, req in list(_requests.items()):
        if req["expiry"] < now:
            _requests.pop(key, None)


def normalize_user_code(code):
    return "".join(c for c in (code or "").upper() if c in _USER_CODE_ALPHABET)


def start_request(hostname, client_version, source_ip):
    """Returns (device_code, user_code), or None when too many requests are pending."""
    now = time.time()
    with _lock:
        _purge(now)
        if len(_requests) >= _MAX_PENDING_REQUESTS:
            return None
        if sum(1 for r in _requests.values() if r["source_ip"] == source_ip) >= _MAX_PENDING_PER_IP:
            return None
        device_code = secrets.token_urlsafe(32)
        while True:
            raw = "".join(secrets.choice(_USER_CODE_ALPHABET) for _ in range(8))
            if all(r["user_code"] != raw for r in _requests.values()):
                break
        _requests[_hash(device_code)] = {
            "user_code": raw,
            "hostname": hostname,
            "client_version": client_version,
            "source_ip": source_ip,
            "created_at": datetime.now(UTC).isoformat(),
            "expiry": now + CLI_REQUEST_TTL,
            "status": "pending",
            "username": None,
        }
    return device_code, f"{raw[:4]}-{raw[4:]}"


def find_request(user_code):
    code = normalize_user_code(user_code)
    now = time.time()
    with _lock:
        _purge(now)
        for req in _requests.values():
            if req["user_code"] == code:
                return dict(req)
    return None


def decide_request(user_code, username, approve):
    """Approve or deny a pending request. Returns the request, or None if unknown,
    expired or already decided."""
    code = normalize_user_code(user_code)
    now = time.time()
    with _lock:
        _purge(now)
        for req in _requests.values():
            if req["user_code"] == code and req["status"] == "pending":
                req["status"] = "approved" if approve else "denied"
                req["username"] = username
                return dict(req)
    return None


def poll_request(device_code):
    """("pending"|"denied"|"expired", None) or ("approved", token_info). An approved
    request hands out its token once, then is forgotten."""
    now = time.time()
    key = _hash(device_code or "")
    with _lock:
        _purge(now)
        req = _requests.get(key)
        if req is None:
            return "expired", None
        if req["status"] in ("pending", "denied"):
            if req["status"] == "denied":
                _requests.pop(key, None)
            return req["status"], None
        _requests.pop(key, None)
    expires_at = datetime.now(UTC) + timedelta(days=cli_token_days())
    name = f"Workstation: {req['hostname']}"[:100]
    token_id, token = create_token(req["username"], name, expires_at=expires_at.isoformat(), kind="cli")
    return "approved", {
        "token": token,
        "token_id": token_id,
        "username": req["username"],
        "expires_at": expires_at.isoformat(),
        "hostname": req["hostname"],
    }
