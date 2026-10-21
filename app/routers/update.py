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
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.core import host_system, offline_update, version
from app.core.audit import log_action
from app.core.database import DB_PATH
from app.core.http_safety import require_http_url
from app.core.security import require_role
from app.core.tasks import create_task, finish_task, task_log, update_task_progress

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/update", tags=["update"])

REPO_DIR = Path(__file__).resolve().parent.parent.parent  # /root/hyperlite
BACKUP_DIR = Path("/root/hyperlite-backups")
# The package of the version before the last update, for a return to it (docs/design/updates-1.0.md): kept on the
# node, so the return works without the mirror too. One version only.
PREVIOUS_DIR = Path("/var/lib/hyperlite/previous")
WATCHDOG_SCRIPT = REPO_DIR / "scripts" / "update_watchdog.sh"
# Where the updater keeps the copy of the watchdog it starts: out of the package, which may remove or move the
# original while the update runs (docs/design/privilege-separation.md, section 9).
WATCHDOG_STAGE = Path("/var/lib/hyperlite/update/update_watchdog.sh")


def _stage_watchdog():
    """A copy of the watchdog out of the package's files, made before apt runs: the copy is what gets started."""
    WATCHDOG_STAGE.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(WATCHDOG_STAGE.parent, 0o700)
    shutil.copy2(WATCHDOG_SCRIPT, WATCHDOG_STAGE)
    os.chmod(WATCHDOG_STAGE, 0o700)
    return WATCHDOG_STAGE


