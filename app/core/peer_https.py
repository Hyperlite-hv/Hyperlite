"""Calls between the members of a cluster over HTTPS, the certificate pinned (docs/scale-results.md, fix 2).

A call to another member's API used to open an SSH connection and start Python on it each time
(rolling_update.peer_api): about 200 ms and CPU on both ends, and past 10 at once sshd refused them (seen under 20
users: errors 500). As Proxmox does between its nodes, a member now calls the other's API directly, on its HTTPS port,
over connections kept open.

Trust: each member publishes the SHA-256 of its HTTPS certificate in the cluster's configuration (/cluster/tls/<node>,
at start and every PUBLISH_S, so a renewed certificate is published again); a peer whose certificate does not match
what it published is refused. The request carries a session token signed with the cluster's key, as the SSH path did.
When the HTTPS call cannot be made (no fingerprint published yet, the port filtered, the certificate refused), the
caller falls back to SSH (rolling_update.peer_api): nothing breaks on a network that only lets SSH through.
"""

import contextlib
import hashlib
import hmac
import http.client
import json
import logging
import queue
import ssl
import threading

logger = logging.getLogger(__name__)

STATE_DIR = "/cluster/tls"
PUBLISH_S = 600
RETRY_S = 15  # after a round that failed
POOL_SIZE = 8  # connections kept open per member

_pools = {}  # (host, port, fingerprint) -> queue of open connections
_pools_lock = threading.Lock()


class Unavailable(Exception):
    """The call could not be made over HTTPS, nothing was sent: the caller uses SSH."""


class Lost(Exception):
    """A change was sent and its answer did not come back: it may have been made, so it is not sent again (over SSH
    either)."""


def _client():
    from app.repositories.cfs import shadow

    return shadow._get_client()


def own_fingerprint():
    from app.core.cluster_setup import _certificate_fingerprint

    return _certificate_fingerprint()


def publish():
    """Publish this node's certificate fingerprint when it changed. True once the cluster has it; False when it could
    not be written (outside a cluster, without the daemon, or refused while the quorum is lost, as when a node joins).
    Never raises: the members keep using SSH meanwhile."""
    from app.core import self_node
    from app.core.cfs_client import CfsError, NotFound
    from app.repositories.cfs import shadow

    try:
        fingerprint = own_fingerprint()
        path = f"{STATE_DIR}/{shadow.component(self_node.name())}"
        client = _client()
        try:
            if json.loads(client.get(path).data).get("sha256") == fingerprint:
                return True
        except (NotFound, ValueError):
            pass
        client.put(path, json.dumps({"sha256": fingerprint}).encode())
        logger.info("HTTPS certificate fingerprint published for the other members")
        return True
    except (CfsError, OSError, ValueError) as e:
        logger.debug("Certificate fingerprint not published: %s", e)
        return False


def fingerprint_of(name):
    from app.core.cfs_client import CfsError, NotFound
    from app.repositories.cfs import shadow

    try:
        return json.loads(_client().get(f"{STATE_DIR}/{shadow.component(name)}").data).get("sha256")
    except (CfsError, NotFound, OSError, ValueError, AttributeError):
        return None


def _connect(host, port, fingerprint, timeout):
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # checked against the published fingerprint below, before any data is sent
    conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=context)
    conn.connect()
    der = conn.sock.getpeercert(binary_form=True)
    if not hmac.compare_digest(hashlib.sha256(der).hexdigest(), fingerprint):
        conn.close()
        raise Unavailable(f"{host}: certificate does not match the fingerprint it published")
    return conn


def call(node, token, method, path, body=None, timeout=60, connect_timeout=5):
    """(status, decoded body) of `method path` on `node`'s API over HTTPS. Raises Unavailable when it cannot be made
    this way (the caller then uses SSH)."""
    from app.core.cluster_setup import PORT

    fingerprint = fingerprint_of(node["name"])
    if not fingerprint:
        raise Unavailable(f"{node['name']} published no certificate fingerprint")
    key = (node["hostname"], PORT, fingerprint)
    with _pools_lock:
        pool = _pools.setdefault(key, queue.LifoQueue(POOL_SIZE))
    # Only a read goes on a kept connection, and only a read is sent twice: a change sent on a connection the peer had
    # just closed could have been made once already.
    reads = method == "GET"
    conn = None
    if reads:
        with contextlib.suppress(queue.Empty):
            conn = pool.get_nowait()
    payload = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    for attempt in (1, 2):  # a kept connection the peer closed meanwhile: once more on a new one
        sent = False
        try:
            if conn is None:
                conn = _connect(node["hostname"], PORT, fingerprint, connect_timeout)
            sent = True
            conn.timeout = timeout
            if conn.sock is not None:
                conn.sock.settimeout(timeout)
            conn.request(method, path, payload, headers)
            response = conn.getresponse()
            status, raw = response.status, response.read()
            break
        except Unavailable:
            raise
        except (OSError, http.client.HTTPException) as e:
            if conn is not None:
                conn.close()
            conn = None
            if sent and not reads:
                raise Lost(
                    f"{node['name']} did not answer {method} {path.split('?')[0]}, which may have been done: {e}"
                ) from None
            if attempt == 2 or not reads:
                raise Unavailable(f"{node['name']} over HTTPS: {e}") from None
    try:
        pool.put_nowait(conn)
    except queue.Full:
        conn.close()
    return status, _decoded(raw)


def _decoded(raw):
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return raw.decode("utf-8", "replace")


def send(node, token, path, content_type, length, chunks, timeout, connect_timeout=5):
    """(status, decoded body) of a POST whose body, `length` bytes, is read from `chunks` while it is sent: a file,
    which never crosses this node in memory. On a new connection, and once: there is no SSH way for it. Raises
    Unavailable when the member cannot be reached this way (nothing was sent), Lost once something was."""
    from app.core.cluster_setup import PORT

    fingerprint = fingerprint_of(node["name"])
    if not fingerprint:
        raise Unavailable(f"{node['name']} published no certificate fingerprint")
    try:
        conn = _connect(node["hostname"], PORT, fingerprint, connect_timeout)
    except (OSError, http.client.HTTPException) as e:
        raise Unavailable(f"{node['name']} over HTTPS: {e}") from None
    headers = {"Authorization": f"Bearer {token}", "Content-Type": content_type, "Content-Length": str(length)}
    try:
        conn.sock.settimeout(timeout)
        conn.request("POST", path, chunks, headers)
        response = conn.getresponse()
        status, raw = response.status, response.read()
    except (OSError, http.client.HTTPException) as e:
        raise Lost(f"{node['name']} did not answer POST {path.split('?')[0]}, which may have been done: {e}") from None
    finally:
        conn.close()
    return status, _decoded(raw)


def start():
    """Publish now, then every PUBLISH_S (a renewed certificate, a node that joined a cluster since)."""
    stop = threading.Event()

    def once():
        """The pause before the next round: soon after a failure (at the first start the database is made seconds
        later)."""
        try:
            # Not written (a node that just joined publishes while the cluster reloads, without the quorum): soon
            # again, or the members would relay nothing to it until the next round.
            return PUBLISH_S if publish() else min(PUBLISH_S, RETRY_S)
        except Exception:  # the thread must survive anything, or the members would call this node over SSH for good
            pause = min(PUBLISH_S, RETRY_S)
            logger.exception("Publishing the certificate fingerprint failed; trying again in %s s", pause)
            return pause

    def loop():
        pause = once()
        while not stop.wait(pause):
            pause = once()

    threading.Thread(target=loop, daemon=True, name="peer-https").start()
    return stop
