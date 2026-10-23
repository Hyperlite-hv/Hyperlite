"""Calls between the members of a cluster, signed with the calling member's own keys (docs/design/privilege-separation.md,
section 12.1, PR C).

A member calls another member's API in a user's name (app/core/cluster_view.py, app/core/rolling_update.py). The call
carries a peer ticket: a token hyperlite-daemon signs with this node's key (app/core/tickets.py), for one member, for
PEER_LIFETIME_S, and only for an account whose credential it checked itself: the session or API token of the request in
progress, or the task ticket of a rolling update. The member called verifies it with the public keys the caller
published in the replicated configuration (/cluster/authkey/<node>), as it pins the caller's certificate
(/cluster/tls/<node>, app/core/peer_https.py). As Proxmox VE's pveproxy forwards the user's own ticket, no member signs
for a user it has not checked; unlike it, no private key leaves its node.

A member that published no keys runs a version before this one, during a rolling update: it still gets the HS256 token
signed with the key the members share (security.create_peer_token), and that token is accepted while such a member is in
the cluster. The HS256 sessions a node signed before it ran this version stay valid LEGACY_SESSIONS_S at most."""

import base64
import contextvars
import json
import logging
import os
import secrets
import threading
import time

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from jwt import PyJWTError

from app.core import paths, tickets

logger = logging.getLogger(__name__)

STATE_DIR = "/cluster/authkey"
RETRY_S = 15  # after a round that failed
PUBLISH_S = 600
PEER_LIFETIME_S = 5 * 60
TASK_LIFETIME_S = 12 * 3600  # the longest rolling update, its own session aside
LEEWAY_S = 60  # the members' clocks may differ by that much
LEGACY_SESSIONS_S = 7 * 24 * 3600  # the longest HS256 session ("Stay signed in") a version before this one signed
PEER, TASK = "peer", "task"

# The credential of the request being served (security._current_user): what this node's daemon checks before it signs a
# call to another member in that user's name.
CREDENTIAL = contextvars.ContextVar("hyperlite_peer_credential", default=None)


class NoTicket(Exception):
    """No peer ticket for the call: no credential to show, or the daemon refused it."""


def remember(credential):
    CREDENTIAL.set(credential)


def carry(fn, credential=None):
    """`fn`, to run in another thread with this thread's credential, or `credential`: a new thread starts without it."""
    context = contextvars.copy_context()

    def run(*args, **kwargs):
        def inner():
            if credential is not None:
                CREDENTIAL.set(credential)
            return fn(*args, **kwargs)

        return context.copy().run(inner)

    return run


# ---- What each member published ----


def _client():
    from app.repositories.cfs import shadow

    return shadow._get_client()


def _path(name):
    from app.repositories.cfs import shadow

    return f"{STATE_DIR}/{shadow.component(name)}"


def _own_keys():
    return {"keys": [{k: e[k] for k in ("kid", "public", "created")} for e in tickets._public_keys()]}


def publish():
    """Publish this node's public keys when they changed. Never raises: outside a cluster, or hyperlite-cfs not
    answering, there is nobody to call."""
    from app.core import self_node
    from app.core.cfs_client import CfsError, NotFound

    try:
        payload = _own_keys()
        if not payload["keys"]:
            return
        client = _client()
        path = _path(self_node.name())
        try:
            if json.loads(client.get(path).data) == payload:
                return
        except (NotFound, ValueError):
            pass
        client.put(path, json.dumps(payload).encode())
        logger.info("Public keys published for the other members")
    except (CfsError, OSError, ValueError) as e:
        logger.debug("Public keys not published: %s", e)


def forget(name):
    """A member removed from the cluster: its keys go with it."""
    from app.core.cfs_client import CfsError, NotFound

    try:
        _client().delete(_path(name))
    except NotFound:
        pass
    except (CfsError, OSError) as e:
        logger.warning("Public keys of %s not removed: %s", name, e)


