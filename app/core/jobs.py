"""Moteur de Jobs -- onglet Automation (chantier 14 de la roadmap
vSphere/vCenter, 2026-09-13). Equivalent simplifie d'un mini Ansible/Datto
RMM, avec en tete de repere vRealize Orchestrator / vCenter Scheduled Tasks
cote fonctionnalites attendues (sequence d'etapes, condition de succes par
etape, historique complet, dry-run).

Choix d'architecture : ASYNCIO/THREADING EN PROCESS, PAS Celery/RQ.
  - Celery/RQ : le choix "standard" pour une vraie file de jobs distribuee --
    mais suppose un broker (Redis/RabbitMQ) en plus de la stack existante,
    des workers separes a superviser, et resout un probleme (scaler
    l'execution sur plusieurs machines) que ce projet n'a pas : Hyperlite
    est mono-hote aujourd'hui (le multi-noeuds, chantier 15, n'est pas
    encore la). Ajouter Celery ici serait de la sur-ingenierie pour la
    taille actuelle du projet.
  - Thread daemon en process (meme pattern deja utilise pour les snapshots
    chantier 4, le clonage chantier 5, les backups chantier 13) : zero
    dependance supplementaire, coherent avec le reste du code, largement
    suffisant pour le volume de jobs attendu ici (quelques executions
    paralleles au plus). Limite assumee : un redemarrage du service pendant
    qu'un job tourne le tue sans reprise -- acceptable pour des jobs de
    quelques minutes, a revisiter si des jobs de plusieurs heures
    apparaissent un jour.

Execution SSH sur une VM cible : meme pattern que get_vm_provisioning
(chantier 12) -- `ssh` en sous-processus avec la cle d'automatisation,
plutot que reimplementer une session persistante asyncssh (une commande par
etape, pas un shell interactif).

Securite (reflexion demandee) : la creation/edition de jobs est reservee aux
admins (voir app/routers/jobs.py) -- une commande de job est du code
arbitraire PAR CONCEPTION (c'est un executeur de commandes shell, comme
Ansible ou n'importe quel outil RMM), pas une entree utilisateur a
assainir : le vrai perimetre de securite est "qui a le droit de creer/
executer un job", pas "empecher l'injection dans la commande". Un vrai
sandboxing (conteneur/gVisor par execution) serait disproportionne pour la
taille du projet -- attenuations retenues a la place : admin-only,
audit complet de chaque commande executee (job_run_logs + audit_log),
mode dry-run pour verifier une sequence avant de l'executer pour de vrai.
Limite connue et documentee : l'utilisateur d'automatisation cote VM a un
sudo sans mot de passe (meme cle que le terminal SSH web, chantier existant
avant ce chantier) -- une commande de job sur une VM est donc de facto
root-equivalent sur cette VM, comme le terminal SSH web l'est deja.
"""
import json
import subprocess
import threading
from datetime import datetime, timezone

import libvirt

from app.core.audit import log_action
from app.core.database import get_conn
from app.core.libvirt_utils import open_conn
from app.core.tasks import create_task, finish_task, update_task_progress
from app.core.vm_meta import get_vm_ssh_user

STEP_TIMEOUT_S = 120


def _now():
    return datetime.now(timezone.utc).isoformat()


def _resolve_vm_ssh(vm_name):
    """(ip, username, key_path) pour une VM -- leve RuntimeError si la VM
    n'est pas active, n'a pas d'IP connue, ou n'a pas d'utilisateur SSH
    enregistre (meme garde-fou que create_terminal_ticket)."""
    from app.core.vm_builder import get_automation_private_key_path
    from app.routers.vms import _get_ip  # import tardif : evite un cycle routers<->core, meme pattern que backups.py

    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(vm_name)
        except libvirt.libvirtError:
            raise RuntimeError(f"VM '{vm_name}' introuvable")
        if not domain.isActive():
            raise RuntimeError(f"VM '{vm_name}' arrêtée")
        ip = _get_ip(domain)
        if not ip:
            raise RuntimeError(f"Adresse IP de '{vm_name}' inconnue")
        username = get_vm_ssh_user(vm_name)
        if not username:
            raise RuntimeError(f"Aucun utilisateur SSH connu pour '{vm_name}'")
        return ip, username, get_automation_private_key_path()
    finally:
        conn.close()