def _watchdog_argv(tarball, log_file, expected, previous):
    """The watchdog's command for a package update: the staged copy, told the version to wait for and the package to
    reinstall if the new one does not answer."""
    env = ["env", f"HYPERLITE_EXPECTED_VERSION={version.without_epoch(expected) or ''}"]
    if previous:
        env.append(f"HYPERLITE_PREVIOUS_DEB={previous['file']}")
    return [*env, "bash", str(WATCHDOG_STAGE), str(tarball), str(REPO_DIR), str(log_file)]


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
# never touches (uploaded ISOs, VM backups, exports, templates, imported disks). .env and the
# rest of data/ stay included, which is what a rollback must be able to restore.
# Excluded files are simply left in place by a rollback (tar extraction never deletes), so
# leaving them out cannot lose them; including them made each backup ~12 GB.
#
# The SQLite database is left out too: archived while the service writes to it, the database
# and its WAL came from different instants, and extracting them over the live files on
# rollback corrupted the database or dropped every write made during the update. A
# consistent copy is taken separately with SQLite's online backup API (_backup_database), and
# a rollback leaves the live database alone: the schema only ever grows (CREATE TABLE/ADD
# COLUMN IF NOT EXISTS), so the previous code runs on the newer database.
_BACKUP_EXCLUDES = [
    f"--exclude={DB_PATH.name}",
    f"--exclude={DB_PATH.name}-wal",
    f"--exclude={DB_PATH.name}-shm",
    f"--exclude={DB_PATH.name}-journal",
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
    """Keep only the newest `keep` update backups (archive and database copy); other files in the directory are
    never touched."""
    for pattern in ("hyperlite-backup-*.tar.gz", "hyperlite-backup-*.db"):
        backups = sorted(Path(directory).glob(pattern), key=lambda p: p.name, reverse=True)
        for old in backups[keep:]:
            with contextlib.suppress(OSError):
                old.unlink()


def _secure_backup_dir(directory):
    """The archives hold .env (the session signing key), the cluster's SSH key and the TLS key, and /root is
    traversable (the package opens it to libvirt-qemu for the ISOs): only root may read them. Also tightens the
    archives an earlier version left world-readable."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    for path in directory.glob("hyperlite-backup-*"):
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)


def _private_file(path):
    """Create `path` empty, readable by root only, before anything is written into it."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.close(fd)
    os.chmod(path, 0o600)


def _backup_database(dest):
    """A consistent copy of the live database (SQLite online backup: includes what is still in the WAL)."""
    _private_file(dest)
    src = sqlite3.connect(str(DB_PATH), timeout=30)
    try:
        out = sqlite3.connect(str(dest))
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()


def _run(cmd, cwd=None, timeout=180):
    return subprocess.run(cmd, cwd=str(cwd or REPO_DIR), capture_output=True, text=True, timeout=timeout)


# Force the C locale for every apt/dpkg command whose OUTPUT is parsed by this
# file. `apt-cache policy` translates its fields ("Candidat :" instead of
# "Candidate:") as soon as LANG/LC_ALL is not English, and the parsing in
# _check_update_apt() then failed SILENTLY (no error, just `remote_commit: null`
# and a wrong `up_to_date: false`). dpkg-query -f='...' is not concerned (already
# machine-readable whatever the locale) purpose is forced here too for consistency.
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


def _raw_installed_version():
    """The installed package version as dpkg knows it, epoch included."""
    r = _run_c(["dpkg-query", "-W", "-f=${Version}", "hyperlite"])
    return r.stdout.strip() if r.returncode == 0 else None


def _raw_candidate_version():
    for line in _run_c(["apt-cache", "policy", "hyperlite"]).stdout.splitlines():
        if line.strip().startswith("Candidate:"):
            return line.split(":", 1)[1].strip()
    return None


def _previous():
    """The kept package of the previous version: {"version", "file"} or None. Never a dated version under 1.0 or
    later: 1.0 renamed the database and the cluster's configuration (API v1), which a dated version cannot read, and
    the return reinstalls the package only, on the database as it is."""
    debs = sorted(PREVIOUS_DIR.glob("hyperlite_*.deb")) if PREVIOUS_DIR.is_dir() else []
    if not debs:
        return None
    r = _run_c(["dpkg-deb", "-f", str(debs[-1]), "Version"])
    if r.returncode != 0 or not r.stdout.strip():
        return None
    kept = version.without_epoch(r.stdout.strip())
    if version.dated(kept) and not version.dated(_raw_installed_version()):
        return None
    return {"version": kept, "file": debs[-1]}


def _keep_previous(task_id):
    """Before Hyperlite changes: keep the installed version's package (from the mirror's pool, which keeps every
    version). Best effort: without it, the return to the previous version is just not offered."""
    installed, candidate = _raw_installed_version(), _raw_candidate_version()
    if not installed or not candidate or installed == candidate:
        return
    if version.dated(installed) and not version.dated(candidate):
        # The update to 1.0: no return below it (see _previous), so nothing worth keeping.
        for old in PREVIOUS_DIR.glob("hyperlite_*.deb"):
            old.unlink(missing_ok=True)
        return
    PREVIOUS_DIR.mkdir(parents=True, exist_ok=True)
    work = PREVIOUS_DIR / ".download"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    r = subprocess.run(
        ["apt-get", "download", f"hyperlite={installed}"], cwd=str(work), capture_output=True, text=True, timeout=300
    )
    debs = list(work.glob("hyperlite_*.deb"))
    if r.returncode != 0 or not debs:
        # A package kept by an earlier update is older than the version now replaced: offering it as "the version
        # before the last update" would go back further than that, past whatever came in between.
        for old in PREVIOUS_DIR.glob("hyperlite_*.deb"):
            old.unlink()
        task_log(task_id, f"The package of {installed} could not be kept: the return to it will not be offered")
        shutil.rmtree(work, ignore_errors=True)
        return
    for old in PREVIOUS_DIR.glob("hyperlite_*.deb"):
        old.unlink()
    debs[0].rename(PREVIOUS_DIR / debs[0].name)
    shutil.rmtree(work, ignore_errors=True)
    task_log(task_id, f"Version {version.without_epoch(installed)} kept for a return to it")


def _dpkg_installed_version():
    r = _run_c(["dpkg-query", "-W", "-f=${Version}", "hyperlite"])
    return version.without_epoch(r.stdout.strip()) if r.returncode == 0 else None


# `apt-get update` used to fail PERSISTENTLY with an inconsistent size error
# (confirmed over 30 attempts spread over 10+ minutes, still inconsistent more
# than 1 h 20 after the last publication, checked by querying the origin
# directly). It was not a transient propagation window purpose a structural lack of
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


def _explain_apt_error(stderr):
    """Turn the apt messages caused by an out-of-sync repository mirror into something actionable."""
    text = (stderr or "").strip()
    if any(marker in text for marker in ("unexpected size", "Hash Sum mismatch", "Mirror sync in progress")):
        return (
            "The update repository is being refreshed and its files are momentarily out of sync. "
            "This usually clears within a few minutes: try again shortly. "
            f"Technical detail: {text[:240]}"
        )
    return text[:400]


APT_SOURCE_CONF = REPO_DIR / "installer" / "apt-source.conf"
APT_LIST = "/etc/apt/sources.list.d/hyperlite.list"


def _official_apt_url():
    """HYPERLITE_APT_URL from installer/apt-source.conf (the single source of truth), None if unreadable."""
    with contextlib.suppress(OSError):
        for line in APT_SOURCE_CONF.read_text().splitlines():
            if line.startswith("HYPERLITE_APT_URL="):
                return line.split("=", 1)[1].strip().strip('"').rstrip("/")
    return None


def _hyperlite_repositories(policy_stdout):
    """Repository URLs apt knows the hyperlite package from, read from the version table of
    `apt-cache policy hyperlite` ("500 https://host stable/main amd64 Packages"). The installed version
    also shows up as "100 /var/lib/dpkg/status", which is not a repository and is ignored."""
    urls, in_table = [], False
    for raw in policy_stdout.splitlines():
        line = raw.strip()
        if line.startswith("Version table:"):
            in_table = True
            continue
        parts = line.split()
        if in_table and len(parts) >= 2 and parts[0].isdigit() and "://" in parts[1]:
            url = parts[1].rstrip("/")
            if url not in urls:
                urls.append(url)
    return urls


def _source_problem(policy_stdout):
    """Why the update check cannot be trusted on this machine, or None. Without the Hyperlite repository
    among the APT sources, the only candidate apt knows is the installed version itself, so the check used
    to answer "up to date" on a machine stuck on an old release (a real case: a source line commented out).
    Same when the repository comes from an address that no longer receives the publications."""
    official = _official_apt_url()
    urls = _hyperlite_repositories(policy_stdout)
    suite = host_system.channel() or "stable"
    line = f"deb [signed-by=/usr/share/keyrings/hyperlite-archive-keyring.gpg] {official or '<repository-url>'} {suite} main"
    if not urls:
        return (
            "The Hyperlite repository is not among this machine's APT sources, so new versions cannot be seen "
            f"(the installed version would always look up to date). Restore this line in {APT_LIST}: {line}"
        )
    if official and official not in urls:
        return (
            f"This machine gets Hyperlite from {', '.join(urls)}, which is no longer the official address and no "
            f"longer receives new versions. Replace the line in {APT_LIST} with: {line}"
        )
    return None


def _check_update_apt():
    installed = _dpkg_installed_version()
    # For /update/check (an interactive call, the user is waiting in front of the
    # UI): few attempts and a short delay. A real network outage must come up quickly
    # rather than make the user wait needlessly after a simple click.
    upd = _apt_update_with_retry(attempts=4, delay_s=8, timeout=25)
    if upd is None or upd.returncode != 0:
        detail = _explain_apt_error(upd.stderr) if upd is not None else "timed out"
        return {
            "verifiable": False,
            "error": f"Unable to contact the APT repository: {detail}",
            "commit_local": installed,
        }

    policy = _run_c(["apt-cache", "policy", "hyperlite"])
    problem = _source_problem(policy.stdout)
    if problem:
        return {"verifiable": False, "error": problem, "commit_local": installed, "source_problem": True}
    candidate = None
    for line in policy.stdout.splitlines():
        line = line.strip()
        if line.startswith("Candidate:"):
            candidate = version.without_epoch(line.split(":", 1)[1].strip())
    # The package can say one version while the service runs another: an update whose new code did not answer
    # in time is rolled back from the backup, purpose dpkg still records the new package. Comparing only dpkg with
    # the candidate then answered "up to date" and the update could never be applied again.
    running = version.STARTUP_VERSION
    rolled_back = bool(running and installed and running != installed)
    up_to_date = bool(candidate) and installed == candidate and not rolled_back
    if rolled_back:
        changelog = [
            f"The update to {installed} was rolled back: {running} is still running. Applying the update installs it again."
        ]
    else:
        changelog = [f"New version available: {candidate}"] if not up_to_date and candidate else []

    return {
        "verifiable": True,
        "branch": "apt",
        "commit_local": running or installed,
        "remote_commit": candidate,
        "up_to_date": up_to_date,
        "clean_tree": True,  # not applicable in apt mode (no Git working tree)
        "changelog": changelog,
        "rolled_back": rolled_back,
    }


SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(~[a-z0-9.]+)?$")
NOTES_TIMEOUT_S = 5


def _deb(value):
    """The package version apt compares: semver releases carry the epoch 1: (installer/build-deb.sh)."""
    return f"1:{value}" if value and SEMVER_RE.match(value) else value


def _newer(a, b):
    """True when package version a is newer than b (dpkg's own ordering, epochs and ~ included)."""
    return _run_c(["dpkg", "--compare-versions", _deb(a), "gt", _deb(b)]).returncode == 0


def _fetch_text(url):
    try:
        require_http_url(url)
    except ValueError as e:
        raise OSError(str(e)) from e
    req = urllib.request.Request(url, headers={"User-Agent": "hyperlite-update-check"})  # noqa: S310 -- checked above
    with urllib.request.urlopen(req, timeout=NOTES_TIMEOUT_S) as resp:  # noqa: S310 -- checked above
        return resp.read(512 * 1024).decode("utf-8", "replace")


def _release_notes(installed, candidate, lang):
    """The notes of every version after the installed one, up to the candidate, newest first, in `lang` when they
    exist in it (else English). Published next to the repository (scripts/release-notes.sh); none before 1.0.0. A
    mirror that does not answer only leaves the notes out: the update itself does not depend on them."""
    base = _official_apt_url()
    if not base or not candidate or not installed:
        return []
    try:
        index = json.loads(_fetch_text(f"{base}/notes/index.json"))
    except (OSError, ValueError):
        return []
    notes = []
    for entry in index if isinstance(index, list) else []:
        v = str(entry.get("version", "")) if isinstance(entry, dict) else ""
        if not SEMVER_RE.match(v) or not _newer(v, installed) or _newer(v, candidate):
            continue
        if "~" in v and "~" not in candidate:
            continue  # a test build's notes, for a node going to a release (the index lists both channels)
        suffix = "fr" if lang == "fr" and entry.get("fr") else "en"
        try:
            notes.append({"version": v, "language": suffix, "text": _fetch_text(f"{base}/notes/{v}.{suffix}.md")})
        except OSError:
            continue
    return notes


@router.get("/check")
def check_update(lang: str = "en", user: dict = Depends(require_role("admin"))):
    if _install_method() == "apt":
        result = _check_update_apt()
        if result.get("verifiable") and not result.get("up_to_date"):
            result["notes"] = _release_notes(result.get("commit_local"), result.get("remote_commit"), lang)
        if result.get("verifiable"):
            # apt-get update ran above: the Debian side is current too.
            result["system"] = _system_summary()
        previous = _previous()
        result["previous"] = previous["version"] if previous else None
        result["channel"] = host_system.channel()
        return result

    branch = _current_branch()
    local = _current_commit()
    try:
        fetch = _run(["git", "fetch", "origin", branch], timeout=30)
    except subprocess.TimeoutExpired:
        return {
            "verifiable": False,
            "error": "Timed out contacting the remote repository (network?)",
            "commit_local": local,
        }

    if fetch.returncode != 0:
        return {
            "verifiable": False,
            "error": f"Unable to contact the remote repository: {fetch.stderr.strip()[:400]}",
            "commit_local": local,
        }

    remote_r = _run(["git", "rev-parse", f"origin/{branch}"])
    remote = remote_r.stdout.strip() if remote_r.returncode == 0 else None
    up_to_date = bool(remote) and local == remote
    changelog = []
    if not up_to_date and remote and local:
        log_r = _run(["git", "log", "--oneline", f"{local}..{remote}"])
        if log_r.returncode == 0:
            changelog = [line for line in log_r.stdout.strip().splitlines() if line]

    return {
        "verifiable": True,
        "branch": branch,
        "commit_local": local,
        "remote_commit": remote,
        "up_to_date": up_to_date,
        "clean_tree": not _is_dirty(),
        "changelog": changelog,
    }


def _backup(task_id):
    _secure_backup_dir(BACKUP_DIR)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    tarball = BACKUP_DIR / f"hyperlite-backup-{stamp}.tar.gz"
    if DB_PATH.exists():
        _backup_database(BACKUP_DIR / f"hyperlite-backup-{stamp}.db")
    _private_file(tarball)  # tar keeps the mode of an existing file: never world-readable, even for a moment
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
        log_action(username, "hyperlite_update_step", message, "success")

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
        log_action(username, "hyperlite_update_step", f"{old_commit[:8]} -> {new_commit[:8]}", "success")

        # The watchdog is started BEFORE the restart, as a fully detached process
        # (start_new_session): it must survive the death of THIS Python process when
        # systemctl stops it. It, not this thread, verifies that the NEW code starts
        # correctly and rolls back automatically if not (see scripts/update_watchdog.sh;
        # this process cannot verify its own replacement).
        _spawn_outside_service(
            "hyperlite-update-watchdog",
            ["bash", str(WATCHDOG_SCRIPT), str(tarball), str(REPO_DIR), str(log_file), old_commit or ""],
        )
        _spawn_outside_service("hyperlite-update-restart", ["bash", "-c", "sleep 2 && systemctl restart hyperlite"])

        # This process will die in ~2 s (the restart above): the task is marked
        # "succeeded" optimistically before dying rather than leaving a task forever
        # "running". The watchdog is the real safety net if the new code does not start.
        finish_task(task_id, "succeeded")
        log_action(username, "hyperlite_update", f"{old_commit} -> {new_commit}", "success")

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
        finish_task(task_id, "failed", str(exc))
        log_action(username, "hyperlite_update", str(exc), "failed")


def _apt_command(args, env=()):
    """apt-get with `args`, in a systemd unit of its own when possible: a package that restarts services (libvirt)
    must not take the upgrade down with the service that started it. Hyperlite's own postinst does not restart
    Hyperlite (HYPERLITE_SKIP_RESTART): the update job does, under its watchdog."""
    apt = [
        "env", "HYPERLITE_SKIP_RESTART=1", "DEBIAN_FRONTEND=noninteractive", "LC_ALL=C", *env,
        "apt-get", "-y", "-o", "Dpkg::Options::=--force-confdef", "-o", "Dpkg::Options::=--force-confold", *args,
    ]  # fmt: skip
    if shutil.which("systemd-run") and Path("/run/systemd/system").is_dir():
        unit = f"hyperlite-node-update-{time.time_ns()}"
        return ["systemd-run", "--quiet", "--collect", "--wait", "--pipe", f"--unit={unit}", *apt]
    return apt


def _full_upgrade_command():
    """apt-get full-upgrade of every package (see _apt_command)."""
    return _apt_command(["full-upgrade"])


def _system_summary():
    """The Debian side of the node's update, for the update dialog: how many packages, how many security ones, and
    whether a reboot will be needed (predicted from their names) or already is."""
    try:
        u = host_system.updates()
    except host_system.SettingError:
        return None
    if not u.get("available"):
        return None
    pkgs = [p for p in u["packages"] if p["name"] != host_system.SELF_PACKAGE]
    names = [p["name"] for p in pkgs]
    return {
        "packages": len(pkgs),
        "security": sum(1 for p in pkgs if p["security"]),
        "names": names[:300],
        "restart_scheduled": host_system.needs_reboot(names),
        "restart_required": u["restart_required"],
    }


def _run_update_job_apt(task_id, username, system=True, offline=False):
    """The node's update. offline: from the update file received by POST /update/offline (app/core/offline_update.py)
    instead of the repositories: its packages only, no network."""
    log_file = BACKUP_DIR / "update.log"

    def step(pct, message):
        update_task_progress(task_id, pct)
        log_action(username, "hyperlite_update_step", message, "success")

    old_version = _dpkg_installed_version()
    try:
        step(5, "Backing up the current state (code + config + database)")
        tarball = _backup(task_id)
        _stage_watchdog()

        apt_opts, apt_env, package = [], [], None
        if offline:
            step(15, "Checking the update file and extracting it")
            new_file_version, package, bundle = offline_update.extract()
            task_log(task_id, f"Update file of Hyperlite {new_file_version}, signature and checksums verified")
            apt_opts = offline_update.local_apt_options(bundle)
            step(20, "Reading the packages of the update file")
            upd = _run_c(["apt-get", *apt_opts, "update"], timeout=120)
        else:
            step(20, "Updating the APT index")
            # See _apt_update_with_retry above: the repository is served directly by nginx
            # (no CDN), so a modest retry is now enough. Slightly more patient than
            # /update/check since this is a background task (the user already sees a progress
            # bar).
            upd = _apt_update_with_retry(attempts=6, delay_s=10, timeout=30)
        if upd is None or upd.returncode != 0:
            detail = upd.stderr.strip()[:400] if upd is not None else "timed out"
            raise RuntimeError(f"apt-get update failed: {detail}")

        rolled_back = bool(version.STARTUP_VERSION and old_version and old_version != version.STARTUP_VERSION)
        env = {**os.environ, "HYPERLITE_SKIP_RESTART": "1", "LC_ALL": "C", "LANG": "C"}
        if not rolled_back:
            _keep_previous(task_id)
        if rolled_back and not offline:
            # After a rollback the package is already "installed" at the version that did not start: reinstall it.
            step(35, "Installing Hyperlite again (the last update was rolled back)")
            again = subprocess.run(
                ["apt-get", "install", "--reinstall", "-y", "hyperlite"],
                cwd=str(REPO_DIR), capture_output=True, text=True, timeout=300, env=env,
            )  # fmt: skip
            if again.returncode != 0:
                raise RuntimeError(f"apt-get install failed: {again.stderr.strip()[:400]}")
        if offline:
            step(40, "Installing the packages of the update file")
            if system:
                code = host_system.run_upgrade(
                    _apt_command([*apt_opts, "full-upgrade"], apt_env), lambda line: task_log(task_id, line)
                )
                if code != 0:
                    raise RuntimeError(f"apt-get full-upgrade exited with {code}: see the task log")
            # The package from the file (its dependencies from the bundle); again after a rolled back update.
            install = [*apt_opts, "install", *(["--reinstall"] if rolled_back else []), str(package)]
            code = host_system.run_upgrade(_apt_command(install, apt_env), lambda line: task_log(task_id, line))
            if code != 0:
                raise RuntimeError(f"apt-get install exited with {code}: see the task log")
        elif system:
            # One update for the whole node: Hyperlite and every Debian package (docs/design/updates-1.0.md).
            step(40, "Installing the updates of Hyperlite and Debian (apt-get full-upgrade)")
            code = host_system.run_upgrade(_full_upgrade_command(), lambda line: task_log(task_id, line))
            if code != 0:
                raise RuntimeError(f"apt-get full-upgrade exited with {code}: see the task log")
        else:
            # HYPERLITE_SKIP_RESTART: the package's postinst NORMALLY restarts the service by itself, which here
            # would kill apt-get in the middle of its own postinst; this thread restarts it right afterwards.
            step(40, "Installing the new version (apt-get install)")
            install = subprocess.run(
                ["apt-get", "install", "--only-upgrade", "-y", "hyperlite"],
                cwd=str(REPO_DIR), capture_output=True, text=True, timeout=300, env=env,
            )  # fmt: skip
            if install.returncode != 0:
                raise RuntimeError(f"apt-get install failed: {install.stderr.strip()[:400]}")
        new_version = _dpkg_installed_version()
        if host_system.REBOOT_REQUIRED.exists():
            task_log(task_id, "A reboot of the node is needed to use some of the new packages")
            log_action(username, "host_reboot_required", "host", "success", "after an update")
        if offline:
            offline_update.discard()  # applied: half a gigabyte less on the node
        if new_version == old_version and not rolled_back:
            # Only Debian packages changed: Hyperlite keeps running, nothing to restart or watch.
            finish_task(task_id, "succeeded")
            log_action(username, "hyperlite_update", f"Debian packages updated, Hyperlite {new_version}", "success")
            return

        step(85, "Checking the database schema (no migration required)")
        step(90, "Restarting the service and post-update verification")
        log_action(username, "hyperlite_update_step", f"{old_version} -> {new_version}", "success")

        # The same safety net as the Git path: a detached watchdog BEFORE the restart
        # (it survives the death of this process), which verifies that the new code starts
        # correctly and restores the tarball otherwise.
        _spawn_outside_service("hyperlite-update-watchdog", _watchdog_argv(tarball, log_file, new_version, _previous()))
        _spawn_outside_service("hyperlite-update-restart", ["bash", "-c", "sleep 2 && systemctl restart hyperlite"])

        finish_task(task_id, "succeeded")
        log_action(username, "hyperlite_update", f"{old_version} -> {new_version}", "success")

    except Exception as exc:
        # No "dpkg reset --hard" symmetric to the Git path here: a failure BEFORE the
        # restart means the old code is still running (nothing was restarted), and the
        # backup tarball plus the watchdog remain the real safety net should the package
        # state stay inconsistent anyway.
        if offline:
            offline_update.clean_extracted()  # the received file stays, to try again
        finish_task(task_id, "failed", str(exc))
        log_action(username, "hyperlite_update", str(exc), "failed")


UPDATE_IN_PROGRESS_WINDOW = timedelta(minutes=15)
_apply_lock = threading.Lock()


def _update_in_progress():
    """Username of whoever started an update that is still running, else None.

    Only tasks started recently count: a task left 'running' by a crash must not block
    updates forever."""
    from app.repositories import registry

    cutoff = (datetime.now(UTC) - UPDATE_IN_PROGRESS_WINDOW).isoformat()
    username = registry.tasks().sync.latest_running("hyperlite_update", cutoff)
    return (username or "another administrator") if username is not None else None


class ApplyRequest(BaseModel):
    # The node's Debian packages are updated with Hyperlite (one update per node); False: Hyperlite alone.
    system: bool = True


@router.post("/apply", status_code=202)
def apply_update(payload: ApplyRequest | None = None, user: dict = Depends(require_role("admin"))):
    # Two administrators (or a double click) must not start two updates at once: two package
    # installs, two backups and two restarts at the same time would corrupt each other.
    with _apply_lock:
        running_for = _update_in_progress()
        if running_for:
            raise HTTPException(
                status_code=409,
                detail=f"An update started by {running_for} is already running. Wait for it to finish.",
            )
        return _start_update(user, payload.system if payload else True)


def _start_update(user, system=True):
    if _install_method() == "apt":
        task_id = create_task("hyperlite_update", "hyperlite", node=None, username=user["username"])
        threading.Thread(target=_run_update_job_apt, args=(task_id, user["username"], system), daemon=True).start()
        return {"task_id": task_id, "status": "running"}

    branch = _current_branch()
    if _is_dirty():
        log_action(user["username"], "hyperlite_update", "arbre non propre", "failed")
        raise HTTPException(
            status_code=409,
            detail="Working tree is not clean (uncommitted changes): commit or discard them before updating.",
        )

    task_id = create_task("hyperlite_update", "hyperlite", node=None, username=user["username"])
    threading.Thread(target=_run_update_job, args=(task_id, user["username"], branch), daemon=True).start()
    return {"task_id": task_id, "status": "running"}


def _run_rollback_job(task_id, username, previous):
    log_file = BACKUP_DIR / "update.log"
    old_version = _dpkg_installed_version()
    try:
        update_task_progress(task_id, 5)
        tarball = _backup(task_id)
        _stage_watchdog()
        update_task_progress(task_id, 30)
        task_log(task_id, f"Installing {previous['version']} again")
        env = {
            **os.environ,
            "HYPERLITE_SKIP_RESTART": "1",
            "LC_ALL": "C",
            "LANG": "C",
            "DEBIAN_FRONTEND": "noninteractive",
        }
        r = subprocess.run(
            ["apt-get", "install", "-y", "--allow-downgrades", str(previous["file"])],
            cwd=str(REPO_DIR), capture_output=True, text=True, timeout=600, env=env,
        )  # fmt: skip
        if r.returncode != 0:
            raise RuntimeError(f"apt-get install failed: {(r.stderr or r.stdout).strip()[-400:]}")
        # One step back only: the version left is the one the mirror offers again.
        previous["file"].unlink(missing_ok=True)
        update_task_progress(task_id, 90)
        _spawn_outside_service(
            "hyperlite-update-watchdog",
            _watchdog_argv(tarball, log_file, previous["version"], None),
        )
        _spawn_outside_service("hyperlite-update-restart", ["bash", "-c", "sleep 2 && systemctl restart hyperlite"])
        finish_task(task_id, "succeeded")
        log_action(username, "hyperlite_rollback", f"{old_version} -> {previous['version']}", "success")
    except Exception as exc:
        finish_task(task_id, "failed", str(exc))
        log_action(username, "hyperlite_rollback", str(exc), "failed")


# ---- Update from a file, for a node without network (app/core/offline_update.py) ----


def _offline_summary(info, lang):
    installed = _dpkg_installed_version()
    notes = info["notes"].get("fr" if lang == "fr" else "en") or info["notes"].get("en")
    return {
        "version": info["version"],
        "debian": info["debian"],
        "size_mb": info["size_mb"],
        "installed": installed,
        # Older than what runs: going back is POST /update/rollback, not an older file.
        "oldest": bool(installed and _newer(installed, info["version"])),
        "notes": notes,
    }


@router.post("/offline")
async def upload_offline_update(
    file: UploadFile = File(...), lang: str = "en", user: dict = Depends(require_role("admin"))
):
    """Receives an update file (published next to the ISO) and checks its signature and contents. Nothing is
    installed: POST /update/offline/apply does."""
    if _install_method() != "apt":
        raise HTTPException(status_code=409, detail="Only a node installed from the package can be updated from a file")
    try:
        try:
            info = await run_in_threadpool(offline_update.receive, file.file)
        finally:
            await file.close()
    except offline_update.OfflineUpdateError as e:
        log_action(user["username"], "upload_update_file", (file.filename or "")[:80], "failed", str(e))
        raise HTTPException(status_code=422, detail=str(e)) from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"The update file could not be stored: {e}") from e
    log_action(user["username"], "upload_update_file", info["version"], "success")
    return _offline_summary(info, lang)


