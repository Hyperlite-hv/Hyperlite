"""Several clusters and sites from one view (docs/design/multi-cluster.md, step 1), as Proxmox Datacenter Manager, but
in every Hyperlite: any cluster is the view of the others, with no server to add, and every node of it serves the view
(its remotes are in the replicated configuration).

A remote is another Hyperlite cluster (or a single node): the address of each of its nodes, each with the SHA-256 of
its HTTPS certificate pinned, and a token per use of a dedicated account there with the least rights (`view`: a global
viewer). The remote checks every call with its own permissions and records it in its own audit log.

The view asks every remote at once, each call with a timeout, and keeps the answers CACHE_S, so a page opened by
several administrators asks once; a remote that does not answer is named, never waited for.
"""

import contextlib
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$")
USES = ("view", "manage")
CACHE_S = 15
WORKERS = 16
CRASHED = ("crashed",)

_cache = {}  # (remote, path) -> (monotonic time, answer)
_cache_lock = threading.Lock()


class SiteError(Exception):
    """`sent`: the request reached the remote (an action may have run there), so it is not tried on another endpoint."""

    def __init__(self, message, status=422, sent=False):
        super().__init__(message)
        self.message, self.status, self.sent = message, status, sent


def _repo():
    from app.repositories import registry

    return registry.sites().sync


def _now():
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---- This cluster, as a remote sees it ----


def summary():
    """This cluster at a glance, in one answer (what a view asks every remote): its name, version, quorum, its nodes
    with their VMs and free memory, its VMs' counts, and its problems. From the status channel, which every node
    already sends (app/core/statd.py); this node's own inventory outside a cluster."""
    from app.core import cluster_lead, self_node, statd, version

    me = self_node.name()
    table = statd.table()
    members, name = [me], None
    if cluster_lead.mode(record=False) == "cluster":
        from app.core import cluster_setup

        try:
            conf = cluster_setup.shared_conf() or {}
            name = conf.get("cluster")
            members = sorted({m["name"] for m in conf.get("members", [])} | {me})
        except Exception as e:  # the daemon does not answer: what the statuses say
            logger.info("Cluster members not read for the summary: %s", e)
            members = sorted(set(table) | {me})
    if me not in table:
        try:
            own = statd.summary()
            table[me] = {"vms": own["vms"], "age_s": 0.0, "stale": False, "mem_free_mb": own.get("mem_free_mb"),
                         "load": own.get("load")}  # fmt: skip
        except Exception as e:
            logger.info("This node's inventory not read for the summary: %s", e)
    nodes, running, stopped, crashed = [], 0, 0, 0
    for node in members:
        status = table.get(node)
        vms = (status or {}).get("vms") or []
        up = sum(1 for v in vms if v.get("state") == "active")
        bad = sum(1 for v in vms if v.get("state") in CRASHED)
        running, stopped, crashed = running + up, stopped + len(vms) - up, crashed + bad
        host = ((status or {}).get("load") or {}).get("host") or {}
        nodes.append({
            "name": node,
            "online": bool(status) and not status.get("stale"),
            "age_s": (status or {}).get("age_s"),
            "vms_running": up,
            "vms_total": len(vms),
            "mem_free_mb": (status or {}).get("mem_free_mb"),
            "mem_total_mb": host.get("mem_total_mb"),
            "cpu_pct": host.get("cpu_pct"),
            "cpus": host.get("cpus"),
        })  # fmt: skip
    problems = []
    reason = cluster_lead.read_only_reason()
    if reason:
        problems.append(reason)
    problems += [f"{n['name']} does not answer" for n in nodes if not n["online"]]
    if crashed:
        problems.append(f"{crashed} VM(s) crashed")
    return {
        "cluster": name,
        "node": me,
        "version": version.STARTUP_VERSION,
        "read_only": bool(reason),
        "nodes": nodes,
        "vms": {"running": running, "stopped": stopped, "crashed": crashed},
        "problems": problems,
    }


def fingerprints():
    """[{"node", "address", "fingerprint"}] of every member, as the members publish their certificates
    (app/core/peer_https.py): what a view pins for each endpoint. A viewer reads it (a certificate is shown to anyone
    who connects); /cluster, which also names the members, is an administrator's."""
    from app.core import cluster_lead, peer_https, self_node

    me = self_node.name()
    addresses = {}
    if cluster_lead.mode(record=False) == "cluster":
        from app.core import cluster_setup

        try:
            addresses = {m["name"]: m["address"] for m in (cluster_setup.shared_conf() or {}).get("members", [])}
        except Exception as e:  # the daemon does not answer: this node alone
            logger.info("Cluster members not read for the fingerprints: %s", e)
    out = []
    for node in sorted(set(addresses) | {me}):
        found = None
        if node == me:
            with contextlib.suppress(
                OSError, ValueError
            ):  # no certificate of its own (served over HTTP behind a proxy)
                found = peer_https.own_fingerprint()
        else:
            found = peer_https.fingerprint_of(node)
        out.append({"node": node, "address": addresses.get(node), "fingerprint": found})
    return out