def _run_command(cible_type, cible, commande, dry_run):
    """Execute une commande shell sur l'hote local ou via SSH sur une VM.
    Renvoie (stdout, stderr, exit_code). En dry-run, n'execute rien -- se
    contente de renvoyer un exit_code 0 factice avec un message explicite."""
    if dry_run:
        return f"[dry-run] commande non exécutée : {commande}", "", 0

    if cible_type == "host":
        try:
            r = subprocess.run(["bash", "-c", commande], capture_output=True, text=True, timeout=STEP_TIMEOUT_S)
            return r.stdout, r.stderr, r.returncode
        except subprocess.TimeoutExpired:
            return "", f"Timeout après {STEP_TIMEOUT_S}s", 124

    ip, username, key_path = _resolve_vm_ssh(cible)
    try:
        r = subprocess.run(
            [
                "ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                "-i", str(key_path), f"{username}@{ip}", commande,
            ],
            capture_output=True, text=True, timeout=STEP_TIMEOUT_S,
        )
        return r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        return "", f"Timeout après {STEP_TIMEOUT_S}s", 124


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
    """Un step de type 'chaque_cible' est duplique une fois par cible fournie
    au run (ex. le job "load balancing" applique la meme etape a chaque VM
    backend) -- les autres types de step ('vm'/'host' explicites dans la
    definition du job) restent inchanges."""
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
            raise RuntimeError("Job introuvable")
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
        cible = "l'hôte" if step["cible_type"] == "host" else step["cible"]
        try:
            stdout, stderr, exit_code = _run_command(step["cible_type"], step["cible"], step["commande"], dry_run)
            reussi = _step_succeeded(step["condition_type"], step["condition_valeur"], stdout, exit_code)
        except RuntimeError as e:
            stdout, stderr, exit_code, reussi = "", str(e), -1, False

        _log_step(run_id, step["ordre"], cible, step["commande"], stdout, stderr, exit_code, reussi)
        update_task_progress(task_id, int((i + 1) / total * 100))

        if not reussi:
            overall_ok = False
            break  # arret a la premiere etape en echec -- pas de "au mieux", un job est une sequence

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


# --- Job predefini "Deployer un load balancing" (exemple concret demande) ---
# Construit dynamiquement sa sequence d'etapes a partir des VM cibles fournies
# au moment du RUN (pas des job_steps statiques) : la premiere VM cible
# devient le noeud HAProxy, les suivantes les backends Nginx.

LB_PREDEFINED_KEY = "deploy_load_balancing"


def ensure_lb_job_exists():
    """Cree le job predefini une seule fois (idempotent) -- appele au
    demarrage de l'app, comme ensure_isolated_network pour le reseau."""
    with get_conn() as db:
        existing = db.execute("SELECT id FROM jobs WHERE predefined_key = ?", (LB_PREDEFINED_KEY,)).fetchone()
        if existing:
            return existing["id"]
        cur = db.execute(
            "INSERT INTO jobs (name, description, predefined_key, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
            ("Déployer un load balancing", "Installe HAProxy sur la première VM cible, Nginx sur les suivantes (backends), puis teste la connectivité.", LB_PREDEFINED_KEY, "system", _now()),
        )
        db.commit()
        return cur.lastrowid


def run_lb_job(job_id, targets, dry_run=False, username="system"):
    """Sequence generee dynamiquement (pas de job_steps stockes pour ce
    job predefini -- ses etapes dependent des cibles choisies a chaque run,
    contrairement a un job custom dont la sequence est fixe)."""
    if len(targets) < 2:
        raise RuntimeError("Il faut au moins 2 VM cibles (1 répartiteur + 1 backend minimum)")
    lb_vm, backends = targets[0], targets[1:]

    backend_lines = "\n".join(
        f"    server backend{i+1} __IP_{b}__:80 check" for i, b in enumerate(backends)
    )
    haproxy_cfg = (
        "frontend http_front\n    bind *:80\n    default_backend http_back\n"
        "backend http_back\n    balance roundrobin\n" + backend_lines
    )

    steps = [
        {"ordre": 0, "cible_type": "vm", "cible": lb_vm, "commande": "sudo apt-get update -qq && sudo apt-get install -y -qq haproxy", "condition_type": "exit_code", "condition_valeur": "0"},
    ]
    for i, b in enumerate(backends):
        steps.append({"ordre": len(steps), "cible_type": "vm", "cible": b, "commande": "sudo apt-get update -qq && sudo apt-get install -y -qq nginx && echo OK_$(hostname) | sudo tee /var/www/html/index.html", "condition_type": "exit_code", "condition_valeur": "0"})

    # Resout les IP des backends pour construire la vraie config HAProxy --
    # doit se faire APRES l'installation (les VM doivent deja avoir une
    # adresse) mais AVANT de pousser la config sur le noeud LB.
    resolved_cfg_step_index = len(steps)
    steps.append({"ordre": resolved_cfg_step_index, "cible_type": "vm", "cible": lb_vm, "commande": "__PLACEHOLDER_HAPROXY_CONFIG__", "condition_type": "exit_code", "condition_valeur": "0"})
    steps.append({"ordre": len(steps), "cible_type": "vm", "cible": lb_vm, "commande": "curl -sf -o /dev/null -w '%{http_code}' http://localhost:80/ | grep -q 200", "condition_type": "exit_code", "condition_valeur": "0"})

    # Remplace le placeholder par la vraie commande une fois les IP connues
    # (necessite de resoudre chaque backend -- fait ici plutot que dans
    # _run_command generique, specifique a ce job).
    from app.routers.vms import _get_ip
    ip_map = {}
    if not dry_run:
        conn = open_conn()
        try:
            for b in backends:
                domain = conn.lookupByName(b)
                ip_map[b] = _get_ip(domain) or "0.0.0.0"
        finally:
            conn.close()
    else:
        ip_map = {b: "0.0.0.0" for b in backends}

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
    task_id = create_task("run_job", "Déployer un load balancing", node=node, username=username)
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
    log_action(username, "run_job", "Déployer un load balancing", "succes" if overall_ok else "echec", resultat)
    return run_id


def run_lb_job_async(job_id, targets, dry_run=False, username="system"):
    threading.Thread(target=run_lb_job, args=(job_id, targets, dry_run, username), daemon=True).start()