@router.get("/offline")
def get_offline_update(lang: str = "en", user: dict = Depends(require_role("admin"))):
    """The update file received and not applied yet, or {"version": null}."""
    info = offline_update.stored()
    return _offline_summary(info, lang) if info else {"version": None}


@router.delete("/offline")
def delete_offline_update(user: dict = Depends(require_role("admin"))):
    offline_update.discard()
    log_action(user["username"], "delete_update_file", "hyperlite", "success")
    return {"version": None}


@router.post("/offline/apply", status_code=202)
def apply_offline_update(payload: ApplyRequest | None = None, user: dict = Depends(require_role("admin"))):
    """The update of section 5 from the received file: its packages only, under the same backup and watchdog."""
    if _install_method() != "apt":
        raise HTTPException(status_code=409, detail="Only a node installed from the package can be updated from a file")
    info = offline_update.stored()
    if not info:
        raise HTTPException(status_code=409, detail="No valid update file received on this node")
    if _offline_summary(info, "en")["oldest"]:
        raise HTTPException(
            status_code=409,
            detail=f"This file holds {info['version']}, older than the installed version: it is not installed",
        )
    with _apply_lock:
        running_for = _update_in_progress()
        if running_for:
            raise HTTPException(
                status_code=409,
                detail=f"An update started by {running_for} is already running. Wait for it to finish.",
            )
        task_id = create_task("hyperlite_update", "hyperlite", node=None, username=user["username"])
        system = payload.system if payload else True
        threading.Thread(
            target=_run_update_job_apt, args=(task_id, user["username"], system, True), daemon=True
        ).start()
    return {"task_id": task_id, "status": "running", "version": info["version"]}


