"""Rolling update of a cluster (docs/design/updates-1.0.md, section 9): node after node, each emptied first.

Started from any node, which updates the others one after another and itself last. For each node:
1. maintenance mode on, and its running VMs live-migrated away (the node drain of the Nodes page). VMs that cannot
   move (local disks, passthrough) keep running through the package update, as on a single node; they only matter
   for the reboot: the administrator chose beforehand to stop them for it, or to leave that node's reboot for later;
2. the update of section 5 on that node (POST /update/apply), followed until it ends and the new version answers;
3. a reboot when one is needed, then waiting for the node to come back;
4. maintenance mode off. Its VMs are not moved back.
Any failure stops the run on that node, which stays in maintenance; the nodes already done stay done.

The other nodes are driven through their own API, reached over the cluster's SSH trust (host keys pinned, as for
migrations) on their loopback address: no certificate to trust, and the session token, signed with the cluster's
shared key in the name of the administrator who started the run, goes through SSH's standard input, never on a
command line. This node updates itself last: its own update restarts the service that runs this job, so the job
ends there, and the next start of the service takes this node out of maintenance once the new version runs.
"""

import json
import logging
import threading
import time

from app.core import cluster, maintenance, self_node
from app.core.audit import log_action
from app.core.security import create_access_token
from app.core.tasks import finish_task, task_log, update_task_progress

logger = logging.getLogger(__name__)

LOCK = "hyperlite-rolling-update"
LOCK_TTL_S = 120
SELF_PENDING = "rolling_update_self"  # app setting: this node updates itself as the last step of a run
LOOPBACK = "127.0.0.1"
API_URL = f"https://{LOOPBACK}:8000"  # each node's own API, on the node itself (not a repository address)
UPDATE_TIMEOUT_S = 3600
RESTART_TIMEOUT_S = 300
REBOOT_TIMEOUT_S = 1200
POLL_S = 5

# Run on a node with its own Python (every Debian has python3): one request to its API on the loopback address, the
# request (token included) read from standard input. -I: nothing from the current directory or the environment.
_PEER_CALL = r"""
import json, ssl, sys, urllib.error, urllib.request
r = json.load(sys.stdin)
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE  # 127.0.0.1 on the node itself, reached over the pinned SSH connection
data = json.dumps(r["body"]).encode() if r["body"] is not None else None
req = urllib.request.Request(r["url"], data=data, method=r["method"],
    headers={"Authorization": "Bearer " + r["token"], "Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, context=ctx, timeout=r["timeout"]) as x:
        status, body = x.status, x.read()
except urllib.error.HTTPError as e:
    status, body = e.code, e.read()
except Exception as e:
    status, body = 0, json.dumps({"detail": str(e)}).encode()
print(json.dumps({"status": status, "body": body.decode("utf-8", "replace")}))
"""


class RollingError(Exception):
    """Why the run stopped, for the administrator."""


class PeerUnreachable(RollingError):
    pass


