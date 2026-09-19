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

SSH execution on a target VM: the same pattern as get_vm_provisioning, `ssh`
in a subprocess with the automation key, rather than reimplementing a
persistent asyncssh session (one command per step, not an interactive shell).

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
import subprocess
import threading
from datetime import UTC, datetime

import libvirt

from app.core.audit import log_action
from app.core.database import get_conn
from app.core.libvirt_utils import open_conn
from app.core.tasks import create_task, finish_task, update_task_progress
from app.core.vm_meta import get_vm_ssh_user

STEP_TIMEOUT_S = 120


def _now():
    return datetime.now(UTC).isoformat()


def _resolve_vm_ssh(vm_name):
    """(ip, username, key_path) for a VM. Raises RuntimeError if the VM is not
    running, has no known IP, or has no registered SSH user (the same guard as
    create_terminal_ticket)."""
    from app.core.vm_builder import get_automation_private_key_path
    from app.routers.vms._shared import (
        _get_ip,  # late import: avoids a routers<->core cycle, the same pattern as backups.py
    )

    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(vm_name)
        except libvirt.libvirtError:
            raise RuntimeError(f"VM '{vm_name}' not found") from None
        if not domain.isActive():
            raise RuntimeError(f"VM '{vm_name}' is stopped")
        ip = _get_ip(domain)
        if not ip:
            raise RuntimeError(f"Adresse IP de '{vm_name}' inconnue")
        username = get_vm_ssh_user(vm_name)
        if not username:
            raise RuntimeError(f"No known SSH user for '{vm_name}'")
        return ip, username, get_automation_private_key_path()
    finally:
        conn.close()


def _run_command(cible_type, cible, commande, dry_run):
    """Run a shell command on the local host, or over SSH on a VM. Returns
    (stdout, stderr, exit_code). In dry-run mode nothing is executed: it just
    returns a fake exit_code 0 with an explicit message."""
    if dry_run:
        return f"[dry-run] command not executed: {commande}", "", 0

    if cible_type == "host":
        try:
            r = subprocess.run(["bash", "-c", commande], capture_output=True, text=True, timeout=STEP_TIMEOUT_S)
            return r.stdout, r.stderr, r.returncode
        except subprocess.TimeoutExpired:
            return "", f"Timed out after {STEP_TIMEOUT_S}s", 124

    ip, username, key_path = _resolve_vm_ssh(cible)
    try:
        r = subprocess.run(
            [
                "ssh",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                "-i",
                str(key_path),
                f"{username}@{ip}",
                commande,
            ],
            capture_output=True,
            text=True,
            timeout=STEP_TIMEOUT_S,
        )
        return r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        return "", f"Timed out after {STEP_TIMEOUT_S}s", 124


def _step_succeeded(condition_type, condition_valeur, stdout, exit_code):
    if condition_type == "exit_code":
        expected = int(condition_valeur) if condition_valeur not in (None, "") else 0
        return exit_code == expected
    if condition_type == "stdout_contains":
        return (condition_valeur or "") in stdout
    return exit_code == 0


def _log_step(run_id, step_ordre, cible, commande, stdout, stderr, exit_code, reussi):
    with get_conn() as db:
        db.execute(
            "INSERT INTO job_run_logs (run_id, step_ordre, cible, commande, stdout, stderr, exit_code, reussi, horodatage) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, step_ordre, cible, commande, stdout[-8000:], stderr[-4000:], exit_code, int(reussi), _now()),
        )
        db.commit()


def _expand_steps(steps, targets):
    """A step of type 'chaque_cible' is duplicated once per target supplied to
    the run (e.g. the "load balancing" job applies the same step to every
    backend VM); the other step types ('vm'/'host' explicit in the job
    definition) are left unchanged."""
    expanded = []
    for step in steps:
        if step["cible_type"] == "chaque_cible":
            for t in targets:
                expanded.append({**step, "cible_type": "vm", "cible": t})
        else:
            expanded.append(step)
    return expanded


