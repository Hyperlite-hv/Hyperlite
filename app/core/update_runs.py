"""Update runs: each update of this node (from the mirror, from a file, back to the previous version), its steps and
its real outcome (docs/design/updates-2.md).

The job writes the steps as it goes (it runs as root in a unit of its own, out of the API). Once the package is
installed the run is `verifying`: the service restarts under the watchdog (scripts/update_watchdog.sh), which writes
its verdict to WATCHDOG_RESULT. Whatever version then starts closes the run (finalize_after_restart): succeeded when
the expected version runs, rolled back when the watchdog put the previous one back, failed when nothing was said
within VERIFY_TIMEOUT. The outcome never depends on a browser being open.

A percentage is only kept for a step that measures one (APT's download and dpkg's install, parse_status); every other
step has none, and the interface shows it as an activity of unknown length.
"""

import json
import logging
import re
from datetime import UTC, datetime, timedelta

from app.core import paths
from app.core.database import get_conn

logger = logging.getLogger(__name__)

STEPS = {
    "online": ["preflight", "backup", "index", "download", "install", "restart", "verify"],
    "file": ["preflight", "backup", "index", "install", "restart", "verify"],
    "rollback": ["backup", "restart", "verify"],
}
STATUSES = ("pending", "running", "done", "failed", "skipped")
VERIFY_TIMEOUT = timedelta(minutes=15)
WATCHDOG_RESULT = paths.UPDATE / "watchdog-result.json"
HISTORY_KEPT = 100


def _now():
    return datetime.now(UTC).isoformat()


def _row(r):
    if r is None:
        return None
    d = dict(r)
    d["steps"] = json.loads(d["steps"] or "[]")
    d["system"] = bool(d["system"])
    d["reboot_required"] = bool(d["reboot_required"])
    if d["finished_at"]:
        try:
            d["duration_s"] = round(
                (datetime.fromisoformat(d["finished_at"]) - datetime.fromisoformat(d["started_at"])).total_seconds()
            )
        except ValueError:
            d["duration_s"] = None
    else:
        d["duration_s"] = None
    return d


def begin(task_id, kind, from_version, to_version, system, username):
    """A new run, every step pending. Returns its id."""
    steps = [
        {"id": s, "status": "pending", "started_at": None, "finished_at": None, "detail": None, "progress": None}
        for s in STEPS[kind]
    ]
    with get_conn() as db:
        cur = db.execute(
            "INSERT INTO update_runs (task_id, kind, from_version, to_version, system, started_by, started_at, status, steps) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?)",
            (task_id, kind, from_version, to_version, 1 if system else 0, username, _now(), json.dumps(steps)),
        )
        db.execute(
            "DELETE FROM update_runs WHERE id NOT IN (SELECT id FROM update_runs ORDER BY id DESC LIMIT ?)",
            (HISTORY_KEPT,),
        )
        db.commit()
        return cur.lastrowid


def get(run_id):
    with get_conn() as db:
        return _row(db.execute("SELECT * FROM update_runs WHERE id = ?", (run_id,)).fetchone())