# ---- Remotes ----

HOST = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$|^\[?[0-9A-Fa-f:]+\]?$")
TIMEOUT_S = 10
API = "/api/v1"


def check_address(address, port):
    address = (address or "").strip()
    if not HOST.match(address):
        raise SiteError("Invalid address: a host name or an IP address")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise SiteError("Invalid port")
    return address.strip("[]"), port


class _Client:
    """Calls to one endpoint of a remote over HTTPS, its certificate pinned, with a token."""

    def __init__(self, endpoint, token):
        self.endpoint, self.token, self._conn = endpoint, token, None

    def json(self, path, method="GET", body=None):
        import http.client
        import json

        from app.core import peer_https

        where = f"{self.endpoint['address']}:{self.endpoint['port']}"
        try:
            if self._conn is None:
                self._conn = peer_https._connect(
                    self.endpoint["address"], self.endpoint["port"], self.endpoint["fingerprint"], TIMEOUT_S
                )
        except peer_https.Unavailable as e:
            self.close()
            raise SiteError(f"{where}: {e}", 502) from None
        except OSError as e:
            self.close()
            raise SiteError(f"{where} does not answer: {e}", 502) from None
        headers = {"Authorization": f"Bearer {self.token}"}
        payload = None
        if body is not None:
            payload, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        try:
            self._conn.sock.settimeout(TIMEOUT_S)
            self._conn.request(method, API + path, body=payload, headers=headers)
            response = self._conn.getresponse()
            status, raw = response.status, response.read()
        except (OSError, http.client.HTTPException) as e:
            self.close()
            raise SiteError(f"{where} stopped answering: {e}", 502, sent=method != "GET") from None
        if status in (401, 403):
            raise SiteError(f"{where} refused the token ({status}): {_detail(raw)}", 403, sent=True)
        if status in (404, 409, 422):  # the remote's own refusal, passed on as it gave it
            raise SiteError(f"{where}: {_detail(raw)}", status, sent=True)
        if not 200 <= status < 300:
            raise SiteError(f"{where} answered {status}: {_detail(raw)}", 502, sent=True)
        try:
            return json.loads(raw)
        except ValueError:
            raise SiteError(f"{where} gave an answer that is not JSON", 502, sent=True) from None

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def _detail(raw):
    import json

    try:
        detail = json.loads(raw).get("detail")
    except (ValueError, AttributeError):
        return raw[:200].decode(errors="replace")
    return (detail if isinstance(detail, str) else json.dumps(detail))[:300]


def _client(endpoint, token):
    return _Client(endpoint, token)


def probe(address, port):
    """The SHA-256 of the certificate the address presents, to be compared with the one its dashboard shows."""
    import hashlib
    import socket
    import ssl

    address, port = check_address(address, port)
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # what is checked is the fingerprint, by a person, then pinned
    try:
        with socket.create_connection((address, port), timeout=TIMEOUT_S) as raw, context.wrap_socket(raw) as tls:
            der = tls.getpeercert(binary_form=True)
    except OSError as e:
        raise SiteError(f"{address}:{port} does not answer over HTTPS: {e}", 502) from None
    return hashlib.sha256(der).hexdigest()


def _token(remote, use):
    from app.core import secrets_crypto

    sealed = _repo().get_token(remote, use)
    if not sealed:
        raise SiteError(f"No {use} token for the remote '{remote}'", 409)
    try:
        return secrets_crypto.decrypt(sealed)
    except secrets_crypto.SecretUnreadable:
        raise SiteError(f"The {use} token of '{remote}' cannot be read on this node", 409) from None


