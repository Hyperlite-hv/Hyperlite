"""Hook scripts on a VM's lifecycle (docs/design/hook-scripts.md), as Proxmox VE's `hookscript`.

A script from the node's library (paths.SNIPPETS, filled by root only: the dashboard picks one, never uploads one) is
attached to a VM in its own metadata (METADATA_NS), so it follows the VM in a migration and a clone. It runs
with `<vm> <phase>`:
- pre-start, post-start and post-stop from libvirt's qemu hook (scripts/libvirt-hook-hookscript), which sees every
  start and stop, whoever asks (the API, the ordered boot, HA, replication, virsh); a failing pre-start refuses the
  start, its error becoming libvirt's;
- pre-stop from Hyperlite, before a shutdown, a stop or a forced restart it is asked for (libvirt has no operation
  before a stop); a failing pre-stop refuses the shutdown or the stop, except a forced stop.
Each run is kept on the node (the last RUNS_KEPT, in paths.HOOKSCRIPT_RUNS) and sent to the journal.
"""

import contextlib
import json
import os
import re
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from app.core import paths
from app.core.safe_paths import safe_child

METADATA_NS = "urn:hyperlite:hookscript:1"
METADATA_KEY = "hlhook"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
PHASES = ("pre-start", "post-start", "pre-stop", "post-stop")
RUN_TIMEOUT_S = 300
OUTPUT_KEPT = 16 * 1024
RUNS_KEPT = 50
SSH_TIMEOUT_S = 30
# The owner a usable script and its directory must have. Tests, run unprivileged in the CI, set their own.
OWNER_UID = 0
SYSLOG_TAG = "hyperlite-hookscript"


class HookError(ValueError):
    """A hook script refused, or a run that failed, with a message for the user."""


def snippets_dir():
    return Path(os.environ.get("HYPERLITE_SNIPPETS_DIR") or paths.SNIPPETS)


def runs_dir():
    return Path(os.environ.get("HYPERLITE_HOOKSCRIPT_RUNS_DIR") or paths.HOOKSCRIPT_RUNS)


def _vm_file(vm, suffix):
    # The VM's name names its files: one that could leave the directory is refused (Hyperlite's names never could).
    if not isinstance(vm, str) or not NAME_RE.fullmatch(vm):
        raise HookError(f"Invalid VM name: {vm!r}")
    try:
        return safe_child(runs_dir(), f"{vm}{suffix}")
    except ValueError as e:
        raise HookError(str(e)) from None


# --- The library ---


def _refusal(st, what):
    """Why a script or its directory cannot be run as root, or None. A file anyone else may change would let them
    run code as root."""
    if st.st_uid != OWNER_UID:
        return f"{what} is not owned by root"
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return f"{what} is writable by others than root"
    return None


def check(name):
    """The path of the usable script `name` in this node's library, or HookError saying why it is not."""
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise HookError("Invalid script name: letters, digits, '.', '_' and '-', at most 100 characters")
    base = snippets_dir()
    try:
        dir_st = os.lstat(base)
    except FileNotFoundError:
        raise HookError(f"No script library on this node ({base})") from None
    if not stat.S_ISDIR(dir_st.st_mode):
        raise HookError(f"The script library {base} is not a directory")
    reason = _refusal(dir_st, f"The library {base}")
    if reason:
        raise HookError(reason)
    try:
        path = safe_child(base, name)
    except ValueError:
        raise HookError(f"'{name}' is not a plain file (a link or a directory is refused)") from None
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise HookError(f"No script '{name}' in {base}") from None
    if not stat.S_ISREG(st.st_mode):
        raise HookError(f"'{name}' is not a plain file (a link or a directory is refused)")
    reason = _refusal(st, f"'{name}'")
    if reason:
        raise HookError(reason)
    if not st.st_mode & stat.S_IXUSR:
        raise HookError(f"'{name}' is not executable (chmod 755)")
    return path


def list_scripts():
    """This node's library: [{name, size, mtime, usable, reason}], by name."""
    base = snippets_dir()
    try:
        names = sorted(p.name for p in base.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return []
    scripts = []
    for name in names:
        entry = {"name": name, "size": None, "mtime": None, "usable": False, "reason": None}
        try:
            st = os.lstat(base / name)
            entry["size"], entry["mtime"] = st.st_size, int(st.st_mtime)
            check(name)
            entry["usable"] = True
        except HookError as e:
            entry["reason"] = str(e)
        except OSError as e:
            entry["reason"] = f"Unreadable: {e.strerror}"
        scripts.append(entry)
    return scripts


# --- The VM's script, in its metadata ---


def script_of_domain_xml(root):
    """The script attached to a VM, from its parsed domain XML, or None."""
    el = root.find(f"metadata/{{{METADATA_NS}}}hookscript")
    name = el.get("script") if el is not None else None
    return name if name and NAME_RE.fullmatch(name) else None


def get_script(domain):
    import libvirt

    return script_of_domain_xml(ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)))