def by_task(task_id):
    with get_conn() as db:
        return _row(
            db.execute("SELECT * FROM update_runs WHERE task_id = ? ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
        )


def current():
    """The run still going (running or verifying), if any."""
    with get_conn() as db:
        return _row(
            db.execute(
                "SELECT * FROM update_runs WHERE status IN ('running', 'verifying') ORDER BY id DESC LIMIT 1"
            ).fetchone()
        )


def history(limit=30):
    with get_conn() as db:
        return [_row(r) for r in db.execute("SELECT * FROM update_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


def _save(run_id, **fields):
    sets = ", ".join(f"{k} = ?" for k in fields)
    values = [json.dumps(v) if k == "steps" else v for k, v in fields.items()]
    with get_conn() as db:
        db.execute(f"UPDATE update_runs SET {sets} WHERE id = ?", (*values, run_id))  # noqa: S608 -- fixed column names
        db.commit()


def step(run_id, step_id, status, detail=None, progress=None):
    """A step moved to `status`; starting a step closes the one still running before it. `progress` is a measured
    percentage only (or None)."""
    run = get(run_id)
    if not run:
        return
    now = _now()
    steps = run["steps"]
    for s in steps:
        if s["id"] == step_id:
            if status == "running" and s["status"] != "running":
                s["started_at"] = s["started_at"] or now
            if status in ("done", "failed", "skipped"):
                s["finished_at"] = now
                if status == "done" and s["progress"] is not None:
                    s["progress"] = 100
            s["status"] = status
            if detail is not None:
                s["detail"] = detail[:500]
            if progress is not None:
                s["progress"] = max(0, min(100, round(progress, 1)))
        elif status == "running" and s["status"] == "running":
            s["status"] = "done"
            s["finished_at"] = now
    _save(run_id, steps=steps)


def skip_pending(run_id, *step_ids):
    run = get(run_id)
    if not run:
        return
    for s in run["steps"]:
        if s["id"] in step_ids and s["status"] == "pending":
            s["status"] = "skipped"
    _save(run_id, steps=run["steps"])


def set_target(run_id, to_version):
    _save(run_id, to_version=to_version)


def fail(run_id, error):
    """The run failed before the service restarted: the step running fails with it, the others stay pending."""
    run = get(run_id)
    if not run:
        return
    now = _now()
    for s in run["steps"]:
        if s["status"] == "running":
            s["status"] = "failed"
            s["finished_at"] = now
            s["detail"] = (s["detail"] or error)[:500]
    _save(run_id, steps=run["steps"], status="failed", finished_at=now, error=str(error)[:2000])
    _announce(get(run_id))


def succeed_without_restart(run_id, reboot_required=False):
    """Only Debian packages changed: nothing restarts, the run is over."""
    run = get(run_id)
    if not run:
        return
    now = _now()
    for s in run["steps"]:
        if s["status"] == "running":
            s["status"] = "done"
            s["finished_at"] = now
        elif s["status"] == "pending":
            s["status"] = "skipped"
    _save(run_id, steps=run["steps"], status="succeeded", finished_at=now, reboot_required=1 if reboot_required else 0)
    _announce(get(run_id))


def verifying(run_id, reboot_required=False):
    """The package is installed: the service restarts under the watchdog, the next start says how it went."""
    step(run_id, "restart", "running")
    _save(run_id, status="verifying", reboot_required=1 if reboot_required else 0)
    try:
        WATCHDOG_RESULT.unlink(missing_ok=True)  # a verdict of an earlier update must not close this one
    except OSError:
        logger.warning("The previous watchdog verdict could not be removed", exc_info=True)


def read_watchdog_result():
    try:
        return json.loads(WATCHDOG_RESULT.read_text())
    except (OSError, ValueError):
        return None


def finalize_after_restart(running_version, now=None):
    """Closes the run left `verifying` by the update this start follows. Returns the run closed, or None."""
    run = current()
    if not run or run["status"] != "verifying":
        return None
    now = now or datetime.now(UTC)
    verdict = read_watchdog_result()
    steps = run["steps"]
    stamp = now.isoformat()

    def close(step_id, status, detail=None):
        for s in steps:
            if s["id"] == step_id:
                s["status"] = status
                s["finished_at"] = stamp
                if detail:
                    s["detail"] = detail[:500]

    expected = run["to_version"]
    if verdict and verdict.get("outcome") == "rolled_back":
        close("restart", "done")
        close(
            "verify",
            "failed",
            f"{expected} did not answer: {verdict.get('version') or 'the previous version'} put back",
        )
        status, error = (
            "rolled_back",
            verdict.get("detail") or f"{expected} did not start; the previous version runs again",
        )
    elif verdict and verdict.get("outcome") == "failed":
        close("restart", "done")
        close("verify", "failed", verdict.get("detail"))
        status, error = "failed", verdict.get("detail") or "The watchdog could not put the previous version back"
    elif expected and running_version == expected:
        close("restart", "done")
        close("verify", "done", f"{expected} answers")
        status, error = "succeeded", None
    elif now - datetime.fromisoformat(run["started_at"]) > VERIFY_TIMEOUT:
        close("restart", "done")
        close("verify", "failed", f"{running_version} runs instead of {expected}")
        status, error = "failed", f"{running_version} runs instead of {expected}"
    else:
        return None  # the old process still answers while the new one starts: the next start decides
    _save(run["id"], steps=steps, status=status, finished_at=stamp, error=error)
    closed = get(run["id"])
    _announce(closed)
    return closed


def _announce(run):
    """The outcome in the notification centre (app/core/notification_feed.py)."""
    if not run:
        return
    try:
        from app.core import notification_feed

        notification_feed.update_outcome(run)
    except Exception:
        logger.warning("The outcome of update run %s was not announced", run.get("id"), exc_info=True)


# APT's status channel (-o APT::Status-Fd): "dlstatus:<package>:<percent>:<text>" while downloading,
# "pmstatus:<package>:<percent>:<text>" while dpkg installs.
_STATUS_RE = re.compile(r"^(dl|pm)status:[^:]*:([0-9]+(?:\.[0-9]+)?):(.*)$")


def parse_status(line):
    """(kind "download" | "install", percent, text) for a status line of APT, None for any other line."""
    m = _STATUS_RE.match(line.strip())
    if not m:
        return None
    return ("download" if m.group(1) == "dl" else "install", float(m.group(2)), m.group(3).strip())
