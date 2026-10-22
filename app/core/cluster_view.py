"""The cluster's activity seen from any node: the tasks and the audit log of every member, as Proxmox shows them.

Each node keeps its own tasks and audit entries (they are history, not configuration: hyperlite-cfs does not hold them,
app/repositories/cfs/tables.py). A task is recorded on the node that ran it, which is the node whose dashboard started
it or the node that hosts the VM of a scheduled job. So, in a cluster, the node that answers asks every other member
for the same list (their own API, over the cluster's SSH trust, as the rolling update does: app/core/rolling_update.py)
and merges the answers. A member that is down or does not answer in time is named, never waited for long.

Each row says which node holds it ("on"), so that its detail, its log or its cancellation goes to that node.
Outside a cluster nothing changes: the lists are this node's own.
"""

import json
import logging
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from fastapi import Query
from starlette.requests import HTTPConnection, Request

from app.core import cluster_lead, node_health, peer_auth, self_node

logger = logging.getLogger(__name__)

TIMEOUT_S = 8  # one member's answer
CONNECT_TIMEOUT_S = 3


def _mode():
    return cluster_lead.mode()


class PeerError(Exception):
    """A member did not answer (status 0), with the reason."""


def members():
    """The other members of the cluster, as registered nodes (name, address, SSH user and port), in a stable order.
    Empty outside a cluster. A node registered over SSH only (no Hyperlite on it) is not a member."""
    if _mode() != "cluster":
        return []
    from app.core import cluster, cluster_setup

    try:
        conf = cluster_setup.shared_conf() or {}
    except Exception as e:  # the daemon does not answer: this node's own lists only, said in the log
        logger.warning("Cluster members unknown: %s", e)
        return []
    names = {m["name"] for m in conf.get("members", [])} - {self_node.name()}
    return sorted((n for n in cluster.list_nodes() if n["name"] in names), key=lambda n: n["name"])


def _call(node, username, method, path):
    from app.core import rolling_update

    try:
        status, body = rolling_update.peer_api(
            node, username, method, path, timeout=TIMEOUT_S, connect_timeout=CONNECT_TIMEOUT_S
        )
    except rolling_update.PeerUnreachable as e:
        raise PeerError(str(e)) from None
    if status == 0:
        raise PeerError(rolling_update._detail(body))
    return status, body


def gather(username, path, params):
    """Ask every other member GET path?params for `username`: ({node: body}, [names that did not answer]).
    Members answer in parallel; one that is offline is not asked."""
    nodes = members()
    if not nodes:
        return {}, []
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    full = f"{path}?{query}" if query else path
    answers, missing = {}, []
    # A member seen down is not asked, so a list never waits for a node that is really gone (app/core/node_health.py).
    seen_down = node_health.down(nodes)

    def ask(node):
        if node["name"] in seen_down:
            return node["name"], None
        try:
            status, body = _call(node, username, "GET", full)
        except Exception as e:  # unreachable, timed out: the list says so
            logger.info("%s did not answer %s: %s", node["name"], path, e)
            return node["name"], None
        return node["name"], body if status == 200 else None

    with ThreadPoolExecutor(max_workers=min(8, len(nodes))) as pool:
        for name, body in pool.map(peer_auth.carry(ask), nodes):
            if body is None:
                missing.append(name)
            else:
                answers[name] = body
    return answers, missing


def peer(on):
    """The member named `on`, when it is another node of the cluster; None for this node, nothing or outside a
    cluster. LookupError for a name that is not a member."""
    if not on or self_node.is_self(on):
        return None
    for node in members():
        if node["name"] == on:
            return node
    raise LookupError(on)


def forward(node, username, method, path):
    """(status, body) of one call to another member, for a row it holds."""
    return _call(node, username, method, path)


def node_filter(value):
    """A node filter as the other members understand it: "local" is this node, by its name."""
    return self_node.to_db(value) if value else value


def merge(local, answers, key, reverse=True, limit=None):
    """This node's rows and the members' rows in one list, each marked with the node that holds it, sorted on `key`
    (newest first by default) and cut at `limit`."""
    me = self_node.name()
    rows = [{**r, "on": me} for r in local]
    for name, body in answers.items():
        body = body.get("items", []) if isinstance(body, dict) else body  # a member's list (v1: {items, total})
        rows += [{**r, "on": name} for r in body if isinstance(r, dict)] if isinstance(body, list) else []
    rows.sort(key=lambda r: (r.get(key) is not None, r.get(key) or ""), reverse=reverse)
    return rows[:limit] if limit else rows


# ---- Requests about another member's guests or history ----
#
# What a node keeps for itself (its backups, the VMs it hosts) is acted on by that node: a request that names another
# member is sent to it, in the name of the same user, and its answer comes back as it is. That member checks the
# rights again and records the audit entry and the task. This node checked the session first.

MAX_BODY = 1 << 20  # JSON bodies only (a backup request, a restore): uploads are never forwarded


class Forwarded(Exception):  # not an error: the answer of another member, returned by app/main.py
    """Another member's answer to a forwarded request."""

    def __init__(self, status, body):
        super().__init__(status)
        self.status, self.body = status, body


def answer(status, body, node_name):
    """(status, body) for the browser. Never a 401, which would sign the dashboard out of this node, and a member
    that fails or does not answer reads as a 502 naming it."""
    if status == 401 or status >= 500:
        return 502, {"detail": f"{node_name} refused the request ({status})"}
    if status == 0:
        return 502, {"detail": f"{node_name} does not answer"}
    return status, body