def published(name):
    """The keys `name` published, by kid; None when it published none (or they cannot be read)."""
    from app.core.cfs_client import CfsError, NotFound

    try:
        data = json.loads(_client().get(_path(name)).data)
    except NotFound:
        return None
    except (CfsError, OSError, ValueError, AttributeError) as e:
        logger.debug("Public keys of %s not read: %s", name, e)
        return None
    keys = data.get("keys") if isinstance(data, dict) else None
    return {k["kid"]: k for k in keys or [] if isinstance(k, dict) and isinstance(k.get("kid"), str)} or None


def _mode():
    from app.core import cluster_lead

    return cluster_lead.mode()


def _members():
    """The other members' names; [] outside a cluster, None when the membership cannot be read."""
    from app.core import cluster_setup, self_node

    try:
        if _mode() != "cluster":
            return []
        conf = cluster_setup.shared_conf() or {}
    except Exception as e:  # hyperlite-cfs not answering: the caller decides what an unknown membership means
        logger.debug("Members not read: %s", e)
        return None
    me = self_node.name()
    return [m["name"] for m in conf.get("members", []) if m.get("name") and m["name"] != me]


# ---- The member called ----


def verify(token):
    """The claims of a peer ticket another member signed for this node, or None."""
    from app.core import self_node

    try:
        header = jwt.get_unverified_header(token)
        unverified = jwt.decode(token, options={"verify_signature": False})
    except PyJWTError:
        return None
    if header.get("alg") != tickets.ALGORITHM or unverified.get("typ") != PEER:
        return None
    issuer = unverified.get("iss")
    if not isinstance(issuer, str) or self_node.is_self(issuer) or issuer not in (_members() or []):
        return None
    entry = (published(issuer) or {}).get(header.get("kid"))
    if entry is None:
        return None
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(entry["public"]))
        claims = jwt.decode(
            token,
            key,
            algorithms=[tickets.ALGORITHM],
            audience=self_node.name(),
            issuer=issuer,
            leeway=LEEWAY_S,
            options={"require": ["exp", "iat", "aud", "iss", "sub"]},
        )
    except (PyJWTError, ValueError, KeyError, TypeError):
        return None
    if claims["exp"] - claims["iat"] > PEER_LIFETIME_S:
        return None
    return claims


def legacy_peer_calls():
    """Whether a member may still call with the shared key: one of the others published no keys (a version before
    this one). A membership that cannot be read: yes, as before."""
    members = _members()
    if members is None:
        return True
    return any(published(m) is None for m in members)


def _cutoff_path():
    return paths.VAR / "auth-hs256-cutoff"


def record_cutoff(now=None):
    """The moment this node first ran this version, recorded once: an HS256 session signed before it stays valid
    LEGACY_SESSIONS_S after it at most."""
    path = _cutoff_path()
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{int(now if now is not None else time.time())}\n")


def legacy_session(claims, now=None):
    """Whether an HS256 token is a session this node signed before it ran this version, still in its transition."""
    try:
        cutoff = int(_cutoff_path().read_text())
    except (OSError, ValueError):
        return False
    now = now if now is not None else time.time()
    return int(claims.get("iat") or 0) < cutoff and now < cutoff + LEGACY_SESSIONS_S


# ---- The calling member ----


def _ask(op, **args):
    """`op` of hyperlite-daemon; in this process with HYPERLITE_AUTH_SIGNING=local (tests)."""
    if os.environ.get(tickets.SIGNING_ENV) == "local":
        return {"peer": peer, "delegate": delegate}[op](**args)
    from app.core import daemon_client

    return daemon_client.call(f"auth.{op}", args)


