"""Hyperlite update from its own Git repository: the equivalent of the vSphere
Lifecycle Manager, with Git as the source of truth instead of a proprietary
patch repository.

It only touches the Hyperlite management layer (API code + interface). VMs that
are already running, driven directly by libvirt/QEMU independently of the
Hyperlite process, are neither stopped nor restarted by an update (the same
principle as a vCenter reboot, which does not affect VMs already running under
ESXi).

A real constraint of this repository's workflow: development commits directly
to `master` from live working sessions, so the working tree is very often
"dirty" (uncommitted changes) when someone wants to trigger an update. Rather
than an optimistic `git pull` that could hit a merge conflict in the middle of
an update, the UI flatly refuses to start when the tree is not clean, with an
actionable message. This is the intended "clean handling", not an oversight.

"""

import contextlib
import logging
import os
import shutil
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.core.audit import log_action
from app.core.database import get_conn
from app.core.security import require_role
from app.core.tasks import create_task, finish_task, update_task_progress

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/update", tags=["update"])

REPO_DIR = Path(__file__).resolve().parent.parent.parent  # /root/hyperlite
BACKUP_DIR = Path("/root/hyperlite-backups")
WATCHDOG_SCRIPT = REPO_DIR / "scripts" / "update_watchdog.sh"


def _spawn_outside_service(unit_prefix, argv):
    """Run a command that must survive the restart of hyperlite.service.

    start_new_session is not enough: the process stays in the service's cgroup, and
    `systemctl restart` kills the whole cgroup, watchdog included (the rollback safety
    net never ran). A transient systemd unit lives outside that cgroup. Falls back to a
    detached process where systemd-run is unavailable."""
    if shutil.which("systemd-run"):
        unit = f"{unit_prefix}-{int(time.time())}"
        try:
            subprocess.run(
                ["systemd-run", "--quiet", "--collect", f"--unit={unit}", *argv],
                check=True,
                timeout=15,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except (subprocess.SubprocessError, OSError):
            logging.getLogger(__name__).warning("systemd-run failed, using a detached process")
    subprocess.Popen(argv, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# Exclusions from the backup tarball: DERIVED code/state, plus the bulky user data an update
# never touches (uploaded ISOs, VM backups, exports, templates, imported disks). hyperlite.db,
# .env and the rest of data/ stay included, which is what a rollback must be able to restore.
# Excluded files are simply left in place by a rollback (tar extraction never deletes), so
# leaving them out cannot lose them; including them made each backup ~12 GB.
_BACKUP_EXCLUDES = [
    "--exclude=venv",
    "--exclude=dashboard/node_modules",
    "--exclude=dashboard/dist",
    "--exclude=.git",
    "--exclude=data/isos",
    "--exclude=data/backups",
    "--exclude=data/vm-exports",
    "--exclude=data/templates",
    "--exclude=data/imported-disks",
]
UPDATE_BACKUPS_KEPT = 3


def _prune_old_backups(directory, keep=UPDATE_BACKUPS_KEPT):
    """Keep only the newest `keep` update backups; other files in the directory are never touched."""
    backups = sorted(Path(directory).glob("hyperlite-backup-*.tar.gz"), key=lambda p: p.name, reverse=True)
    for old in backups[keep:]:
        with contextlib.suppress(OSError):
            old.unlink()


def _run(cmd, cwd=None, timeout=180):
    return subprocess.run(cmd, cwd=str(cwd or REPO_DIR), capture_output=True, text=True, timeout=timeout)


# Force the C locale for every apt/dpkg command whose OUTPUT is parsed by this
# file. `apt-cache policy` translates its fields ("Candidat :" instead of
# "Candidate:") as soon as LANG/LC_ALL is not English, and the parsing in
# _check_update_apt() then failed SILENTLY (no error, just `commit_distant: null`
# and a wrong `a_jour: false`). dpkg-query -f='...' is not concerned (already
# machine-readable whatever the locale) but is forced here too for consistency.
def _run_c(cmd, timeout=180):
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    return subprocess.run(cmd, cwd=str(REPO_DIR), capture_output=True, text=True, timeout=timeout, env=env)


def _current_commit():
    r = _run(["git", "rev-parse", "HEAD"])
    return r.stdout.strip() if r.returncode == 0 else None


def _current_branch():
    r = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    return r.stdout.strip() if r.returncode == 0 else "master"


def _is_dirty():
    r = _run(["git", "status", "--porcelain"])
    return bool(r.stdout.strip())


# --- Update through an APT repository: an alternative to the git mechanism
# above, for any machine that explicitly adopted the `hyperlite` package (see
# installer/build-deb.sh and installer/build-apt-repo.sh). A machine running from a
# Git clone (the development setup) always gets "git" from _install_method()
# (presence of .git), and ALL the behaviour below stays unchanged there: this path
# only activates on a machine where `apt install hyperlite` was really run at
# least once (dpkg knows it as "installed").
def _install_method():
    if (REPO_DIR / ".git").exists():
        return "git"
    r = _run_c(["dpkg-query", "-W", "-f=${Status}", "hyperlite"])
    if r.returncode == 0 and "install ok installed" in r.stdout:
        return "apt"
    return "git"  # indeterminate state: fall back to the historical behaviour


def _dpkg_installed_version():
    r = _run_c(["dpkg-query", "-W", "-f=${Version}", "hyperlite"])
    return r.stdout.strip() if r.returncode == 0 else None


# `apt-get update` used to fail PERSISTENTLY with an inconsistent size error
# (confirmed over 30 attempts spread over 10+ minutes, still inconsistent more
# than 1 h 20 after the last publication, checked by querying the origin
# directly). It was not a transient propagation window but a structural lack of
# strong consistency between linked files on GitHub Pages' multi-node CDN. It was
# FIXED AT THE ROOT rather than worked around here: the repository is now served
# directly by nginx on the publishing host, with no intermediate CDN (see
# installer/postinstall.sh). This function keeps a MODEST retry as defense in
# depth against a plain network glitch (a brief Tailscale outage...), no longer to
# work around a structural inconsistency that does not exist anymore at that time
# scale.
def _apt_update_with_retry(attempts, delay_s, timeout=60):
    last = None
    for i in range(attempts):
        try:
            last = _run_c(["apt-get", "update"], timeout=timeout)
        except subprocess.TimeoutExpired:
            last = None
            continue
        if last.returncode == 0:
            return last
        if i < attempts - 1:
            time.sleep(delay_s)
    return last


def _check_update_apt():
    installed = _dpkg_installed_version()
    # For /update/check (an interactive call, the user is waiting in front of the
    # UI): few attempts and a short delay. A real network outage must come up quickly
    # rather than make the user wait needlessly after a simple click.
    upd = _apt_update_with_retry(attempts=4, delay_s=8, timeout=25)
    if upd is None or upd.returncode != 0:
        detail = upd.stderr.strip()[:400] if upd is not None else "timed out"
        return {
            "verifiable": False,
            "erreur": f"Unable to contact the APT repository: {detail}",
            "commit_local": installed,
        }

    policy = _run_c(["apt-cache", "policy", "hyperlite"])
    candidate = None
    for line in policy.stdout.splitlines():
        line = line.strip()
        if line.startswith("Candidate:"):
            candidate = line.split(":", 1)[1].strip()
    a_jour = bool(candidate) and installed == candidate
    changelog = [f"New version available: {candidate}"] if not a_jour and candidate else []

    return {
        "verifiable": True,
        "branche": "apt",
        "commit_local": installed,
        "commit_distant": candidate,
        "a_jour": a_jour,
        "arbre_propre": True,  # not applicable in apt mode (no Git working tree)
        "changelog": changelog,
    }


@router.get("/check")
def check_update(user: dict = Depends(require_role("admin"))):
    if _install_method() == "apt":
        return _check_update_apt()

    branch = _current_branch()
    local = _current_commit()
    try:
        fetch = _run(["git", "fetch", "origin", branch], timeout=30)
    except subprocess.TimeoutExpired:
        return {
            "verifiable": False,
            "erreur": "Timed out contacting the remote repository (network?)",
            "commit_local": local,
        }

    if fetch.returncode != 0:
        return {
            "verifiable": False,
            "erreur": f"Unable to contact the remote repository: {fetch.stderr.strip()[:400]}",
            "commit_local": local,
        }

    remote_r = _run(["git", "rev-parse", f"origin/{branch}"])
    remote = remote_r.stdout.strip() if remote_r.returncode == 0 else None
    a_jour = bool(remote) and local == remote
    changelog = []
    if not a_jour and remote and local:
        log_r = _run(["git", "log", "--oneline", f"{local}..{remote}"])
        if log_r.returncode == 0:
            changelog = [line for line in log_r.stdout.strip().splitlines() if line]

    return {
        "verifiable": True,
        "branche": branch,
        "commit_local": local,
        "commit_distant": remote,
        "a_jour": a_jour,
        "arbre_propre": not _is_dirty(),
        "changelog": changelog,
    }


def _backup(task_id):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    tarball = BACKUP_DIR / f"hyperlite-backup-{stamp}.tar.gz"
    cmd = ["tar", "czf", str(tarball), *_BACKUP_EXCLUDES, "-C", str(REPO_DIR.parent), REPO_DIR.name]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    # WARNING (a real bug found on the very first /update/apply ever run under real
    # conditions): tar returns exit code 1, neither 0 nor a real error, as soon as a
    # file changes WHILE it is being read ("file changed as we read it"). Hyperlite
    # runs continuously and writes constantly to hyperlite.db (WAL mode, metrics
    # collection, etc.) exactly while this tar archives it. That is not a rare case,
    # it is SYSTEMATIC on an active instance. According to tar itself (man tar, EXIT
    # STATUS section): 0 = success, 1 = "some files differ" (a non-fatal warning, the
    # archive is still usable), 2 = a real fatal error. Treating 1 as a failure blocked
    # EVERY update as soon as a background thread touched a file at the wrong moment.
    # Fixed by considering only code 2+ as a real error.
    if r.returncode >= 2:
        raise RuntimeError(f"Backup failed: {r.stderr.strip()[:400]}")
    _prune_old_backups(BACKUP_DIR)
    return tarball


def _run_update_job(task_id, username, branch):
    log_file = BACKUP_DIR / "update.log"

    def step(pct, message):
        update_task_progress(task_id, pct)
        log_action(username, "hyperlite_update_step", message, "succes")

    old_commit = _current_commit()
    try:
        if _is_dirty():
            raise RuntimeError(
                "Working tree is not clean (uncommitted changes): update refused so as not to"
                "risk a merge conflict midway. Commit or discard the local changes first."
            )

        step(5, "Backing up the current state (code + config + database)")
        tarball = _backup(task_id)

        step(20, "Fetching the latest version (git fetch)")
        fetch = _run(["git", "fetch", "origin", branch])
        if fetch.returncode != 0:
            raise RuntimeError(f"git fetch failed: {fetch.stderr.strip()[:400]}")

        step(30, "Applying the new version (git reset --hard)")
        reset = _run(["git", "reset", "--hard", f"origin/{branch}"])
        if reset.returncode != 0:
            raise RuntimeError(f"git reset failed: {reset.stderr.strip()[:400]}")
        new_commit = _current_commit()

        changed = _run(["git", "diff", "--name-only", old_commit, new_commit]).stdout

        if "requirements.txt" in changed:
            step(45, "Installing Python dependencies (pip install)")
            pip = _run([str(REPO_DIR / "venv" / "bin" / "pip"), "install", "-r", "requirements.txt"], timeout=300)
            if pip.returncode != 0:
                raise RuntimeError(f"pip install failed: {pip.stderr.strip()[:400]}")

        if "dashboard/package.json" in changed or "dashboard/package-lock.json" in changed:
            step(55, "Installing frontend dependencies (npm install)")
            npm_i = _run(["npm", "install"], cwd=REPO_DIR / "dashboard", timeout=300)
            if npm_i.returncode != 0:
                raise RuntimeError(f"npm install failed: {npm_i.stderr.strip()[:400]}")

        # Rebuild the frontend on every update (not only when package.json changed): the
        # source code itself changed in almost every update, and a systematic rebuild is
        # the only reliable way never to serve an outdated interface.
        step(70, "Reconstruction de l'interface (npm run build)")
        npm_b = _run(["npm", "run", "build"], cwd=REPO_DIR / "dashboard", timeout=300)
        if npm_b.returncode != 0:
            raise RuntimeError(f"npm run build failed: {npm_b.stderr.strip()[:400]}")

        # There is no database migration framework (Alembic or equivalent) in this
        # project: the schema is created with idempotent CREATE TABLE IF NOT EXISTS (see
        # app/core/database.py::init_db), replayed automatically at the next start.
        # Nothing more to do here.
        step(85, "Checking the database schema (no migration required)")

        step(90, "Restarting the service and post-update verification")
        log_action(username, "hyperlite_update_step", f"{old_commit[:8]} -> {new_commit[:8]}", "succes")

        # The watchdog is started BEFORE the restart, as a fully detached process
        # (start_new_session): it must survive the death of THIS Python process when
        # systemctl stops it. It, not this thread, verifies that the NEW code starts
        # correctly and rolls back automatically if not (see scripts/update_watchdog.sh;
        # this process cannot verify its own replacement).
        _spawn_outside_service(
            "hyperlite-update-watchdog",
            ["bash", str(WATCHDOG_SCRIPT), str(tarball), str(REPO_DIR), str(log_file)],
        )
        _spawn_outside_service("hyperlite-update-restart", ["bash", "-c", "sleep 2 && systemctl restart hyperlite"])

        # This process will die in ~2 s (the restart above): the task is marked
        # "termine" optimistically before dying rather than leaving a task forever
        # "en_cours". The watchdog is the real safety net if the new code does not start.
        finish_task(task_id, "termine")
        log_action(username, "hyperlite_update", f"{old_commit} -> {new_commit}", "succes")

    except Exception as exc:
        # Any failure before the restart: the running process still runs the OLD code (no
        # restart has been triggered yet), so nothing breaks for users. The tree on disk
        # is still put back to the old commit so that a later manual restart does not pick
        # up half-updated code.
        if old_commit:
            try:
                _run(["git", "reset", "--hard", old_commit])
            except Exception:
                logger.exception("Rollback to the previous commit failed")
        finish_task(task_id, "echec", str(exc))
        log_action(username, "hyperlite_update", str(exc), "echec")


def _run_update_job_apt(task_id, username):
    log_file = BACKUP_DIR / "update.log"

    def step(pct, message):
        update_task_progress(task_id, pct)
        log_action(username, "hyperlite_update_step", message, "succes")

    old_version = _dpkg_installed_version()
    try:
        step(5, "Backing up the current state (code + config + database)")
        tarball = _backup(task_id)

        step(20, "Updating the APT index")
        # See _apt_update_with_retry above: the repository is served directly by nginx
        # (no CDN), so a modest retry is now enough. Slightly more patient than
        # /update/check since this is a background task (the user already sees a progress
        # bar).
        upd = _apt_update_with_retry(attempts=6, delay_s=10, timeout=30)
        if upd is None or upd.returncode != 0:
            detail = upd.stderr.strip()[:400] if upd is not None else "timed out"
            raise RuntimeError(f"apt-get update failed: {detail}")

        step(40, "Installing the new version (apt-get install)")
        # HYPERLITE_SKIP_RESTART: the package's postinst NORMALLY restarts the service by
        # itself (standard Debian behaviour, expected by an admin running `apt install`
        # by hand over SSH). But HERE, apt-get is a subprocess of the RUNNING hyperlite
        # service (this very HTTP request), and letting it restart itself from inside
        # that subprocess would kill apt-get in the middle of its own postinst. This flag
        # tells the postinst NOT to restart, and this thread does it right afterwards, in
        # the same detached way as the Git path (Popen + sleep 2).
        env = {**os.environ, "HYPERLITE_SKIP_RESTART": "1", "LC_ALL": "C", "LANG": "C"}
        install = subprocess.run(
            ["apt-get", "install", "--only-upgrade", "-y", "hyperlite"],
            cwd=str(REPO_DIR),
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        if install.returncode != 0:
            raise RuntimeError(f"apt-get install failed: {install.stderr.strip()[:400]}")
        new_version = _dpkg_installed_version()

        step(85, "Checking the database schema (no migration required)")
        step(90, "Restarting the service and post-update verification")
        log_action(username, "hyperlite_update_step", f"{old_version} -> {new_version}", "succes")

        # The same safety net as the Git path: a detached watchdog BEFORE the restart
        # (it survives the death of this process), which verifies that the new code starts
        # correctly and restores the tarball otherwise.
        _spawn_outside_service(
            "hyperlite-update-watchdog",
            ["bash", str(WATCHDOG_SCRIPT), str(tarball), str(REPO_DIR), str(log_file)],
        )
        _spawn_outside_service("hyperlite-update-restart", ["bash", "-c", "sleep 2 && systemctl restart hyperlite"])

        finish_task(task_id, "termine")
        log_action(username, "hyperlite_update", f"{old_version} -> {new_version}", "succes")

    except Exception as exc:
        # No "dpkg reset --hard" symmetric to the Git path here: a failure BEFORE the
        # restart means the old code is still running (nothing was restarted), and the
        # backup tarball plus the watchdog remain the real safety net should the package
        # state stay inconsistent anyway.
        finish_task(task_id, "echec", str(exc))
        log_action(username, "hyperlite_update", str(exc), "echec")


UPDATE_IN_PROGRESS_WINDOW = timedelta(minutes=15)
_apply_lock = threading.Lock()


def _update_in_progress():
    """Username of whoever started an update that is still running, else None.

    Only tasks started recently count: a task left 'en_cours' by a crash must not block
    updates forever."""
    cutoff = (datetime.now(UTC) - UPDATE_IN_PROGRESS_WINDOW).isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT username FROM tasks WHERE type = 'hyperlite_update' AND statut = 'en_cours' "
            "AND cree_le > ? ORDER BY cree_le DESC LIMIT 1",
            (cutoff,),
        ).fetchone()
    return (row["username"] or "another administrator") if row else None


@router.post("/apply", status_code=202)
def apply_update(user: dict = Depends(require_role("admin"))):
    # Two administrators (or a double click) must not start two updates at once: two package
    # installs, two backups and two restarts at the same time would corrupt each other.
    with _apply_lock:
        running_for = _update_in_progress()
        if running_for:
            raise HTTPException(
                status_code=409,
                detail=f"An update started by {running_for} is already running. Wait for it to finish.",
            )
        return _start_update(user)


def _start_update(user):
    if _install_method() == "apt":
        task_id = create_task("hyperlite_update", "hyperlite", node=None, username=user["username"])
        threading.Thread(target=_run_update_job_apt, args=(task_id, user["username"]), daemon=True).start()
        return {"task_id": task_id, "statut": "en_cours"}

    branch = _current_branch()
    if _is_dirty():
        log_action(user["username"], "hyperlite_update", "arbre non propre", "echec")
        raise HTTPException(
            status_code=409,
            detail="Working tree is not clean (uncommitted changes): commit or discard them before updating.",
        )

    task_id = create_task("hyperlite_update", "hyperlite", node=None, username=user["username"])
    threading.Thread(target=_run_update_job, args=(task_id, user["username"], branch), daemon=True).start()
    return {"task_id": task_id, "statut": "en_cours"}
