"""The Terminal's boxes (docs/design/workbench.md, sections 4 to 6): hyperlite-daemon mounts the account's home, starts
the box and its relay as transient units, and hands the API a terminal on the box's tmux. The units' properties come
from app/core/workbench_box.py, never from the request: an operation names a session, its uid and its token only, each
checked against the API's records here."""

import hashlib
import json
import os
import shutil
import subprocess
import time

from pydantic import Field

from app.daemon.needs import ADMIN, by_rule, own_account
from app.daemon.registry import Args, DaemonError, Handover, operation

SID = Field(min_length=16, max_length=16, pattern=r"^[a-f0-9]{16}$")
HOME_MB = 1024
START_WAIT_S = 5


def _run(argv, timeout=60):
    """A command's result (a seam the tests replace)."""
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def _session(sid):
    from app.core.database import get_conn

    with get_conn() as db:
        row = db.execute("SELECT * FROM workbench_sessions WHERE id = ?", (sid,)).fetchone()
    return dict(row) if row else None


@own_account
def _owner(user, args):
    """The session is the caller's (an administrator: any) and still open."""
    row = _session(args.sid)
    if row is None or row["state"] != "open":
        return "No such Terminal session"
    if user.get("api_token_id") is not None or user.get("token_scope"):
        return "A Terminal session is opened from a signed-in session, not with an API token"
    if user["role"] != "admin" and row["account"] != user["username"]:
        return "This Terminal session is another account's"
    return None


class OpenArgs(Args):
    sid: str = SID
    token: str = Field(min_length=8, max_length=256)


def _may_open(user, args):
    """The caller's own session, with the uid the API gave its account and a Terminal token of that account."""
    refusal = _owner(user, args)
    if refusal:
        return refusal
    from app.core.api_tokens import verify_token

    holder = verify_token(args.token)
    row = _session(args.sid)
    if holder is None or holder["username"] != row["account"] or holder.get("api_token_id") != row["token_id"]:
        return "The Terminal token does not belong to this session"
    return None


def _fingerprint():
    """The SHA-256 of this node's certificate (DER), which the client in the box pins."""
    import base64
    import re

    from app.core import paths

    pem = (paths.TLS_DIR / "hyperlite.crt").read_text()
    body = re.search(r"-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----", pem, re.S).group(1)
    return hashlib.sha256(base64.b64decode("".join(body.split()))).hexdigest()


def _ensure_user(uid):
    """The host account the box runs as (`hl-term-<uid>`, a system account without a password or a home on the
    host), made the first time: systemd runs a unit only as a user the system knows."""
    import pwd

    try:
        pwd.getpwuid(uid)
        return
    except KeyError:
        pass
    import grp

    if uid not in {g.gr_gid for g in grp.getgrall()}:
        r = _run(["groupadd", "--system", "--gid", str(uid), f"hl-term-{uid}"])
        if r.returncode != 0:
            raise DaemonError("failed", f"The Terminal's group was not made: {r.stderr.strip()[-200:]}")
    r = _run(["useradd", "--system", "--uid", str(uid), "--gid", str(uid), "--no-create-home", "--home-dir",
              "/home/workbench", "--shell", "/bin/bash", f"hl-term-{uid}"])  # fmt: skip
    if r.returncode != 0:
        raise DaemonError("failed", f"The Terminal's account was not made: {r.stderr.strip()[-200:]}")


def _home(uid):
    """The account's home image made (the first time), checked and mounted; its mount point."""
    from app.core import workbench_box

    image = workbench_box.HOMES / f"{uid}.img"
    mount = workbench_box.MOUNTS / str(uid)
    workbench_box.HOMES.mkdir(mode=0o700, parents=True, exist_ok=True)
    mount.mkdir(mode=0o755, parents=True, exist_ok=True)
    if _run(["mountpoint", "-q", str(mount)]).returncode == 0:
        return mount
    if not image.exists():
        with open(image, "wb") as f:
            f.truncate(HOME_MB * 1024 * 1024)
        os.chmod(image, 0o600)
        r = _run(["mkfs.ext4", "-q", "-F", "-m", "0", "-E", f"root_owner={uid}:{uid}", "-L", "hl-terminal", str(image)])
        if r.returncode != 0:
            image.unlink(missing_ok=True)
            raise DaemonError("failed", f"The Terminal home was not made: {r.stderr.strip()[-200:]}")
    _run(["e2fsck", "-p", str(image)], timeout=120)  # repairs what it can; a damaged image still mounts or fails below
    r = _run(["mount", "-o", "loop,nodev,nosuid", str(image), str(mount)])
    if r.returncode != 0:
        raise DaemonError("failed", f"The Terminal home was not mounted: {r.stderr.strip()[-200:]}")
    return mount