def set_script(domain, name):
    """Attach `name` (checked by the caller on the VM's node) to the VM, or detach its script with None, in its
    definition and, running, in its live XML too (a migration takes the live one)."""
    import libvirt

    if name is not None and not NAME_RE.fullmatch(name):
        raise HookError("Invalid script name")
    flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
    if domain.isActive():
        flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
    domain.setMetadata(
        libvirt.VIR_DOMAIN_METADATA_ELEMENT,
        f"<hookscript script='{name}'/>" if name else None,
        METADATA_KEY if name else None,
        METADATA_NS,
        flags,
    )


# --- Running ---


def _record(vm, phase, code, output, seconds):
    run = {
        "at": int(time.time()),
        "phase": phase,
        "exit_code": code,
        "ok": code == 0,
        "seconds": round(seconds, 2),
        "output": output[-OUTPUT_KEPT:],
    }
    path = _vm_file(vm, ".jsonl")
    import syslog

    syslog.openlog(SYSLOG_TAG)
    # Keeping the run must not change its outcome: a full disk loses the record, the journal still has it.
    try:
        # Root's only: a script's output may hold what it should not show to others.
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            lines = path.read_text(errors="replace").splitlines()[-(RUNS_KEPT - 1) :]
        except FileNotFoundError:
            lines = []
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(f"{line}\n" for line in [*lines, json.dumps(run)]))
        tmp.replace(path)
    except OSError as e:
        syslog.syslog(syslog.LOG_ERR, f"{vm} {phase}: the run could not be kept in {path}: {e}")
    syslog.syslog(
        syslog.LOG_INFO if code == 0 else syslog.LOG_ERR,
        f"{vm} {phase}: exit {code} in {run['seconds']} s" + (f": {output.strip()[-500:]}" if output.strip() else ""),
    )
    return run


def run(vm, phase, script, domain_xml=""):
    """Run the VM's script for `phase` on this node, after checking it again, and record the run. Returns the run;
    a script that cannot run is a failed run (exit code None)."""
    _vm_file(vm, ".jsonl")
    start = time.monotonic()
    try:
        path = check(script)
    except HookError as e:
        return _record(vm, phase, None, str(e), 0)
    env = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "HOME": "/root",
        "HYPERLITE_VM": vm,
        "HYPERLITE_PHASE": phase,
    }
    # Files rather than pipes: a script that leaves a process behind (a post-start starting a daemon) would keep a
    # pipe open, and waiting for its end would hold the VM's start in libvirt for as long as that process lives. The
    # script runs in a session of its own, so a timeout stops it with everything it started.
    with tempfile.TemporaryFile() as stdin, tempfile.TemporaryFile() as out:
        stdin.write(domain_xml.encode())
        stdin.seek(0)
        try:
            proc = subprocess.Popen(
                [str(path), vm, phase],
                stdin=stdin,
                stdout=out,
                stderr=subprocess.STDOUT,
                env=env,
                cwd="/",
                start_new_session=True,
            )
        except OSError as e:
            return _record(vm, phase, None, f"{e.strerror} (is its first line a valid #! interpreter?)", 0)
        try:
            code, note = proc.wait(timeout=RUN_TIMEOUT_S), ""
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            code, note = None, f"\nStopped after {RUN_TIMEOUT_S} s"
        out.seek(max(0, os.fstat(out.fileno()).st_size - OUTPUT_KEPT))
        output = out.read().decode(errors="replace") + note
    return _record(vm, phase, code, output, time.monotonic() - start)


def failure_message(run_):
    """What a failed run says to the user."""
    output = run_["output"].strip()
    head = f"exit code {run_['exit_code']}" if run_["exit_code"] is not None else "not run"
    return f"{run_['phase']} hook script failed ({head})" + (f": {output[-500:]}" if output else "")


def runs(vm):
    """This node's recorded runs of the VM's script, the newest last."""
    try:
        lines = _vm_file(vm, ".jsonl").read_text(errors="replace").splitlines()
    except FileNotFoundError:
        return []
    found = []
    for line in lines:
        try:
            found.append(json.loads(line))
        except ValueError:
            continue
    return found


# --- On another node (over SSH, through the same module there) ---


def _on_node(node, args, stdin=""):
    from app.core.cluster import get_node, node_ssh_options

    target = get_node(node)
    if target is None:
        raise HookError(f"Node '{node}' not found")
    code = paths.INSTALLED_CODE
    remote = f"cd {shlex.quote(str(code))} && {shlex.quote(str(code / 'venv/bin/python3'))} -m app.core.hookscript"
    cmd = [
        "ssh",
        *node_ssh_options(),
        "-p",
        str(target["ssh_port"]),
        f"{target['ssh_user']}@{target['hostname']}",
        f"{remote} {shlex.join(args)}",
    ]
    timeout = RUN_TIMEOUT_S + SSH_TIMEOUT_S if args[:1] == ["run"] else SSH_TIMEOUT_S
    r = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise HookError(f"Node '{node}': {(r.stderr or r.stdout).strip()[-300:] or f'exit code {r.returncode}'}")
    return json.loads(r.stdout)


