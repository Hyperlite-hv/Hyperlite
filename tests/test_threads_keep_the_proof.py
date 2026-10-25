"""The work a request starts on a thread keeps that request's proof for hyperlite-daemon (docs/design/privilege-
separation.md, section 14, a prerequisite of 4e): a task ticket bound to its task, for any account, valid while the task
runs (app/core/peer_auth.py, bound_ticket), and tasks.start_thread, the only way the API starts such a thread (the scan
at the end)."""

import ast
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest

import app.daemon.ops  # noqa: F401  (registers the operations)
from app.core import peer_auth, security, tasks
from app.daemon.registry import Context, dispatch, proven

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_credential_left():
    token = peer_auth.CREDENTIAL.set(None)
    yield
    peer_auth.CREDENTIAL.reset(token)


def _session(username):
    return security.create_access_token({"sub": username})


def _end(task_id, minutes_ago):
    tasks.finish_task(task_id, "succeeded")
    when = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
    from app.core.database import get_conn

    with get_conn() as db:
        db.execute("UPDATE tasks SET finished_at = ? WHERE id = ?", (when, task_id))
        db.commit()


# ---- The ticket ----


def test_any_account_gets_a_ticket_bound_to_its_own_running_task(make_user, database):
    make_user("vic", "viewer")
    make_user("ana", "admin")
    mine = tasks.create_task("backup_vm", "web", username="vic")
    ticket = peer_auth.bound_ticket(_session("vic"), mine)
    claims = jwt.decode(ticket, options={"verify_signature": False})
    assert claims["typ"] == "task" and claims["task"] == mine and claims["sub"] == "vic"
    assert claims["exp"] - claims["iat"] == peer_auth.BOUND_LIFETIME_S
    assert proven(ticket) == "vic"
    with pytest.raises(PermissionError, match="No such task"):
        peer_auth.bound_ticket(_session("ana"), mine)  # another account's task
    with pytest.raises(PermissionError, match="No such task"):
        peer_auth.bound_ticket(_session("vic"), "00000000-0000-4000-8000-000000000000")


