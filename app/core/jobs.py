"""Job engine for the Automation tab: a simplified equivalent of a mini
Ansible / Datto RMM, using vRealize Orchestrator and vCenter Scheduled Tasks as
the reference for expected features (a sequence of steps, a success condition
per step, full history, dry-run).

Architecture choice: IN-PROCESS ASYNCIO/THREADING, NOT Celery/RQ.
  - Celery/RQ is the "standard" choice for a real distributed job queue, but
    it assumes a broker (Redis/RabbitMQ) on top of the existing stack and
    separate workers to supervise, and it solves a problem (scaling execution
    across several machines) that this project does not have. Adding Celery
    here would be over-engineering for the current size of the project.
  - A daemon thread in the process (the same pattern already used for
    snapshots, cloning and backups): zero extra dependency, consistent with the
    rest of the code, and more than enough for the expected job volume (a few
    parallel runs at most). Known limitation: restarting the service while a
    job runs kills it without resuming, which is acceptable for jobs lasting a
    few minutes and worth revisiting if multi-hour jobs ever appear.

SSH execution on a target VM: `ssh` with the automation key, one command per
step, run by hyperlite-daemon (job.step), which alone reads that key.

Security: creating and editing jobs is restricted to admins (see
app/routers/jobs.py). A job command is arbitrary code BY DESIGN (this is a shell
command executor, like Ansible or any RMM tool), not user input to sanitize: the
real security perimeter is "who may create and run a job", not "preventing
injection into the command". Real sandboxing (a container or gVisor per run)
would be disproportionate for the size of the project. The mitigations chosen
instead are admin-only access, a full audit of every executed command
(job_run_logs + audit_log), and a dry-run mode to check a sequence before
running it for real. Known and documented limitation: the automation user on
the VM side has passwordless sudo (the same key as the web SSH terminal), so a
job command on a VM is effectively root-equivalent on that VM, as the web SSH
terminal already is.

"""

import json
import logging
import threading
import uuid
from datetime import UTC, datetime

import libvirt

from app.core import privileged
from app.core.audit import log_action
from app.core.libvirt_utils import open_conn
from app.core.tasks import create_task, finish_task, raise_if_cancelled, register_cancel, task_log, update_task_progress
from app.daemon.registry import DaemonError

logger = logging.getLogger(__name__)

STEP_TIMEOUT_S = 120


def _now():
    return datetime.now(UTC).isoformat()


# The task of the run executing in this thread, and who started it (see _run_steps).
_current = threading.local()
# What the API waits for a step's answer: the daemon's own limit (app/daemon/ops/job.py), and a margin.
STEP_ANSWER_S = STEP_TIMEOUT_S + 30


def _run_command(target_type, target, command, dry_run):
    """Run a shell command on the local host, or over SSH on a VM: hyperlite-daemon runs it as root, with the
    automation key for a VM (job.step). Returns (stdout, stderr, exit_code); raises RuntimeError when the step could
    not run (a VM stopped, without an address or an account). In dry-run mode nothing is executed: it just returns a
    fake exit_code 0 with an explicit message."""
    if dry_run:
        return f"[dry-run] command not executed: {command}", "", 0
    task_id = getattr(_current, "task_id", None) or "-"
    try:
        result = privileged.run(
            "job.step",
            user=getattr(_current, "username", None),
            timeout=STEP_ANSWER_S,
            task_id=task_id,
            target_type=target_type,
            target=None if target_type == "host" else target,
            command=command,
        )
    except DaemonError as e:
        raise RuntimeError(e.detail) from None
    return result["stdout"], result["stderr"], result["exit"]


def _cancel_step(task_id):
    """Cancelling a run stops the step in progress, in the daemon (job.cancel)."""
    try:
        privileged.run("job.cancel", task_id=task_id)
    except DaemonError as e:
        logger.warning("The step of %s was not stopped: %s", task_id, e.detail)


def _step_succeeded(condition_type, condition_value, stdout, exit_code):
    if condition_type == "exit_code":
        expected = int(condition_value) if condition_value not in (None, "") else 0
        return exit_code == expected
    if condition_type == "stdout_contains":
        return (condition_value or "") in stdout
    return exit_code == 0


def _store():
    # The automation repository's synchronous bridge: runs execute in threads.
    from app.repositories import registry

    return registry.automation().sync


def _log_step(run_id, step_position, target, command, stdout, stderr, exit_code, succeeded):
    _store().log_step(
        run_id, step_position, target, command, stdout[-8000:], stderr[-4000:], exit_code, succeeded, _now()
    )