def list_scripts_on(node=None):
    return _on_node(node, ["list"]) if node else list_scripts()


def check_on(node, name):
    """HookError when `name` is not usable on the VM's node."""
    if not node:
        check(name)
        return
    result = _on_node(node, ["check", name])
    if result.get("error"):
        raise HookError(result["error"])


def runs_on(node, vm):
    return _on_node(node, ["runs", vm]) if node else runs(vm)


def pre_stop(domain, node=None, task_id=None):
    """Run the VM's pre-stop on its node when it has a script. Returns the run (None without a script); its output
    goes to the task log."""
    xml = domain.XMLDesc(0)
    script = script_of_domain_xml(ET.fromstring(xml))
    if not script:
        return None
    if node:
        run_ = _on_node(node, ["run", domain.name(), "pre-stop", script], stdin=xml)
    else:
        run_ = run(domain.name(), "pre-stop", script, xml)
    log_run(task_id, run_)
    return run_


def log_run(task_id, run_):
    from app.core.tasks import task_log

    if run_["ok"]:
        state = "ok"
    elif run_["exit_code"] is None:
        state = "not run"
    else:
        state = f"failed, exit code {run_['exit_code']}"
    task_log(task_id, f"Hook script, {run_['phase']}: {state} ({run_['seconds']} s)")
    if run_["output"].strip():
        task_log(task_id, run_["output"].strip()[-2000:])


def log_runs_since(task_id, vm, since, node=None):
    """Write into the task log the runs of the VM's script recorded since `since` (a start's pre-start and
    post-start, run by libvirt's hook)."""
    try:
        recent = [r for r in runs_on(node, vm) if r.get("at", 0) >= int(since)]
    except (HookError, OSError, ValueError, subprocess.SubprocessError):
        return
    for r in recent:
        log_run(task_id, r)


def start_error(message):
    """libvirt's error for a start a hook refused carries the hook's command line and environment before the
    script's own error: only that error is kept. Any other message is returned as it is."""
    marker = re.search(r"Hook script execution failed:.*?unexpected exit status \d+: (.*)", message, re.S)
    return marker.group(1).strip() if marker else message


# --- libvirt's qemu hook ---

_HOOK_PHASES = {("prepare", "begin"): "pre-start", ("started", "begin"): "post-start", ("release", "end"): "post-stop"}


def hook(argv, stdin):
    """libvirt's qemu hook: `<vm> <operation> <sub-operation> -`, the domain XML on stdin. It must not call libvirt.
    A non-zero exit at `prepare` refuses the start, libvirt then showing what this printed on stderr."""
    if len(argv) < 3:
        return 0
    vm, phase = argv[0], _HOOK_PHASES.get((argv[1], argv[2]))
    if phase is None or not NAME_RE.fullmatch(vm):
        return 0
    try:
        return _hook(vm, phase, stdin.read())
    except Exception as e:
        # Whatever went wrong here: a pre-start that could not be run refuses the start (the script may be the check
        # the start depends on), any other phase never fails the VM. The reason goes to the journal.
        import syslog

        syslog.openlog(SYSLOG_TAG)
        syslog.syslog(syslog.LOG_ERR, f"{vm} {phase}: {e!r}")
        if phase == "pre-start":
            print(f"pre-start hook script: {e}", file=sys.stderr)
            return 1
        return 0


def _hook(vm, phase, xml):
    try:
        script = script_of_domain_xml(ET.fromstring(xml))
    except ET.ParseError:
        script = None
    # libvirt also releases a VM whose start the pre-start refused: that VM gets no post-stop, as in Proxmox VE.
    refused = _vm_file(vm, ".refused")
    if phase == "post-stop" and refused.exists():
        refused.unlink(missing_ok=True)
        return 0
    if not script:
        return 0
    run_ = run(vm, phase, script, xml)
    if phase == "pre-start" and not run_["ok"]:
        refused.parent.mkdir(parents=True, exist_ok=True)
        refused.touch()
        print(failure_message(run_), file=sys.stderr)
        return 1
    if phase == "pre-start":
        refused.unlink(missing_ok=True)
    return 0


def _main(argv):
    command, args = (argv[0], argv[1:]) if argv else ("", [])
    if command == "hook":
        return hook(args, sys.stdin)
    if command == "list":
        print(json.dumps(list_scripts()))
    elif command == "check" and len(args) == 1:
        try:
            check(args[0])
            print(json.dumps({"error": None}))
        except HookError as e:
            print(json.dumps({"error": str(e)}))
    elif command == "runs" and len(args) == 1:
        print(json.dumps(runs(args[0])))
    elif command == "run" and len(args) == 3 and args[1] in PHASES:
        print(json.dumps(run(args[0], args[1], args[2], sys.stdin.read())))
    else:
        print("usage: python -m app.core.hookscript hook|list|check NAME|runs VM|run VM PHASE SCRIPT", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
