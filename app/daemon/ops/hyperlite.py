"""Hyperlite's own update, done as root (docs/design/privilege-separation.md, section 13, host system part 2).

An update installs a package whose maintainer script restarts hyperlite-daemon itself, then the API: the job cannot
run in either. hyperlite.start_update checks the request again and starts the job in a transient unit of its own
(app/daemon/update_job.py), as the update watchdog runs, which outlives both restarts. The job is the code the API ran
in a thread up to 1.1.3 (app/routers/update.py); it writes the task's progress and log itself.

HYPERLITE_DAEMON_INPROCESS=1 (tests): the job runs in a thread of this process, as before."""

import os
import subprocess
import sys
import threading
from typing import Literal

from pydantic import Field

from app.core import paths
from app.daemon.registry import Args, DaemonError, operation

ENV_FILE = paths.ENV_FILE


class StartArgs(Args):
    kind: Literal["apt", "offline", "git", "rollback"]
    task_id: str = Field(max_length=64, pattern=r"^[A-Za-z0-9-]+$")
    username: str = Field(max_length=256)
    system: bool = True


def run_job(kind, task_id, username, system=True):
    """The job itself, in the unit (or the tests' thread)."""
    from app.routers import update

    if kind == "apt":
        update._run_update_job_apt(task_id, username, system)
    elif kind == "offline":
        update._run_update_job_apt(task_id, username, system, True)
    elif kind == "git":
        update._run_update_job(task_id, username, update._current_branch())
    elif kind == "rollback":
        update._run_rollback_job(task_id, username, update._previous())


def _launch(args):
    from app.core import privileged

    if os.environ.get(privileged.INPROCESS_ENV) == "1":
        threading.Thread(
            target=run_job, args=(args.kind, args.task_id, args.username, args.system), daemon=True
        ).start()
        return
    unit = f"hyperlite-update-job-{args.task_id[:12]}"
    argv = [
        "systemd-run",
        "--quiet",
        "--collect",
        f"--unit={unit}",
        f"--working-directory={paths.CODE}",
        f"--property=EnvironmentFile=-{ENV_FILE}",
        "--setenv=PYTHONUNBUFFERED=1",
        "--setenv=LC_ALL=C.UTF-8",
        sys.executable,
        "-m",
        "app.daemon.update_job",
        args.kind,
        args.task_id,
        args.username,
        "1" if args.system else "0",
    ]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise DaemonError("failed", f"The update job did not start: {(r.stderr or r.stdout).strip()[-300:]}")


@operation("hyperlite.start_update", StartArgs)
def start_update(args, ctx):
    """Start an update of Hyperlite (apt: from the mirror; offline: from the received file; git: a development
    checkout; rollback: the kept package of the previous version), after checking it can be done here."""
    from app.core import offline_update
    from app.routers import update

    method = update._install_method()
    if args.kind in ("apt", "offline", "rollback") and method != "apt":
        raise DaemonError("refused", "Only a node installed from the package can do that")
    if args.kind == "git" and method == "apt":
        raise DaemonError("refused", "This node is installed from the package: it updates from the mirror")
    if args.kind == "offline" and not offline_update.stored():
        raise DaemonError("refused", "No valid update file received on this node")
    if args.kind == "rollback" and not update._previous():
        raise DaemonError("refused", "No previous version kept on this node")
    _launch(args)
    return {"task_id": args.task_id}


class RefreshArgs(Args):
    attempts: int = Field(default=4, ge=1, le=10)
    delay_s: int = Field(default=8, ge=0, le=60)
    apt_timeout: int = Field(default=25, ge=5, le=180)


@operation("hyperlite.refresh_lists", RefreshArgs)
def refresh_lists(args, ctx):
    """apt-get update, retried (the check of a new version, app/routers/update.py)."""
    from app.routers import update

    r = update._apt_update_with_retry(attempts=args.attempts, delay_s=args.delay_s, timeout=args.apt_timeout)
    if r is None:
        return None
    return {"returncode": r.returncode, "stderr": (r.stderr or "")[-4000:]}


class ChannelArgs(Args):
    name: str = Field(max_length=16)


@operation("host.set_channel", ChannelArgs)
def set_channel(args, ctx):
    """The node's APT channel (/etc/apt/sources.list.d/hyperlite.list)."""
    from app.core import host_system

    try:
        return host_system.set_channel(args.name)
    except host_system.SettingError as e:
        raise DaemonError("refused", str(e)) from e