def ticket_for(node, username):
    """The token of a call to `node` in `username`'s name: a peer ticket for a member that published its keys, the
    HS256 token an older member verifies otherwise. Raises NoTicket."""
    from app.daemon.registry import DaemonError

    if published(node["name"]) is None:
        from app.core.security import create_peer_token

        return create_peer_token({"sub": username, "role": "admin"})
    credential = CREDENTIAL.get()
    if credential is None:
        raise NoTicket("no credential to show for this call")
    try:
        return _ask("peer", credential=credential, targets=[node["name"]])[node["name"]]
    except (PermissionError, DaemonError) as e:
        raise NoTicket(str(getattr(e, "detail", e))) from None


def task_ticket():
    """A task ticket for this request's credential, for a run that outlives the request (the rolling update). Raises
    NoTicket."""
    from app.daemon.registry import DaemonError

    credential = CREDENTIAL.get()
    if credential is None:
        raise NoTicket("no credential to show for this run")
    try:
        return _ask("delegate", credential=credential)
    except (PermissionError, DaemonError) as e:
        raise NoTicket(str(getattr(e, "detail", e))) from None


# ---- What hyperlite-daemon runs (app/daemon/ops/auth.py) ----


def _typ(token):
    try:
        return jwt.decode(token, options={"verify_signature": False}).get("typ")
    except PyJWTError:
        return None


def _account(credential):
    """The account `credential` proves, checked as the API checks a request; also a task ticket of this node. Raises
    PermissionError."""
    from fastapi import HTTPException

    from app.core import security

    if _typ(credential) == PEER:
        raise PermissionError("A call from another member cannot be passed on")
    claims = tickets.decode(credential)
    try:
        if claims is not None and claims.get("typ") == TASK:
            return security._session_user({k: v for k, v in claims.items() if k != "typ"})
        return security._current_user(credential, pending_ok=False)
    except HTTPException as e:
        raise PermissionError(f"The credential was refused: {e.detail}") from None


def _sign(claims, lifetime):
    now = int(time.time())
    return tickets.sign_local({**claims, "iat": now, "exp": now + lifetime, "jti": secrets.token_urlsafe(16)})


def peer(credential, targets):
    """One peer ticket per member of `targets`, for the account `credential` proves."""
    from app.core import self_node

    user = _account(credential)
    members = _members() or []
    strangers = [t for t in targets if t not in members]
    if strangers:
        raise PermissionError(f"Not a member of the cluster: {', '.join(strangers)}")
    tickets.ensure_keys()
    publish()  # a key made since: the members called can find it before they see a ticket signed with it
    me = self_node.name()
    claims = {"typ": PEER, "iss": me, "sub": user["username"], "uid": user["id"]}
    return {t: _sign({**claims, "aud": t}, PEER_LIFETIME_S) for t in targets}


def delegate(credential):
    """A task ticket for the account a session or an API token proves, an administrator's."""
    if _typ(credential) in (PEER, TASK):
        raise PermissionError("A task ticket is made from a session or an API token")
    user = _account(credential)
    if user["role"] != "admin":
        raise PermissionError("Only an administrator starts a run across the cluster")
    return _sign({"typ": TASK, "sub": user["username"], "uid": user["id"]}, TASK_LIFETIME_S)


def start_publishing():
    """In hyperlite-daemon: this node's keys made if needed and published now, then every PUBLISH_S (a rotation, a node
    that joined a cluster since). A member that published is known to verify peer tickets."""
    stop = threading.Event()

    def loop():
        while True:
            pause = PUBLISH_S
            try:
                tickets.ensure_keys()
                publish()
            except OSError as e:
                logger.warning("Session keys not made: %s", e)
            except Exception:  # the thread must survive anything, or the members would never get this node's keys
                # Soon, not at the next round: at the first start, the database is made seconds later.
                pause = min(PUBLISH_S, RETRY_S)
                logger.exception("Publishing this node's keys failed; trying again in %s s", pause)
            if stop.wait(pause):
                return

    threading.Thread(target=loop, daemon=True, name="authkey-publish").start()
    return stop