def run_job(job_id, targets=None, dry_run=False, username="system"):
    with get_conn() as db:
        job = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            raise RuntimeError("Job not found")
        steps = db.execute("SELECT * FROM job_steps WHERE job_id = ? ORDER BY ordre", (job_id,)).fetchall()
    steps = [dict(s) for s in steps]
    targets = targets or []

    import uuid

    run_id = str(uuid.uuid4())
    conn = open_conn()
    node = conn.getHostname()
    conn.close()
    task_id = create_task("run_job", job["name"], node=node, username=username)

    with get_conn() as db:
        db.execute(
            "INSERT INTO job_runs (id, job_id, task_id, dry_run, targets, statut, started_at) VALUES (?, ?, ?, ?, ?, 'en_cours', ?)",
            (run_id, job_id, task_id, int(dry_run), json.dumps(targets), _now()),
        )
        db.commit()

    expanded = _expand_steps(steps, targets)
    total = max(len(expanded), 1)
    overall_ok = True

    for i, step in enumerate(expanded):
        cible = "the host" if step["cible_type"] == "host" else step["cible"]
        try:
            stdout, stderr, exit_code = _run_command(step["cible_type"], step["cible"], step["commande"], dry_run)
            reussi = _step_succeeded(step["condition_type"], step["condition_valeur"], stdout, exit_code)
        except RuntimeError as e:
            stdout, stderr, exit_code, reussi = "", str(e), -1, False

        _log_step(run_id, step["ordre"], cible, step["commande"], stdout, stderr, exit_code, reussi)
        update_task_progress(task_id, int((i + 1) / total * 100))

        if not reussi:
            overall_ok = False
            break  # stop at the first failed step: no "best effort", a job is a sequence

    resultat = "SUCCESS" if overall_ok else "FAILED"
    statut = "succes" if overall_ok else "echec"
    with get_conn() as db:
        db.execute(
            "UPDATE job_runs SET statut = ?, finished_at = ?, resultat = ? WHERE id = ?",
            (statut, _now(), resultat, run_id),
        )
        db.commit()

    if overall_ok:
        finish_task(task_id, "termine")
    else:
        finish_task(task_id, "echec", f"Job '{job['name']}' : {resultat}")
    log_action(username, "run_job", job["name"], "succes" if overall_ok else "echec", resultat)
    return run_id


def run_job_async(job_id, targets=None, dry_run=False, username="system"):
    threading.Thread(target=run_job, args=(job_id, targets, dry_run, username), daemon=True).start()


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
    with get_conn() as db:
        existing = db.execute("SELECT id FROM jobs WHERE predefined_key = ?", (LB_PREDEFINED_KEY,)).fetchone()
        if existing:
            # Installations created before the English translation still hold the legacy
            # (French) name and description: bring them up to date. The job is found by
            # its predefined key, never by name, so renaming is safe.
            db.execute(
                "UPDATE jobs SET name = ?, description = ? WHERE id = ?",
                (LB_JOB_NAME, LB_JOB_DESCRIPTION, existing["id"]),
            )
            db.commit()
            return existing["id"]
        cur = db.execute(
            "INSERT INTO jobs (name, description, predefined_key, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (LB_JOB_NAME, LB_JOB_DESCRIPTION, LB_PREDEFINED_KEY, "system", _now()),
        )
        db.commit()
        return cur.lastrowid