def _expand_steps(steps, targets):
    """A step of type 'each_target' is duplicated once per target supplied to
    the run (e.g. the "load balancing" job applies the same step to every
    backend VM); the other step types ('vm'/'host' explicit in the job
    definition) are left unchanged."""
    expanded = []
    for step in steps:
        if step["target_type"] == "each_target":
            for t in targets:
                expanded.append({**step, "target_type": "vm", "target": t})
        else:
            expanded.append(step)
    return expanded


class JobRunRefused(ValueError):
    """A run request that cannot start (targets that do not fit the job). Raised
    before anything is recorded, so the caller answers with an error instead of
    announcing a background run that dies where nobody sees it."""


def _local_node():
    """The node a run is recorded under: this one (app/core/self_node.py), even when libvirt does not answer."""
    from app.core import self_node

    return self_node.name()


def _check_targets(job, steps, targets):
    if any(not t.strip() for t in targets):
        raise JobRunRefused("Target VM names must not be empty")
    if len(set(targets)) != len(targets):
        raise JobRunRefused("A target VM is listed more than once")
    if job["predefined_key"] == LB_PREDEFINED_KEY:
        # The steps of this job are generated from the targets at run time, so the
        # stored steps cannot tell whether targets are needed: check it here.
        if len(targets) < 2:
            raise JobRunRefused("At least 2 target VMs are needed (1 balancer + 1 backend minimum)")
        return
    if not steps:
        raise JobRunRefused("This job has no step to run")
    if not targets and any(s["target_type"] == "each_target" for s in steps):
        # Without targets those steps expand to nothing: the run would report a
        # success for work it silently skipped.
        raise JobRunRefused("This job runs steps on each target VM: choose at least one target")


def start_job_run(job_id, targets=None, dry_run=False, username="system"):
    """Validate a run request, record the run and its task, then execute it in a
    daemon thread. Returns the run id.

    Raises LookupError for an unknown job and JobRunRefused for a request that
    cannot run; both happen before anything is recorded, so a refused request is
    never reported (nor audited) as started. Once this returns, the run exists and
    always ends in 'success' or 'failed', whatever happens in the thread."""
    targets = list(targets or [])
    job = _store().get_job(job_id)
    if not job:
        raise LookupError("Job not found")
    steps = job.pop("steps")
    _check_targets(job, steps, targets)

    if job["predefined_key"] == LB_PREDEFINED_KEY:

        def build_steps():
            return _lb_steps(targets, dry_run)

    else:

        def build_steps():
            return _expand_steps(steps, targets)

    run_id = str(uuid.uuid4())
    task_id = create_task("run_job", job["name"], node=_local_node(), username=username)
    try:
        _store().create_run(run_id, job_id, task_id, dry_run, json.dumps(targets), _now())
    except Exception as e:
        finish_task(task_id, "failed", f"Could not record the run: {e}")
        raise

    threading.Thread(
        target=_execute_run,
        args=(run_id, task_id, job["name"], build_steps, dry_run, username),
        daemon=True,
    ).start()
    return run_id


def _run_steps(run_id, task_id, steps, dry_run):
    """Run the steps in order and log each one. Returns True when all succeeded."""
    total = max(len(steps), 1)
    _current.task_id = task_id
    register_cancel(task_id, lambda: _cancel_step(task_id))
    for i, step in enumerate(steps):
        # Cancelling stops the command in progress (see _run) and the steps after it.
        raise_if_cancelled(task_id)
        target = "the host" if step["target_type"] == "host" else step["target"]
        task_log(task_id, f"Step {i + 1}/{total} on {target}: {step['command'][:200]}")
        try:
            stdout, stderr, exit_code = _run_command(step["target_type"], step["target"], step["command"], dry_run)
            succeeded = _step_succeeded(step["condition_type"], step["condition_value"], stdout, exit_code)
        except RuntimeError as e:
            stdout, stderr, exit_code, succeeded = "", str(e), -1, False

        _log_step(run_id, step["position"], target, step["command"], stdout, stderr, exit_code, succeeded)
        update_task_progress(task_id, int((i + 1) / total * 100))

        if not succeeded:
            return False  # stop at the first failed step: no "best effort", a job is a sequence
    return True


def _close_run(run_id, task_id, job_name, username, ok, error=None):
    if ok:
        result = "SUCCESS"
    elif error:
        result = f"ERROR: {error}"
    else:
        result = "FAILED"
    if ok:
        finish_task(task_id, "succeeded")
    else:
        finish_task(task_id, "failed", f"Job '{job_name}': {result}")
    log_action(username, "run_job", job_name, "success" if ok else "failed", result)
    # The run row is what the dashboard polls: written last, a run shown as finished never has its task still
    # running nor its outcome missing from the audit log.
    _store().close_run(run_id, "success" if ok else "failed", _now(), result)