def forward_to(param, refuse_others=None):
    """A FastAPI dependency: when the query parameter `param` names another member, the whole request goes to it
    (minus that parameter) and its answer is returned (Forwarded, app/main.py). This node, nothing, or outside a
    cluster: the endpoint runs here. A node that is not a member (registered over SSH only, no Hyperlite on it):
    refused with `refuse_others` (a sentence) when given, else the endpoint runs here and handles it its own way."""
    from fastapi import Depends, HTTPException, Request

    from app.core.security import get_current_user

    # The parameter is declared, so the API's description lists it on every route that takes this dependency.
    async def dependency(
        request: Request,
        user: dict = Depends(get_current_user),
        _named: str | None = Query(None, alias=param, description=NODE_DOC),
    ):
        name = request.query_params.get(param)
        try:
            node = peer(name)
        except LookupError:
            if refuse_others:
                raise HTTPException(status_code=409, detail=refuse_others.format(node=name)) from None
            return None
        if node is None:
            return None
        await _send(request, param, node, user)

    return dependency


# ---- ?node= on everything a node holds (docs/design/terraform-provider.md, section 8) ----
#
# A VM, a template, a network, a storage pool and an ISO image live on one node: a request about them that names
# another member goes to that member, which does the work itself, with its own locks, tasks and audit entries, as
# Proxmox's pveproxy passes a request to the node that owns the guest. One rule for every route of their routers
# (`on_node`, a dependency of each router), instead of some routes reaching another node through libvirt over SSH and
# others acting here only. A node registered over SSH only (no Hyperlite on it) is still reached through libvirt by the
# routes that do so.

NODE_DOC = "The member that holds the object (absent, `local` or this node: here): the request is sent to it"


def stays_here(endpoint):
    """Marks an endpoint that `?node=` does not send to another member: it answers here about that member's object,
    because what follows comes back here (a console ticket: its WebSocket opens on this node)."""
    endpoint.stays_here = True
    return endpoint


async def on_node(connection: HTTPConnection, node: str | None = Query(None, description=NODE_DOC)):
    """The routers' dependency: `?node=` naming another member sends the request there (`forward_to`). A WebSocket is
    never sent (its ticket route says which node it is for), nor an endpoint marked `stays_here`."""
    if not node or connection.scope["type"] != "http" or not isinstance(connection, Request):
        return None
    if getattr(connection.scope.get("endpoint"), "stays_here", False):
        return None
    try:
        member = peer(node)
    except LookupError:
        # Not a member: a node registered over SSH only, or no node at all. An endpoint that takes `node` itself
        # reaches such a node through libvirt, as before; any other would act here, on an object of the same name.
        if _takes_node(connection.scope.get("endpoint")):
            return None
        from fastapi import HTTPException

        raise HTTPException(
            status_code=409, detail=f"{node} is not a member of this cluster: this cannot be done there from this node"
        ) from None
    if member is None:
        return None
    from app.core.security import get_current_user, oauth2_scheme

    user = await get_current_user(await oauth2_scheme(connection))
    await _send(connection, "node", member, user)


def _takes_node(endpoint):
    """Whether the endpoint has a `node` parameter of its own (it then handles a node that is not a member)."""
    import inspect

    try:
        return "node" in inspect.signature(endpoint).parameters
    except (TypeError, ValueError):
        return False


# The answer of a write can take as long as the work: a VM stopped cleanly, a large disk deleted. A read is short.
SEND_TIMEOUT_S = {"GET": 60}
SEND_WRITE_TIMEOUT_S = 1800


async def _send(request, param, node, user):
    """Sends `request`, minus the `param` query parameter, to the member `node` in the name of `user`; raises
    Forwarded with its answer."""
    from fastapi import HTTPException
    from fastapi.concurrency import run_in_threadpool

    query = urllib.parse.urlencode([(k, v) for k, v in request.query_params.multi_items() if k != param])
    path = request.scope.get("raw_path", b"").decode("latin-1") or urllib.parse.quote(request.url.path)
    path = path.split("?", 1)[0] + (f"?{query}" if query else "")
    if path.startswith("/api/v1/"):
        path = path[len("/api/v1") :]  # the member's API prefix is added by peer_api, as for every call
    body = None
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if not request.headers.get("content-type", "application/json").startswith("application/json"):
            # A file (an ISO image) would cross this node in memory: it is sent to the member itself.
            raise HTTPException(
                status_code=422, detail=f"A file cannot be sent on to {node['name']}: send it to {node['name']} itself"
            )
        raw = await request.body()
        if len(raw) > MAX_BODY:
            raise HTTPException(status_code=413, detail="Request too large to send to another node")
        if raw:
            try:
                body = json.loads(raw)
            except ValueError:
                raise HTTPException(status_code=422, detail="Invalid JSON body") from None
    timeout = SEND_TIMEOUT_S.get(request.method, SEND_WRITE_TIMEOUT_S)
    try:
        status, reply = await run_in_threadpool(
            rolling_update_call, node, user["username"], request.method, path, body, timeout
        )
    except PeerError:
        status, reply = 0, None
    raise Forwarded(*answer(status, reply, node["name"]))


def rolling_update_call(node, username, method, path, body, timeout=60):
    from app.core import rolling_update

    try:
        return rolling_update.peer_api(node, username, method, path, body=body, timeout=timeout, connect_timeout=5)
    except rolling_update.PeerUnreachable as e:
        raise PeerError(str(e)) from None