def call(remote, path, use="view", method="GET", body=None, on=None):
    """The JSON answer of <method> <API>path on the remote, from the first of its endpoints that answers. `on`: the
    remote node a VM runs on: its own endpoint is asked first, and any other is asked with ?node=<on>, as the
    remote's dashboard asks it. An action is tried on another endpoint only when it did not reach the first."""
    import urllib.parse

    endpoints = _repo().endpoints(remote)
    if not endpoints:
        raise SiteError(f"No remote '{remote}'", 404)
    token = _token(remote, use)
    if on:
        endpoints = sorted(endpoints, key=lambda e: e.get("node") != on)
    last = None
    for endpoint in endpoints:
        target = path
        if on and endpoint.get("node") != on:
            target += ("&" if "?" in path else "?") + "node=" + urllib.parse.quote(on, safe="")
        client = _client(endpoint, token)
        try:
            return client.json(target, method, body)
        except SiteError as e:
            last = e
            if e.status in (403, 404, 409, 422) or e.sent:  # the remote answered, or the action may have run
                raise
        finally:
            client.close()
    raise SiteError(last.message if last else f"'{remote}' does not answer", 502)


def _cached(remote, path, use="view"):
    key = (remote, path, use)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_S:
            return hit[1]
    answer = call(remote, path, use)
    with _cache_lock:
        _cache[key] = (time.monotonic(), answer)
    return answer


def forget_cache():
    with _cache_lock:
        _cache.clear()