def _ssh(node, args, stdin=None, timeout=60):
    import subprocess

    target = f"{node['ssh_user']}@{node['hostname']}"
    return subprocess.run(
        ["ssh", *cluster.node_ssh_options(["-o", "ConnectTimeout=10"]), "-p", str(node["ssh_port"]), target, *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def peer_api(node, username, method, path, body=None, timeout=60):
    """(status, decoded body) of a call to `node`'s own API. Status 0: the API did not answer (restarting)."""
    request = {
        "url": API_URL + path,
        "method": method,
        "body": body,
        "timeout": timeout,
        "token": create_access_token({"sub": username, "role": "admin"}),
    }
    try:
        r = _ssh(node, ["python3", "-I", "-c", _PEER_CALL], stdin=json.dumps(request), timeout=timeout + 30)
    except Exception as e:  # ssh missing, timed out: the node is not reachable
        raise PeerUnreachable(f"{node['name']} is not reachable over SSH: {e}") from None
    if r.returncode != 0:
        raise PeerUnreachable(f"{node['name']} is not reachable over SSH: {r.stderr.strip()[:300]}")
    try:
        answer = json.loads(r.stdout)
        status, text = int(answer["status"]), answer["body"]
    except (ValueError, KeyError, TypeError):
        raise PeerUnreachable(f"{node['name']} gave an unexpected answer: {r.stdout.strip()[:200]}") from None
    try:
        return status, json.loads(text) if text else None
    except ValueError:
        return status, text


def _detail(body):
    return body.get("detail") if isinstance(body, dict) else str(body)[:300]


def _boot_id(node):
    r = _ssh(node, ["cat", "/proc/sys/kernel/random/boot_id"], timeout=30)
    return r.stdout.strip() if r.returncode == 0 else None


def peers():
    """The other members, in a stable order."""
    return sorted(cluster.list_nodes(), key=lambda n: n["name"])


# ---- The plan, shown before starting ----


def node_plan(node, username, local_check=None):
    """What updating `node` (None: this node) would do: versions, Debian packages, reboot, and its VMs."""
    name = node["name"] if node else self_node.name()
    label = node["name"] if node else maintenance.LOCAL
    out = {"nom": name, "local": node is None, "joignable": True}
    try:
        if node is None:
            check, channel = local_check()
        else:
            status, check = peer_api(node, username, "GET", "/update/check", timeout=120)
            if status != 200:
                raise RollingError(f"{name}: the update check failed ({_detail(check)})")
            _, ch = peer_api(node, username, "GET", "/update/channel")
            channel = ch.get("canal") if isinstance(ch, dict) else None
    except RollingError as e:
        return {**out, "joignable": False, "erreur": str(e)}
    sys_ = check.get("systeme") or {}
    out.update(
        {
            "canal": channel,
            "verifiable": bool(check.get("verifiable")),
            "erreur": None if check.get("verifiable") else check.get("erreur"),
            "version": check.get("commit_local"),
            "candidate": check.get("commit_distant"),
            "hyperlite_a_jour": bool(check.get("a_jour")),
            "paquets": sys_.get("paquets", 0),
            "redemarrage": bool(sys_.get("redemarrage_prevu") or sys_.get("redemarrage_requis")),
        }
    )
    out["a_faire"] = bool(out["verifiable"] and (not out["hyperlite_a_jour"] or out["paquets"] or out["redemarrage"]))
    try:
        from app.core.libvirt_utils import open_conn

        conn = open_conn(maintenance.conn_key(label))
        try:
            vms = maintenance.plan(conn, None, None)
        finally:
            conn.close()
        out["vms_a_migrer"] = vms["migrables"]
        out["vms_bloquees"] = vms["non_migrables"]
    except Exception as e:  # libvirt unreachable: the node cannot be emptied
        out["vms_a_migrer"], out["vms_bloquees"] = [], []
        out["joignable"] = False
        out["erreur"] = f"{name}: its VMs cannot be listed ({e})"
    return out


def cluster_plan(username, local_check):
    """Every node's plan, the others in order then this node, checked in parallel (each check reads the
    repositories), and what prevents starting."""
    others = peers()
    results = [None] * (len(others) + 1)

    def run(i, node):
        results[i] = node_plan(node, username, local_check)

    threads = [threading.Thread(target=run, args=(i, n), daemon=True) for i, n in enumerate([*others, None])]
    for t in threads:
        t.start()
    for t in threads:
        t.join(300)
    nodes = [r for r in results if r is not None]
    problems = []
    for n in nodes:
        if not n["joignable"] or not n.get("verifiable", False):
            problems.append(n.get("erreur") or f"{n['nom']}: not reachable")
    for n, node in zip(nodes, [*others, None], strict=False):
        if node is not None and node.get("statut") == "hors_ligne":
            problems.append(f"{n['nom']} is offline")
    channels = {n.get("canal") for n in nodes if n["joignable"]}
    if len(channels) > 1:
        problems.append("The nodes do not follow the same update channel: " + ", ".join(
            f"{n['nom']} {n.get('canal')}" for n in nodes))  # fmt: skip
    if len(nodes) < 2:
        problems.append("This node is not in a cluster with other nodes")
    return {"noeuds": nodes, "problemes": problems, "a_faire": any(n.get("a_faire") for n in nodes)}


# ---- The run ----


class _LockKeeper:
    """The cluster-wide lock of the run, renewed while it lasts: one rolling update at a time in the cluster."""

    def __init__(self):
        self._stop = threading.Event()
        self._thread = None

    def _client(self):
        from app.repositories.cfs import shadow

        return shadow._get_client()

    def __enter__(self):
        from app.core.cfs_client import Locked

        try:
            self._client().lock(LOCK, self_node.name(), LOCK_TTL_S)
        except Locked:
            raise RollingError("A rolling update is already running in this cluster") from None
        self._thread = threading.Thread(target=self._renew, daemon=True)
        self._thread.start()
        return self

    def _renew(self):
        while not self._stop.wait(LOCK_TTL_S / 3):
            try:
                self._client().lock(LOCK, self_node.name(), LOCK_TTL_S)
            except Exception:  # kept trying; the daemon frees it after its TTL anyway
                logger.warning("Could not renew the rolling update lock", exc_info=True)

    def __exit__(self, *exc):
        self._stop.set()
        try:
            self._client().unlock(LOCK, self_node.name())
        except Exception:
            logger.warning("Could not release the rolling update lock", exc_info=True)


def _wait(what, check, timeout, every=POLL_S):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(every)
    raise RollingError(f"{what}: not done after {timeout // 60} minutes")


def _drain(task_id, username, label, target):
    """Maintenance on, and the node's running VMs live-migrated to `target` (the Nodes page's drain)."""
    from app.core.tasks import create_task, task_status
    from app.routers import nodes as nodes_router

    maintenance.enter(label, username)
    with nodes_router._draining_lock:
        if label in nodes_router._draining:
            raise RollingError(f"{label} is already being drained")
        nodes_router._draining.add(label)
    plan = nodes_router._plan(label, target)
    if not plan["migrables"]:
        with nodes_router._draining_lock:
            nodes_router._draining.discard(label)
        return plan
    child = create_task("drain_node", label, node=label, username=username)
    task_log(task_id, f"Moving {len(plan['migrables'])} VM(s) from {label} to {target}")
    nodes_router._drain_job(child, username, label, target, plan["migrables"])  # releases the reservation
    if task_status(child) != "termine":
        raise RollingError(f"{label} could not be emptied: see task {child}")
    return plan


def _update_peer(task_id, username, node, target, stop_blocked):
    name = node["name"]
    task_log(task_id, f"== {name}")
    plan = _drain(task_id, username, name, target)

    status, body = peer_api(node, username, "POST", "/update/apply", {"systeme": True})
    if status != 202:
        raise RollingError(f"{name}: the update did not start ({_detail(body)})")
    remote_task = body["task_id"]
    task_log(task_id, f"{name}: updating (its task {remote_task})")

    def finished():
        try:
            s, t = peer_api(node, username, "GET", f"/tasks/{remote_task}")
        except PeerUnreachable:
            return None
        if s == 200 and t.get("statut") in ("termine", "echec"):
            return t
        return None

    result = _wait(f"{name}: the update", finished, UPDATE_TIMEOUT_S)
    if result["statut"] != "termine":
        raise RollingError(f"{name}: the update failed: {result.get('erreur')}")

    def answers():
        try:
            s, check = peer_api(node, username, "GET", "/update/check", timeout=120)
        except PeerUnreachable:
            return None
        return check if s == 200 else None

    check = _wait(f"{name}: the service after its update", answers, RESTART_TIMEOUT_S)
    if check.get("rolled_back"):
        raise RollingError(f"{name}: the new version did not start and was rolled back")
    task_log(task_id, f"{name}: Hyperlite {check.get('commit_local')}")

    if (check.get("systeme") or {}).get("redemarrage_requis"):
        if plan["non_migrables"] and not stop_blocked:
            task_log(task_id, f"{name}: a reboot is needed, left for later (VMs that cannot move run there)")
            log_action(username, "host_reboot_required", name, "succes", "left by the rolling update")
        else:
            _reboot_peer(task_id, username, node)
    maintenance.leave(name)
    task_log(task_id, f"{name}: done, out of maintenance")


def _reboot_peer(task_id, username, node):
    name = node["name"]
    before = _boot_id(node)
    hostname = _ssh(node, ["hostname"], timeout=30).stdout.strip()
    task_log(task_id, f"{name}: rebooting")
    status, body = peer_api(
        node, username, "POST", "/host/system/power",
        {"action": "reboot", "confirmation": hostname, "arreter_invites": True},
    )  # fmt: skip
    if status != 202:
        raise RollingError(f"{name}: the reboot was refused ({_detail(body)})")

    def back():
        try:
            boot = _boot_id(node)
        except Exception:  # down while it reboots
            return None
        if not boot or boot == before:
            return None
        try:
            s, _ = peer_api(node, username, "GET", "/update/channel")
        except PeerUnreachable:
            return None
        return s == 200

    _wait(f"{name}: the reboot", back, REBOOT_TIMEOUT_S, every=10)
    task_log(task_id, f"{name}: back after its reboot")


def run(task_id, username, stop_blocked, start_local_update, restarts_after_update):
    """The run, in the task `task_id`. start_local_update(username) starts this node's own update (POST /update/apply
    here) and returns its task id; restarts_after_update() says whether that update installed a new Hyperlite, whose
    service restart ends this job (the next start finishes the node: finish_self_after_restart)."""
    from app.core.tasks import task_status

    others = peers()
    done = []
    name = self_node.name()
    try:
        with _LockKeeper():
            if any(n.get("statut") == "hors_ligne" for n in others):
                raise RollingError("A node is offline: every node must be online for a rolling update")
            steps = len(others) + 1
            for i, node in enumerate(others):
                # The VMs go to a node already done when there is one, so they do not move twice.
                target = done[-1] if done else (others[i + 1]["name"] if i + 1 < len(others) else maintenance.LOCAL)
                _update_peer(task_id, username, node, target, stop_blocked)
                done.append(node["name"])
                update_task_progress(task_id, int((i + 1) * 100 / steps))

            task_log(task_id, f"== {name} (this node, last)")
            _drain(task_id, username, maintenance.LOCAL, done[-1] if done else None)
            _settings().set_app_setting(SELF_PENDING, json.dumps({"task": task_id, "user": username}))
            local_task = start_local_update(username)
            task_log(task_id, f"{name}: updating (task {local_task}); it leaves maintenance once it runs again")
    except Exception as e:
        logger.exception("Rolling update stopped")
        message = str(e) if isinstance(e, RollingError) else f"internal error: {e}"
        if done:
            message += f" (already updated: {', '.join(done)})"
        finish_task(task_id, "echec", message)
        log_action(username, "cluster_update", "cluster", "echec", message)
        return
    # Ended here, before this node's own update may restart the service under this job.
    finish_task(task_id, "termine")
    log_action(username, "cluster_update", "cluster", "succes", f"updated: {', '.join([*done, name])}")
    _wait_local(username, local_task, task_status, restarts_after_update)


def _wait_local(username, local_task, task_status, restarts_after_update):
    """This node's own update, followed while the service lives: when only Debian changed, nothing restarts and
    this thread takes the node out of maintenance itself."""
    deadline = time.monotonic() + UPDATE_TIMEOUT_S
    while time.monotonic() < deadline and task_status(local_task) not in ("termine", "echec"):
        time.sleep(POLL_S)
    status = task_status(local_task)
    if status == "echec":
        _settings().set_app_setting(SELF_PENDING, "")
        log_action(
            username, "cluster_update", self_node.name(), "echec", "this node's update failed: still in maintenance"
        )
    elif status == "termine" and not restarts_after_update():
        _settings().set_app_setting(SELF_PENDING, "")
        maintenance.leave(maintenance.LOCAL)
        log_action(username, "cluster_update", self_node.name(), "succes", "this node: out of maintenance")
    # Otherwise the service restarts now: finish_self_after_restart() ends it.


def _settings():
    from app.repositories.sqlite.settings import SqliteSettingsStore

    return SqliteSettingsStore()


def finish_self_after_restart():
    """At start-up: when this node updated itself as the last step of a rolling update, take it out of maintenance
    (the run's job ended with the restart). Called once the service runs, whatever version that is: a rolled back
    update is noted, and the node still leaves maintenance, since it runs."""
    try:
        raw = _settings().app_setting(SELF_PENDING)
    except Exception:  # no settings table yet
        return
    if not raw:
        return
    _settings().set_app_setting(SELF_PENDING, "")
    try:
        info = json.loads(raw)
    except ValueError:
        info = {}
    maintenance.leave(maintenance.LOCAL)
    log_action(
        info.get("user") or "system", "cluster_update", self_node.name(), "succes", "this node: out of maintenance"
    )