class ChannelRequest(BaseModel):
    channel: str


@router.get("/channel")
def get_channel(user: dict = Depends(require_role("admin"))):
    """The channel this node updates from: `stable` (releases) or `test` (every merge into the test branch)."""
    return {"channel": host_system.channel(), "channels": list(host_system.CHANNELS)}


@router.put("/channel")
def put_channel(payload: ChannelRequest, user: dict = Depends(require_role("admin"))):
    """Moves the node to another channel. Nothing is installed: the next check shows what the channel offers, and
    leaving test keeps the installed build until a stable release is newer (no downgrade)."""
    try:
        new = host_system.set_channel(payload.channel)
    except host_system.SettingError as e:
        log_action(user["username"], "set_update_channel", payload.channel[:20], "failed", str(e))
        raise HTTPException(status_code=422, detail=str(e)) from e
    log_action(user["username"], "set_update_channel", new, "success")
    return {"channel": new, "channels": list(host_system.CHANNELS)}


@router.post("/rollback", status_code=202)
def rollback_update(user: dict = Depends(require_role("admin"))):
    """Back to the version before the last update of Hyperlite (its package kept on the node), under the same
    safety net as an update. Hyperlite only: the Debian packages stay as they are."""
    if _install_method() != "apt":
        raise HTTPException(status_code=409, detail="Only a node installed from the package can go back a version")
    previous = _previous()
    if not previous:
        raise HTTPException(status_code=409, detail="No previous version kept on this node")
    with _apply_lock:
        running_for = _update_in_progress()
        if running_for:
            raise HTTPException(
                status_code=409,
                detail=f"An update started by {running_for} is already running. Wait for it to finish.",
            )
        task_id = create_task("hyperlite_update", "hyperlite", node=None, username=user["username"])
        threading.Thread(target=_run_rollback_job, args=(task_id, user["username"], previous), daemon=True).start()
    return {"task_id": task_id, "status": "running", "version": previous["version"]}