def add(name, address, port, fingerprint, view_token, username, manage_token=None):
    """Record a remote from one of its nodes, its certificate compared with the one its dashboard shows: the other
    members and their certificates are read from it, so a call goes to whichever member answers."""
    from app.core import secrets_crypto

    if not NAME.match(name or ""):
        raise SiteError("Invalid name: letters, digits, '.', '-' and '_', 63 characters at most")
    if _repo().get_remote(name):
        raise SiteError(f"A remote '{name}' already exists", 409)
    address, port = check_address(address, port)
    fingerprint = (fingerprint or "").replace(":", "").replace(" ", "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise SiteError("The fingerprint is the SHA-256 of the remote's certificate: 64 hexadecimal digits")
    if not view_token:
        raise SiteError("Give the API token of a dedicated viewer account on the remote")
    if manage_token and len(manage_token) > 4096:
        raise SiteError("A token is at most 4096 characters")
    first = {"address": address, "port": port, "fingerprint": fingerprint, "node": None}
    if manage_token:  # accepted by the remote too, before anything is saved
        check = _client(first, manage_token)
        try:
            check.json("/sites/summary")
        finally:
            check.close()
    client = _client(first, view_token)
    try:
        remote_summary = client.json("/sites/summary")
        try:
            members = client.json("/sites/fingerprints")
        except SiteError:  # a node that cannot say: its one endpoint
            members = []
    finally:
        client.close()
    endpoints = [{**first, "node": remote_summary.get("node")}]
    for member in members if isinstance(members, list) else []:
        member_address, pin = member.get("address"), (member.get("fingerprint") or "").lower()
        if member_address and member_address != address and re.fullmatch(r"[0-9a-f]{64}", pin):
            with contextlib.suppress(SiteError):  # an address that is not one: that member is left out
                endpoints.append({"address": check_address(member_address, port)[0], "port": port,
                                  "fingerprint": pin, "node": member.get("node")})  # fmt: skip
    _repo().save_remote(name, username, _now(), endpoints)
    _repo().save_token(name, "view", secrets_crypto.encrypt(view_token))
    if manage_token:
        _repo().save_token(name, "manage", secrets_crypto.encrypt(manage_token))
    forget_cache()
    return describe(_repo().get_remote(name))


def set_token(name, use, token):
    """Give the remote a token for `use` (checked there first), or take it back (`token` None)."""
    from app.core import secrets_crypto

    if use not in USES:
        raise SiteError(f"Unknown use: {use}", 404)
    if not _repo().get_remote(name):
        raise SiteError(f"No remote '{name}'", 404)
    if not token:
        if use == "view":
            raise SiteError("The view token is what the view reads with: replace it, or remove the remote", 409)
        _repo().delete_token(name, use)
    else:
        if len(token) > 4096:
            raise SiteError("A token is at most 4096 characters")
        _check_token(name, token)
        _repo().save_token(name, use, secrets_crypto.encrypt(token))
    forget_cache()
    return describe(_repo().get_remote(name))


def _check_token(name, token):
    """The remote accepts the token (any of its endpoints that answers)."""
    last = None
    for endpoint in _repo().endpoints(name):
        client = _client(endpoint, token)
        try:
            client.json("/sites/summary")
            return
        except SiteError as e:
            last = e
            if e.status == 403:
                raise
        finally:
            client.close()
    raise last or SiteError(f"'{name}' does not answer", 502)


def describe(row):
    endpoints = _repo().endpoints(row["name"])
    return {
        "name": row["name"],
        "created_by": row.get("created_by"),
        "created_at": row.get("created_at"),
        "endpoints": [{k: e[k] for k in ("address", "port", "fingerprint", "node")} for e in endpoints],
        "uses": sorted(_repo().token_uses(row["name"])),
    }


def remove(name):
    if not _repo().get_remote(name):
        raise SiteError(f"No remote '{name}'", 404)
    _repo().delete_remote(name)
    forget_cache()


def list_remotes():
    return [describe(r) for r in _repo().list_remotes()]


# ---- The view ----


def overview():
    """This cluster and every remote, each with its summary or why it is missing: the Sites page, one call per remote,
    all at once."""
    rows = _repo().list_remotes()

    def one(row):
        try:
            return {
                "name": row["name"],
                "self": False,
                "reachable": True,
                "summary": _cached(row["name"], "/sites/summary"),
            }
        except SiteError as e:
            return {"name": row["name"], "self": False, "reachable": False, "problem": e.message}

    with ThreadPoolExecutor(max_workers=max(1, min(WORKERS, len(rows)))) as pool:
        remotes = list(pool.map(one, rows))
    own = summary()
    return [{"name": own["cluster"] or own["node"], "self": True, "reachable": True, "summary": own}, *remotes]


LISTS = {"vms": "/vms?cluster=true", "nodes": "/nodes", "storage": "/storage"}  # what a viewer may read


def remote_list(remote, what):
    """One remote's VMs, nodes or storage pools, as its own API answers them."""
    if what not in LISTS:
        raise SiteError(f"Unknown list: {what}", 404)
    if not _repo().get_remote(remote):
        raise SiteError(f"No remote '{remote}'", 404)
    return _cached(remote, LISTS[what])


def search(query):
    """VMs of every remote whose name, tag or address holds `query`: [{"site", "vm", "node", "state", ...}], and the
    remotes that did not answer."""
    needle = (query or "").strip().lower()
    if len(needle) < 2:
        raise SiteError("Search for at least 2 characters")
    rows = _repo().list_remotes()

    def one(row):
        try:
            answer = _cached(row["name"], LISTS["vms"])
        except SiteError:
            return row["name"], None
        items = answer.get("items", answer) if isinstance(answer, dict) else answer
        return row["name"], items if isinstance(items, list) else []

    found, missing = [], []
    with ThreadPoolExecutor(max_workers=max(1, min(WORKERS, len(rows)))) as pool:
        for site, vms in pool.map(one, rows):
            if vms is None:
                missing.append(site)
                continue
            for vm in vms:
                fields = [str(vm.get("name") or ""), str(vm.get("ip") or ""), *[str(t) for t in vm.get("tags") or []]]
                if any(needle in f.lower() for f in fields):
                    found.append({"site": site, "vm": vm.get("name"), "node": vm.get("on") or vm.get("node"),
                                  "state": vm.get("state"), "ip": vm.get("ip")})  # fmt: skip
    return {"items": found, "total": len(found), "unreachable": missing}


# ---- Actions (step 2): with the remote's `manage` token, checked there with that account's rights ----

# action -> (the remote's route under /vms/{vm}, its query): what the remote's own dashboard calls.
ACTIONS = {
    "start": ("start", ""),
    "shutdown": ("stop", ""),
    "stop": ("stop", "?force=true"),
    "reboot": ("restart", ""),
    "reset": ("restart", "?force=true"),
}


def _vm_path(vm, route):
    import urllib.parse

    if not vm or len(vm) > 255:
        raise SiteError("Invalid VM name")
    return f"/vms/{urllib.parse.quote(vm, safe='')}/{route}"


def act(remote, vm, action, on=None):
    """Run a power action on a VM of the remote, as its own dashboard does: the remote's answer (its task)."""
    if action not in ACTIONS:
        raise SiteError(f"Unknown action: {action}", 404)
    route, query = ACTIONS[action]
    try:
        return call(remote, _vm_path(vm, route) + query, "manage", "POST", on=on)
    finally:
        forget_cache()


def migrate(remote, vm, target_node, on=None):
    """Move a VM of the remote to another of its nodes (a live migration there, its checks included)."""
    if not target_node or len(target_node) > 63:
        raise SiteError("Name the remote node to move the VM to")
    if target_node == on:
        raise SiteError(f"{vm} already runs on {target_node}")
    try:
        return call(remote, _vm_path(vm, "migrate"), "manage", "POST", {"target_node": target_node}, on=on)
    finally:
        forget_cache()