def _release_home(uid, sid):
    """Unmount the account's home once no other box of it runs."""
    from app.core import workbench_box
    from app.core.database import get_conn

    with get_conn() as db:
        others = db.execute(
            "SELECT COUNT(*) FROM workbench_sessions WHERE uid = ? AND state = 'open' AND id != ?", (uid, sid)
        ).fetchone()[0]
    if others == 0:
        _run(["umount", str(workbench_box.MOUNTS / str(uid))])


def _stop_units(sid):
    from app.core import workbench_box

    for unit in (workbench_box.relay_unit_name(sid), workbench_box.unit_name(sid)):
        _run(["systemctl", "stop", unit], timeout=30)


@operation("workbench.open", OpenArgs, needs=by_rule(_may_open))
def open_box(args, ctx):
    """The account's home mounted, the client's config written, the box and its relay started."""
    from app.core import workbench_box

    row = _session(args.sid)
    uid = row["uid"]
    run = workbench_box.RUN / args.sid
    try:
        _ensure_user(uid)
        _home(uid)
        run.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chown(run, uid, uid)
        config = run / "hyperlite.json"
        fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(
                {"default": "local", "servers": {"local": {"url": "https://127.0.0.1:8443", "username": row["account"],
                 "token": args.token, "token_id": row["token_id"], "fingerprint": _fingerprint()}}}, f,
            )  # fmt: skip
        os.chown(config, uid, uid)
        from app.core import workbench

        limits = workbench.settings()
        for argv in (workbench_box.box_argv(args.sid, uid, limits), workbench_box.relay_argv(args.sid, uid)):
            r = _run(argv)
            if r.returncode != 0:
                raise DaemonError("failed", f"The Terminal did not start: {(r.stderr or r.stdout).strip()[-300:]}")
        deadline = time.monotonic() + START_WAIT_S
        while not (run / "tmux.sock").exists():
            if time.monotonic() > deadline:
                raise DaemonError("failed", "The Terminal's shell did not start in time")
            time.sleep(0.1)
    except BaseException:
        _stop_units(args.sid)
        shutil.rmtree(run, ignore_errors=True)
        _release_home(uid, args.sid)
        raise
    return {"started": args.sid}


class AttachArgs(Args):
    sid: str = SID
    cols: int = Field(80, ge=1, le=1000)
    rows: int = Field(24, ge=1, le=1000)


@operation("workbench.attach", AttachArgs, needs=by_rule(_owner))
def attach(args, ctx):
    """A terminal on the box's tmux: a client run outside the box as the session's uid; the shells stay inside."""
    from app.core import console_sessions, workbench_box

    row = _session(args.sid)
    if not (workbench_box.RUN / args.sid / "tmux.sock").exists():
        raise DaemonError("not_found", "This Terminal session has ended")
    env = {"TERM": "xterm-256color", "LANG": "C.UTF-8", "PATH": console_sessions.PATH, "HOME": "/nonexistent"}
    fd, psid = console_sessions.open_pty(workbench_box.attach_argv(args.sid, row["uid"]), env, args.cols, args.rows)
    return Handover({"session": psid}, [fd])


class SessionArgs(Args):
    sid: str = SID


@operation("workbench.close", SessionArgs, needs=by_rule(_owner))
def close(args, ctx):
    """The box and its relay stopped, the session's directory removed, the home unmounted when no other box uses it.
    The API marks the session ended once this answered."""
    from app.core import workbench_box

    row = _session(args.sid)
    _stop_units(args.sid)
    shutil.rmtree(workbench_box.RUN / args.sid, ignore_errors=True)
    if row:
        _release_home(row["uid"], args.sid)
    return {"closed": args.sid}


@operation("workbench.list", needs=ADMIN)
def list_boxes(args, ctx):
    """The session ids of the boxes running on this node (at start, to re-adopt or end them)."""
    r = _run(["systemctl", "list-units", "--plain", "--no-legend", "--all", "hyperlite-workbench-*.service"])
    found = []
    for line in r.stdout.splitlines():
        name = line.split()[0] if line.split() else ""
        if name.startswith("hyperlite-workbench-") and not name.endswith("-relay.service"):
            found.append(name[len("hyperlite-workbench-") : -len(".service")])
    return {"sessions": found}


class HomeArgs(Args):
    uid: int = Field(ge=700000, le=799999)


@operation("workbench.home_delete", HomeArgs, needs=ADMIN)
def home_delete(args, ctx):
    from app.core import workbench_box

    _run(["umount", str(workbench_box.MOUNTS / str(args.uid))])
    (workbench_box.HOMES / f"{args.uid}.img").unlink(missing_ok=True)
    return {"deleted": args.uid}


# --- Tools and files of the home (docs/design/workbench.md, sections 8 and 11) ---