def run_lb_job(job_id, targets, dry_run=False, username="system"):
    """Dynamically generated sequence (no stored job_steps for this predefined
    job: its steps depend on the targets chosen at each run, unlike a custom
    job whose sequence is fixed)."""
    if len(targets) < 2:
        raise RuntimeError("At least 2 target VMs are needed (1 balancer + 1 backend minimum)")
    lb_vm, backends = targets[0], targets[1:]

    backend_lines = "\n".join(f"    server backend{i + 1} __IP_{b}__:80 check" for i, b in enumerate(backends))
    haproxy_cfg = (
        "frontend http_front\n    bind *:80\n    default_backend http_back\n"
        "backend http_back\n    balance roundrobin\n" + backend_lines
    )

    steps = [
        {
            "ordre": 0,
            "cible_type": "vm",
            "cible": lb_vm,
            "commande": "sudo apt-get update -qq && sudo apt-get install -y -qq haproxy",
            "condition_type": "exit_code",
            "condition_valeur": "0",
        },
    ]
    for _i, b in enumerate(backends):
        steps.append(
            {
                "ordre": len(steps),
                "cible_type": "vm",
                "cible": b,
                "commande": "sudo apt-get update -qq && sudo apt-get install -y -qq nginx && echo OK_$(hostname) | sudo tee /var/www/html/index.html",
                "condition_type": "exit_code",
                "condition_valeur": "0",
            }
        )

    # Resolve the backend IPs to build the real HAProxy configuration: this must
    # happen AFTER the installation (the VMs must already have an address) but BEFORE
    # pushing the configuration to the LB node.
    resolved_cfg_step_index = len(steps)
    steps.append(
        {
            "ordre": resolved_cfg_step_index,
            "cible_type": "vm",
            "cible": lb_vm,
            "commande": "__PLACEHOLDER_HAPROXY_CONFIG__",
            "condition_type": "exit_code",
            "condition_valeur": "0",
        }
    )
    steps.append(
        {
            "ordre": len(steps),
            "cible_type": "vm",
            "cible": lb_vm,
            "commande": "curl -sf -o /dev/null -w '%{http_code}' http://localhost:80/ | grep -q 200",
            "condition_type": "exit_code",
            "condition_valeur": "0",
        }
    )

    # Replace the placeholder with the real command once the IPs are known (it needs
    # to resolve each backend; done here rather than in the generic _run_command
    # because it is specific to this job).
    from app.routers.vms._shared import _get_ip

    ip_map = {}
    if not dry_run:
        conn = open_conn()
        try:
            for b in backends:
                domain = conn.lookupByName(b)
                ip_map[b] = _get_ip(domain) or "0.0.0.0"  # noqa: S104 -- placeholder address in the generated config, not a bind
        finally:
            conn.close()
    else:
        ip_map = dict.fromkeys(backends, "0.0.0.0")  # noqa: S104 -- placeholder address, not a bind

    cfg = haproxy_cfg
    for b, ip in ip_map.items():
        cfg = cfg.replace(f"__IP_{b}__", ip)
    steps[resolved_cfg_step_index]["commande"] = (
        f"echo '{cfg}' | sudo tee /etc/haproxy/haproxy.cfg > /dev/null && sudo systemctl restart haproxy"
    )

    import uuid

    run_id = str(uuid.uuid4())
    conn = open_conn()
    node = conn.getHostname()
    conn.close()
    task_id = create_task("run_job", LB_JOB_NAME, node=node, username=username)
    with get_conn() as db:
        db.execute(
            "INSERT INTO job_runs (id, job_id, task_id, dry_run, targets, statut, started_at) VALUES (?, ?, ?, ?, ?, 'en_cours', ?)",
            (run_id, job_id, task_id, int(dry_run), json.dumps(targets), _now()),
        )
        db.commit()

    overall_ok = True
    for i, step in enumerate(steps):
        stdout, stderr, exit_code = _run_command(step["cible_type"], step["cible"], step["commande"], dry_run)
        reussi = _step_succeeded(step["condition_type"], step["condition_valeur"], stdout, exit_code)
        _log_step(run_id, step["ordre"], step["cible"], step["commande"], stdout, stderr, exit_code, reussi)
        update_task_progress(task_id, int((i + 1) / len(steps) * 100))
        if not reussi:
            overall_ok = False
            break

    resultat = "SUCCESS" if overall_ok else "FAILED"
    with get_conn() as db:
        db.execute(
            "UPDATE job_runs SET statut = ?, finished_at = ?, resultat = ? WHERE id = ?",
            ("succes" if overall_ok else "echec", _now(), resultat, run_id),
        )
        db.commit()
    finish_task(task_id, "termine" if overall_ok else "echec", None if overall_ok else resultat)
    log_action(username, "run_job", LB_JOB_NAME, "succes" if overall_ok else "echec", resultat)
    return run_id


def run_lb_job_async(job_id, targets, dry_run=False, username="system"):
    threading.Thread(target=run_lb_job, args=(job_id, targets, dry_run, username), daemon=True).start()