def test_it_proves_nothing_once_its_task_ended_but_its_last_calls(make_user, database):
    make_user("vic", "viewer")
    task_id = tasks.create_task("backup_vm", "web", username="vic")
    ticket = peer_auth.bound_ticket(_session("vic"), task_id)
    _end(task_id, minutes_ago=1)
    assert proven(ticket) == "vic"  # its cleanup, just after
    with pytest.raises(PermissionError, match="has ended"):
        peer_auth.bound_ticket(_session("vic"), task_id)  # never made for a task that ended
    _end(task_id, minutes_ago=peer_auth.END_MARGIN_S // 60 + 1)
    assert proven(ticket) is None


def test_a_valid_task_ticket_makes_one_for_another_task_of_the_same_account(make_user, database):
    make_user("vic", "viewer")
    first = tasks.create_task("backup_vm", "web", username="vic")
    second = tasks.create_task("backup_vm", "db", username="vic")
    ticket = peer_auth.bound_ticket(_session("vic"), first)
    child = peer_auth.bound_ticket(ticket, second)
    assert jwt.decode(child, options={"verify_signature": False})["task"] == second
    _end(first, minutes_ago=60)
    with pytest.raises(PermissionError, match="has ended"):
        peer_auth.bound_ticket(ticket, second)  # from a ticket that proves nothing any more


def test_a_call_from_another_member_makes_no_task_ticket(make_user, database):
    make_user("vic", "viewer")
    task_id = tasks.create_task("backup_vm", "web", username="vic")
    now = int(time.time())
    peer = jwt.encode({"typ": "peer", "sub": "vic", "iat": now, "exp": now + 60}, "x" * 32, algorithm="HS256")
    with pytest.raises(PermissionError):
        peer_auth.bound_ticket(peer, task_id)


def test_the_daemon_signs_it_for_the_proven_account_only(make_user, database):
    make_user("vic", "viewer")
    task_id = tasks.create_task("backup_vm", "web", username="vic")

    def ask(credential, task):
        return dispatch(
            {"op": "auth.task", "args": {"credential": credential, "task_id": task}},
            Context("vic", None, 1, 0, proof=None),
        )

    answer = ask(_session("vic"), task_id)
    assert answer["ok"] and jwt.decode(answer["result"], options={"verify_signature": False})["task"] == task_id
    refused = ask("not a credential", task_id)
    assert not refused["ok"] and refused["error"] == "forbidden"
    assert ask(_session("vic"), "../../etc")["error"] == "invalid_arguments"


# ---- The threads ----


def _seen_on_a_thread(create=False, **kwargs):
    seen = {}

    def work(*args):
        seen["credential"] = peer_auth.CREDENTIAL.get()
        seen["args"] = args
        if create:
            seen["task"] = tasks.create_task("backup_vm", "web", username="vic")
            seen["after"] = peer_auth.CREDENTIAL.get()

    worker = tasks.start_thread(work, 1, 2, **kwargs)
    worker.join(10)
    return seen


def test_a_task_thread_shows_a_ticket_bound_to_its_task(make_user, database):
    make_user("vic", "viewer")
    task_id = tasks.create_task("backup_vm", "web", username="vic")
    peer_auth.remember(_session("vic"))
    seen = _seen_on_a_thread(task_id=task_id)
    assert seen["args"] == (1, 2)
    assert jwt.decode(seen["credential"], options={"verify_signature": False})["task"] == task_id


def test_a_thread_that_makes_its_own_task_binds_its_proof_to_it(make_user, database):
    make_user("vic", "viewer")
    session = _session("vic")
    peer_auth.remember(session)
    seen = _seen_on_a_thread(create=True)
    assert seen["credential"] == session  # until its task exists
    assert jwt.decode(seen["after"], options={"verify_signature": False})["task"] == seen["task"]
    assert peer_auth.CREDENTIAL.get() == session  # the request's own is untouched


def test_without_a_ticket_the_thread_keeps_the_proof_it_had(make_user, database, caplog):
    make_user("vic", "viewer")
    task_id = tasks.create_task("backup_vm", "web", username="someone else")
    session = _session("vic")
    peer_auth.remember(session)
    assert _seen_on_a_thread(task_id=task_id)["credential"] == session
    assert "no task ticket" in caplog.text


def test_outside_a_request_the_thread_starts_as_it_is(database):
    assert _seen_on_a_thread(task_id="any")["credential"] is None


def test_a_long_endpoints_task_and_a_cancellation_keep_the_proof(make_user, database):
    make_user("vic", "viewer")
    task_id = tasks.create_task("backup_vm", "web", username="vic")
    peer_auth.remember(_session("vic"))
    seen, done = {}, threading.Event()

    def work():
        seen["work"] = peer_auth.CREDENTIAL.get()
        tasks.register_cancel(task_id, lambda: seen.update(cancel=peer_auth.CREDENTIAL.get()))
        done.set()

    tasks.answer_later(task_id, work)
    assert done.wait(10)
    peer_auth.CREDENTIAL.set(None)  # the watch of cross-process cancellations runs with none
    tasks._cancellers[task_id]()
    assert seen["cancel"] == seen["work"]
    assert jwt.decode(seen["work"], options={"verify_signature": False})["task"] == task_id
    tasks.unregister_cancel(task_id)


# ---- No other way to start a thread that calls hyperlite-daemon ----

# Where the API (outside hyperlite-daemon, app/daemon/) still starts a thread itself, and why that keeps no proof it
# needs. A loop with nobody behind it moves out of the API with 4e (app/scheduler, app/ha_services, the daemon's own
# start); until then it runs as root. Keyed by file and function, as the scan finds them.
ALLOWED = {
    "app/core/tasks.py": {"start_thread": "the helper itself", "_watch": "acts through cancellers registered carried"},
    # Loops and timers with nobody behind them (4e: the scheduler, HA's services or the daemon).
    "app/core/access_log.py": {"start": "writes this process's request log"},
    "app/core/audit.py": {"_ensure_writer_started": "writes the audit log", "log_action": "sends a notification"},
    "app/core/backups.py": {"start_backup_scheduler": "hyperlite-scheduler's"},
    "app/core/backup_store_sync.py": {"run_due": "hyperlite-scheduler's"},
    "app/core/backup_stores.py": {"collect_due": "hyperlite-scheduler's", "_cluster_lock": "renews a cfs lock"},
    "app/core/cluster.py": {"start_node_poller": "hyperlite-scheduler's", "_check_node_boot": "hyperlite-scheduler's"},
    "app/core/cluster_lead.py": {"start_lead_loop": "renews the lead"},
    "app/core/cluster_v1.py": {"start": "rewrites the replicated configuration once"},
    "app/core/config_copy.py": {"start_config_copy": "hyperlite-scheduler's"},
    "app/core/db_health.py": {"start": "SQLite's own check"},
    "app/core/drs.py": {
        "start_carry_out": "hyperlite-ha-lrm",
        "start_evaluate": "hyperlite-ha-crm",
    },
    "app/core/ha_monitor.py": {"start": "HA's services"},
    "app/core/ha_recovery.py": {"start": "HA's services"},
    "app/core/ha_selffence.py": {"start_selffence": "HA's services"},
    "app/core/ha_watch.py": {"start_ha_watch": "HA's services"},
    "app/core/initial_password.py": {"start": "hyperlite-daemon's start (seed.first_start)"},
    "app/core/metrics.py": {"start_metrics_collector": "4e: to the scheduler"},
    "app/core/peer_auth.py": {"start_publishing": "hyperlite-daemon's"},
    "app/core/peer_https.py": {"start": "publishes this node's certificate"},
    "app/core/replication.py": {"start_replication_scheduler": "hyperlite-scheduler's"},
    "app/core/statd.py": {"start": "this node's status; HA's services send it too"},
    "app/core/update_check.py": {"start_update_check_scheduler": "hyperlite-scheduler's"},
    "app/core/vm_boot.py": {"start_boot_sequence": "hyperlite-scheduler's"},
    "app/core/vm_cleanup.py": {"start_auto_cleanup_scheduler": "hyperlite-scheduler's"},
    "app/core/vm_crash_watch.py": {"start_vm_crash_watch": "reads libvirt's events, read-only"},
    "app/main.py": {"on_startup": "protects the cloud-init drives, at start"},
    "app/repositories/cfs/inbound.py": {"start_apply_loop": "4e: as root (section 14.1)"},
    "app/repositories/cfs/shadow.py": {"start_shadow_copy": "sends the outbox to hyperlite-cfs"},
    # Threads that already carry their proof (peer_auth.carry), or need none.
    "app/routers/cluster_update.py": {"start_rolling_update": "carries a task ticket of its own"},
    "app/core/rolling_update.py": {"cluster_plan": "carried", "_LockKeeper.__enter__": "renews a cfs lock"},
    "app/core/iso_share.py": {"cluster_isos": "carried", "_copy": "hyperlite-daemon's (iso.copy)"},
    "app/core/guest_agent.py": {"ipv4_many": "queries the agent on a connection it is given"},
    "app/core/site_tunnel.py": {"SourceTunnel.start": "relays bytes", "SourceTunnel._accept": "relays bytes"},
    # Modules run inside hyperlite-daemon's operations.
    "app/core/backup_store_vm.py": {
        "read_into_store": "hyperlite-daemon's",
        "read_changed_into_store": "hyperlite-daemon's",
        "restore_disk": "hyperlite-daemon's",
    },
    "app/core/bridge_vlan.py": {"start_trial": "hyperlite-daemon's (network.vlan_trial)"},
    "app/core/console_sessions.py": {"_keep": "hyperlite-daemon's"},
    "app/core/disk_move.py": {"_convert_cold": "hyperlite-daemon's"},
    "app/core/file_restore.py": {"open_here": "hyperlite-daemon's"},
    "app/core/site_live.py": {"run": "hyperlite-daemon's"},
    "app/core/site_transfer.py": {"export": "hyperlite-daemon's", "import_disk": "hyperlite-daemon's"},
    "app/core/vm_migrate.py": {"migrate": "hyperlite-daemon's"},
}


class _Threads(ast.NodeVisitor):
    """The functions of one file that start a thread themselves (threading.Thread, a Timer, an executor's submit)."""

    def __init__(self, rel, found):
        self.rel, self.found, self.stack = rel, found, []

    def visit_FunctionDef(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_ClassDef = visit_FunctionDef

    def visit_Call(self, node):
        name = ast.unparse(node.func)
        if name in ("threading.Thread", "threading.Timer") or name.endswith(".submit"):
            self.found.add((self.rel, ".".join(self.stack) or "<module>"))
        self.generic_visit(node)


def _raw_threads():
    found = set()
    for path in sorted((ROOT / "app").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if not rel.startswith("app/daemon/"):
            _Threads(rel, found).visit(ast.parse(path.read_text(), rel))
    return found


def test_the_api_starts_its_threads_through_start_thread():
    found = _raw_threads()
    allowed = {(f, fn) for f, fns in ALLOWED.items() for fn in fns}
    assert sorted(found - allowed) == [], "start it with tasks.start_thread, or say here why it needs no proof"
    assert sorted(allowed - found) == [], "an entry of ALLOWED found no thread any more: remove it"