TOFU_VERSION = "1.13.9"
TOFU_SHA256 = "bc44eb6b06446ba83bd4072114891987f590f66cca712737a7f1147c50c539ce"
TOFU_URL = f"https://github.com/opentofu/opentofu/releases/download/v{TOFU_VERSION}/tofu_{TOFU_VERSION}_linux_amd64.zip"
FILE_MAX = 20 * 1024 * 1024


def _home_path(row, relative):
    """The file `relative` inside the session's mounted home, refused when it obviously leaves it (`..`, an absolute
    path). Every read and write then runs as the session's own account (_as_user), so a link inside the home cannot
    lead the daemon anywhere that account could not go itself."""
    from app.core import workbench_box

    root = workbench_box.MOUNTS / str(row["uid"])
    parts = [p for p in (relative or "").split("/") if p not in ("", ".")]
    if not parts or relative.startswith("/") or "\0" in relative or ".." in parts:
        raise DaemonError("refused", "A path inside your home, such as notes/plan.txt")
    return root, root.joinpath(*parts)


def _inside(row, root, target):
    """Refuse a path that resolves outside the home (a link to the host's own files): resolved as the session's account,
    so a world-readable file of the host hidden in the box is not handed out either."""
    real = _as_user(row["uid"], ["realpath", "-m", "--", str(target)]).decode().strip()
    base = _as_user(row["uid"], ["realpath", "--", str(root)]).decode().strip()
    if real != base and not real.startswith(base + "/"):
        raise DaemonError("refused", "That path leads out of your home")


def _as_user(uid, argv, data=None, timeout=120):
    """argv run as the session's account, without privileges."""
    cmd = ["setpriv", f"--reuid={uid}", f"--regid={uid}", "--clear-groups", "--inh-caps=-all", "--no-new-privs", "--",
           *argv]  # fmt: skip
    r = subprocess.run(cmd, input=data, capture_output=True, timeout=timeout)
    if r.returncode != 0:
        raise DaemonError("refused", (r.stderr.decode(errors="replace").strip() or "Refused")[-300:])
    return r.stdout


@operation("workbench.install_tofu", SessionArgs, needs=by_rule(_owner))
def install_tofu(args, ctx):
    """OpenTofu, a pinned release checked against its SHA-256, into the home's ~/.local/bin (the box has no network)."""
    import io
    import urllib.request
    import zipfile

    row = _session(args.sid)
    _, target = _home_path(row, ".local/bin/tofu")
    with urllib.request.urlopen(TOFU_URL, timeout=120) as r:
        data = r.read(200 * 1024 * 1024)
    if hashlib.sha256(data).hexdigest() != TOFU_SHA256:
        raise DaemonError("failed", "The OpenTofu download does not match its checksum: not installed")
    binary = zipfile.ZipFile(io.BytesIO(data)).read("tofu")
    _as_user(row["uid"], ["mkdir", "-p", str(target.parent)])
    _as_user(row["uid"], ["/usr/bin/dd", f"of={target}", "status=none"], data=binary)
    _as_user(row["uid"], ["chmod", "700", str(target)])
    return {"installed": f"OpenTofu {TOFU_VERSION}", "path": "~/.local/bin/tofu"}


class FilePutArgs(Args):
    sid: str = SID
    path: str = Field(min_length=1, max_length=512)
    data: str = Field(max_length=FILE_MAX * 4 // 3 + 8)  # base64


@operation("workbench.file_put", FilePutArgs, needs=by_rule(_owner))
def file_put(args, ctx):
    """A file written into the home, by the session's own account."""
    import base64

    row = _session(args.sid)
    root, target = _home_path(row, args.path)
    content = base64.b64decode(args.data)
    if len(content) > FILE_MAX:
        raise DaemonError("refused", "At most 20 MB per file")
    _inside(row, root, target)
    _as_user(row["uid"], ["mkdir", "-p", str(target.parent)])
    _as_user(row["uid"], ["/usr/bin/dd", f"of={target}", "status=none"], data=content)
    return {"path": str(target.relative_to(root)), "size": len(content)}


class FileGetArgs(Args):
    sid: str = SID
    path: str = Field(min_length=1, max_length=512)


@operation("workbench.file_get", FileGetArgs, needs=by_rule(_owner))
def file_get(args, ctx):
    """A regular file of the home, base64, 20 MB at most."""
    import base64

    row = _session(args.sid)
    root, target = _home_path(row, args.path)
    _inside(row, root, target)
    size = _as_user(row["uid"], ["stat", "-L", "-c", "%F %s", str(target)]).decode().strip()
    kind, _, length = size.rpartition(" ")
    if kind != "regular file" and kind != "regular empty file":
        raise DaemonError("refused", "Not a regular file")
    if int(length) > FILE_MAX:
        raise DaemonError("refused", "At most 20 MB per file")
    data = _as_user(row["uid"], ["cat", str(target)])
    return {"name": target.name, "data": base64.b64encode(data).decode()}