def _execute_run(run_id, task_id, job_name, build_steps, dry_run, username):
    """Body of the run thread. An exception raised in a daemon thread is only
    printed on stderr: without this catch-all the run would stay 'running'
    forever and nobody would learn why it stopped."""
    _current.username = username  # named in the daemon's log for each step
    try:
        ok = _run_steps(run_id, task_id, build_steps(), dry_run)
        error = None
    except Exception as e:
        logger.exception("Job run %s (%s) aborted", run_id, job_name)
        ok, error = False, str(e) or type(e).__name__
    try:
        _close_run(run_id, task_id, job_name, username, ok, error)
    except Exception:
        logger.exception("Could not record the end of job run %s (%s)", run_id, job_name)


# --- Predefined job "Deploy a load balancer" (a concrete example) ---
# Its sequence of steps is built dynamically from the target VMs supplied at RUN
# time (not from static job_steps): the first target VM becomes the HAProxy node
# and the following ones the Nginx backends.

LB_PREDEFINED_KEY = "deploy_load_balancing"


LB_JOB_NAME = "Deploy a load balancer"
LB_JOB_DESCRIPTION = (
    "Installs HAProxy on the first target VM and Nginx on the following ones (backends), then tests connectivity."
)


def ensure_lb_job_exists():
    """Create the predefined job once (idempotent). Called at application start,
    like ensure_isolated_network for the network."""
    # Installations created before the English translation still hold the legacy (French) name and description:
    # they are brought up to date. The job is found by its predefined key, never by name, so renaming is safe.
    return _store().ensure_predefined(LB_PREDEFINED_KEY, LB_JOB_NAME, LB_JOB_DESCRIPTION, _now())


def _lb_steps(targets, dry_run):
    """Steps of the predefined job, generated from the targets of the run (no
    stored job_steps: unlike a custom job, its sequence depends on the targets).
    The first target becomes the HAProxy node, the following ones the Nginx
    backends. The caller has already checked that there are at least 2 targets."""
    lb_vm, backends = targets[0], targets[1:]

    backend_lines = "\n".join(f"    server backend{i + 1} __IP_{b}__:80 check" for i, b in enumerate(backends))
    haproxy_cfg = (
        "frontend http_front\n    bind *:80\n    default_backend http_back\n"
        "backend http_back\n    balance roundrobin\n" + backend_lines
    )

    steps = [
        {
            "position": 0,
            "target_type": "vm",
            "target": lb_vm,
            "command": "sudo apt-get update -qq && sudo apt-get install -y -qq haproxy",
            "condition_type": "exit_code",
            "condition_value": "0",
        },
    ]
    for b in backends:
        steps.append(
            {
                "position": len(steps),
                "target_type": "vm",
                "target": b,
                "command": "sudo apt-get update -qq && sudo apt-get install -y -qq nginx && echo OK_$(hostname) | sudo tee /var/www/html/index.html",
                "condition_type": "exit_code",
                "condition_value": "0",
            }
        )

    # Backend IPs are resolved here, specific to this job, rather than in the
    # generic _run_command. Every backend is reached over SSH by its IP for the
    # install steps anyway, so a backend without a known IP could not run them.
    from app.routers.vms._shared import _get_ip

    if dry_run:
        ip_map = dict.fromkeys(backends, "0.0.0.0")  # noqa: S104 -- placeholder address, not a bind
    else:
        ip_map = {}
        conn = open_conn()
        try:
            for b in backends:
                try:
                    domain = conn.lookupByName(b)
                except libvirt.libvirtError:
                    raise RuntimeError(f"VM '{b}' not found") from None
                ip = _get_ip(domain)
                if not ip:
                    # A 0.0.0.0 placeholder would deploy a balancer that forwards nowhere.
                    raise RuntimeError(f"IP address of '{b}' unknown (is the VM running?)")
                ip_map[b] = ip
        finally:
            conn.close()

    cfg = haproxy_cfg
    for b, ip in ip_map.items():
        cfg = cfg.replace(f"__IP_{b}__", ip)
    steps.append(
        {
            "position": len(steps),
            "target_type": "vm",
            "target": lb_vm,
            "command": f"echo '{cfg}' | sudo tee /etc/haproxy/haproxy.cfg > /dev/null && sudo systemctl restart haproxy",
            "condition_type": "exit_code",
            "condition_value": "0",
        }
    )
    steps.append(
        {
            "position": len(steps),
            "target_type": "vm",
            "target": lb_vm,
            "command": "curl -sf -o /dev/null -w '%{http_code}' http://localhost:80/ | grep -q 200",
            "condition_type": "exit_code",
            "condition_value": "0",
        }
    )
    return steps
