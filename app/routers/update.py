"""Mise a jour d'Hyperlite depuis son propre depot Git (chantier 7 de la
roadmap vSphere/vCenter) -- equivalent vSphere Lifecycle Manager, mais Git
comme source de verite plutot qu'un depot de patchs proprietaire.

Ne touche QUE la couche de gestion Hyperlite (code API + interface) -- les VM
deja actives, pilotees directement par libvirt/QEMU independamment du
processus Hyperlite, ne sont ni arretees ni redemarrees par une mise a jour
(meme principe qu'un reboot de vCenter qui n'affecte pas les VM deja
actives sous ESXi).

Contrainte reelle de ce depot (verifiee le 2026-09-13, pas hypothetique) :
le workflow de developpement actuel commite directement sur `master` depuis
des sessions de travail live sur kvm-lab, donc l'arbre de travail est
tres souvent "sale" (modifications non commitees) au moment ou quelqu'un
voudrait declencher une mise a jour. Plutot que de faire un `git pull`
optimiste qui risquerait un conflit de fusion en pleine mise a jour, l'IHM
refuse purement et simplement de demarrer si l'arbre n'est pas propre, avec
un message exploitable -- c'est le cas "gestion propre" demande, pas un
oubli.
"""
import os
import subprocess
import tarfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.core.audit import log_action
from app.core.security import require_role
from app.core.tasks import create_task, finish_task, update_task_progress

router = APIRouter(prefix="/update", tags=["update"])

REPO_DIR = Path(__file__).resolve().parent.parent.parent  # /root/hyperlite
BACKUP_DIR = Path("/root/hyperlite-backups")
WATCHDOG_SCRIPT = REPO_DIR / "scripts" / "update_watchdog.sh"

# Exclusions du tarball de sauvegarde : uniquement du code/etat DERIVE,
# jamais de la donnee (hyperlite.db, data/, .env restent inclus -- c'est
# precisement ce qu'un rollback doit pouvoir restaurer).
_BACKUP_EXCLUDES = ["--exclude=venv", "--exclude=dashboard/node_modules", "--exclude=dashboard/dist", "--exclude=.git"]


def _run(cmd, cwd=None, timeout=180):
    return subprocess.run(cmd, cwd=str(cwd or REPO_DIR), capture_output=True, text=True, timeout=timeout)


def _current_commit():
    r = _run(["git", "rev-parse", "HEAD"])
    return r.stdout.strip() if r.returncode == 0 else None


def _current_branch():
    r = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    return r.stdout.strip() if r.returncode == 0 else "master"


def _is_dirty():
    r = _run(["git", "status", "--porcelain"])
    return bool(r.stdout.strip())


# --- Mise a jour via depot APT (2026-09-17) -- alternative au mecanisme
# git ci-dessus, pour toute machine ayant explicitement adopte le paquet
# `hyperlite` (voir installer/build-deb.sh, installer/build-apt-repo.sh).
# kvm-lab reste developpe en clone Git (voir CLAUDE.md) : sur cette
# machine precise, _install_method() renvoie toujours "git" (presence de
# .git) et TOUT le comportement ci-dessous reste inchange -- ce nouveau
# chemin ne s'active QUE sur une machine ou `apt install hyperlite` a
# reellement ete execute au moins une fois (dpkg la connait comme
# "installee").
def _install_method():
    if (REPO_DIR / ".git").exists():
        return "git"
    r = _run(["dpkg-query", "-W", "-f=${Status}", "hyperlite"])
    if r.returncode == 0 and "install ok installed" in r.stdout:
        return "apt"
    return "git"  # etat indetermine : repli sur le comportement historique


def _dpkg_installed_version():
    r = _run(["dpkg-query", "-W", "-f=${Version}", "hyperlite"])
    return r.stdout.strip() if r.returncode == 0 else None


def _check_update_apt():
    installed = _dpkg_installed_version()
    try:
        upd = _run(["apt-get", "update"], timeout=60)
    except subprocess.TimeoutExpired:
        return {"verifiable": False, "erreur": "Délai dépassé en contactant le dépôt APT (réseau ?)", "commit_local": installed}
    if upd.returncode != 0:
        return {
            "verifiable": False,
            "erreur": f"Impossible de contacter le dépôt APT : {upd.stderr.strip()[:400]}",
            "commit_local": installed,
        }

    policy = _run(["apt-cache", "policy", "hyperlite"])
    candidate = None
    for line in policy.stdout.splitlines():
        line = line.strip()
        if line.startswith("Candidate:"):
            candidate = line.split(":", 1)[1].strip()
    a_jour = bool(candidate) and installed == candidate
    changelog = [f"Nouvelle version disponible : {candidate}"] if not a_jour and candidate else []

    return {
        "verifiable": True,
        "branche": "apt",
        "commit_local": installed,
        "commit_distant": candidate,
        "a_jour": a_jour,
        "arbre_propre": True,  # non applicable en mode apt (pas d'arbre de travail Git)
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
        return {"verifiable": False, "erreur": "Délai dépassé en contactant le dépôt distant (réseau ?)", "commit_local": local}

    if fetch.returncode != 0:
        return {
            "verifiable": False,
            "erreur": f"Impossible de contacter le dépôt distant : {fetch.stderr.strip()[:400]}",
            "commit_local": local,
        }

    remote_r = _run(["git", "rev-parse", f"origin/{branch}"])
    remote = remote_r.stdout.strip() if remote_r.returncode == 0 else None
    a_jour = bool(remote) and local == remote
    changelog = []
    if not a_jour and remote and local:
        log_r = _run(["git", "log", "--oneline", f"{local}..{remote}"])
        if log_r.returncode == 0:
            changelog = [l for l in log_r.stdout.strip().splitlines() if l]

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
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tarball = BACKUP_DIR / f"hyperlite-backup-{stamp}.tar.gz"
    cmd = ["tar", "czf", str(tarball)] + _BACKUP_EXCLUDES + ["-C", str(REPO_DIR.parent), REPO_DIR.name]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    # ATTENTION (bug reel trouve lors du tout premier /update/apply jamais
    # execute en conditions reelles, sur le serveur physique d'Antho) : tar
    # renvoie le code de sortie 1 -- pas 0, mais pas non plus une vraie
    # erreur -- des qu'un fichier change PENDANT sa lecture ("file changed
    # as we read it"). Hyperlite tourne en continu ET ecrit sans arret dans
    # hyperlite.db (mode WAL, collecte de metriques, etc.) exactement
    # pendant que ce tar l'archive -- ce n'est pas un cas rare, c'est
    # SYSTEMATIQUE sur une instance active. D'apres tar lui-meme (man tar,
    # section EXIT STATUS) : 0 = succes, 1 = "some files differ" (avertissement
    # non fatal, l'archive est quand meme utilisable), 2 = erreur fatale
    # reelle. Traiter 1 comme un echec bloquait TOUTE mise a jour des qu'un
    # thread d'arriere-plan touchait un fichier au mauvais moment -- corrige
    # en ne considerant que le code 2+ comme une vraie erreur.
    if r.returncode >= 2:
        raise RuntimeError(f"Échec de la sauvegarde : {r.stderr.strip()[:400]}")
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
                "Arbre de travail non propre (modifications non commitées) -- mise à jour refusée pour ne pas "
                "risquer un conflit de fusion en cours de route. Commitez ou annulez les changements locaux d'abord."
            )

        step(5, "Sauvegarde de l'état actuel (code + config + base)")
        tarball = _backup(task_id)

        step(20, "Récupération de la dernière version (git fetch)")
        fetch = _run(["git", "fetch", "origin", branch])
        if fetch.returncode != 0:
            raise RuntimeError(f"git fetch a échoué : {fetch.stderr.strip()[:400]}")

        step(30, "Application de la nouvelle version (git reset --hard)")
        reset = _run(["git", "reset", "--hard", f"origin/{branch}"])
        if reset.returncode != 0:
            raise RuntimeError(f"git reset a échoué : {reset.stderr.strip()[:400]}")
        new_commit = _current_commit()

        changed = _run(["git", "diff", "--name-only", old_commit, new_commit]).stdout

        if "requirements.txt" in changed:
            step(45, "Installation des dépendances Python (pip install)")
            pip = _run([str(REPO_DIR / "venv" / "bin" / "pip"), "install", "-r", "requirements.txt"], timeout=300)
            if pip.returncode != 0:
                raise RuntimeError(f"pip install a échoué : {pip.stderr.strip()[:400]}")

        if "dashboard/package.json" in changed or "dashboard/package-lock.json" in changed:
            step(55, "Installation des dépendances front (npm install)")
            npm_i = _run(["npm", "install"], cwd=REPO_DIR / "dashboard", timeout=300)
            if npm_i.returncode != 0:
                raise RuntimeError(f"npm install a échoué : {npm_i.stderr.strip()[:400]}")

        # Rebuild du front a chaque mise a jour (pas seulement si package.json
        # a change -- le code source lui-meme a change dans la quasi-totalite
        # des mises a jour, un rebuild systematique est le seul moyen fiable
        # de ne jamais servir une interface perimee).
        step(70, "Reconstruction de l'interface (npm run build)")
        npm_b = _run(["npm", "run", "build"], cwd=REPO_DIR / "dashboard", timeout=300)
        if npm_b.returncode != 0:
            raise RuntimeError(f"npm run build a échoué : {npm_b.stderr.strip()[:400]}")

        # Pas de framework de migration de base (Alembic ou equivalent) dans
        # ce projet -- le schema est cree via des CREATE TABLE IF NOT EXISTS
        # idempotents (voir app/core/database.py::init_db), rejoues
        # automatiquement au prochain demarrage. Rien a faire ici de plus.
        step(85, "Vérification du schéma de base (pas de migration requise)")

        step(90, "Redémarrage du service et vérification post-mise à jour")
        log_action(username, "hyperlite_update_step", f"{old_commit[:8]} -> {new_commit[:8]}", "succes")

        # Le watchdog est lance AVANT le restart, en processus totalement
        # detache (start_new_session) : il doit survivre a la mort de CE
        # process Python quand systemctl le coupera. C'est lui, pas ce
        # thread, qui verifie que le NOUVEAU code demarre correctement et
        # fait le rollback automatique si non (voir scripts/update_watchdog.sh
        # -- ce process-ci ne peut pas verifier son propre remplacement).
        subprocess.Popen(
            ["bash", str(WATCHDOG_SCRIPT), str(tarball), str(REPO_DIR), str(log_file)],
            start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        subprocess.Popen(
            ["bash", "-c", "sleep 2 && systemctl restart hyperlite"],
            start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        # Ce process va mourir dans ~2s (le restart ci-dessus) : on marque la
        # tache "terminee" par optimisme avant de mourir plutot que de laisser
        # une tache eternellement "en_cours" -- le watchdog est le vrai filet
        # si le nouveau code ne demarre pas.
        finish_task(task_id, "termine")
        log_action(username, "hyperlite_update", f"{old_commit} -> {new_commit}", "succes")

    except Exception as exc:
        # Tout echec avant le restart : le process en cours d'execution
        # tourne encore sur l'ANCIEN code (aucun restart n'a encore ete
        # declenche), donc rien ne casse pour les utilisateurs -- mais on
        # remet quand meme l'arbre sur disque a l'ancien commit pour eviter
        # qu'un redemarrage manuel ulterieur ne reprenne un code a moitie mis
        # a jour.
        if old_commit:
            try:
                _run(["git", "reset", "--hard", old_commit])
            except Exception:
                pass
        finish_task(task_id, "echec", str(exc))
        log_action(username, "hyperlite_update", str(exc), "echec")


def _run_update_job_apt(task_id, username):
    log_file = BACKUP_DIR / "update.log"

    def step(pct, message):
        update_task_progress(task_id, pct)
        log_action(username, "hyperlite_update_step", message, "succes")

    old_version = _dpkg_installed_version()
    try:
        step(5, "Sauvegarde de l'état actuel (code + config + base)")
        tarball = _backup(task_id)

        step(20, "Mise à jour de l'index APT")
        upd = _run(["apt-get", "update"])
        if upd.returncode != 0:
            raise RuntimeError(f"apt-get update a échoué : {upd.stderr.strip()[:400]}")

        step(40, "Installation de la nouvelle version (apt-get install)")
        # HYPERLITE_SKIP_RESTART : le postinst du paquet redemarre NORMALEMENT
        # le service tout seul (comportement Debian standard, attendu par un
        # admin qui lance `apt install` a la main en SSH) -- mais ICI,
        # apt-get est un sous-processus du service hyperlite EN COURS
        # D'EXECUTION (cette requete HTTP meme) : le laisser se redemarrer
        # lui-meme depuis l'interieur de ce sous-processus tuerait apt-get en
        # plein milieu de son propre postinst. Ce flag dit au postinst de
        # NE PAS redemarrer, et c'est ce thread qui s'en charge juste apres,
        # de la meme facon detachee que le chemin Git (Popen + sleep 2).
        env = {**os.environ, "HYPERLITE_SKIP_RESTART": "1"}
        install = subprocess.run(
            ["apt-get", "install", "--only-upgrade", "-y", "hyperlite"],
            cwd=str(REPO_DIR), capture_output=True, text=True, timeout=300, env=env,
        )
        if install.returncode != 0:
            raise RuntimeError(f"apt-get install a échoué : {install.stderr.strip()[:400]}")
        new_version = _dpkg_installed_version()

        step(85, "Vérification du schéma de base (pas de migration requise)")
        step(90, "Redémarrage du service et vérification post-mise à jour")
        log_action(username, "hyperlite_update_step", f"{old_version} -> {new_version}", "succes")

        # Meme filet de securite que le chemin Git : watchdog detache AVANT
        # le restart (survit a la mort de ce process), qui verifie que le
        # nouveau code demarre correctement et restaure le tarball sinon.
        subprocess.Popen(
            ["bash", str(WATCHDOG_SCRIPT), str(tarball), str(REPO_DIR), str(log_file)],
            start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        subprocess.Popen(
            ["bash", "-c", "sleep 2 && systemctl restart hyperlite"],
            start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        finish_task(task_id, "termine")
        log_action(username, "hyperlite_update", f"{old_version} -> {new_version}", "succes")

    except Exception as exc:
        # Pas de "dpkg reset --hard" symetrique au chemin Git ici -- un echec
        # AVANT le restart signifie que l'ancien code tourne toujours (rien
        # n'a ete redemarre), le tarball de sauvegarde + le watchdog restent
        # le filet de securite reel si l'etat du paquet devait rester
        # incoherent malgre tout.
        finish_task(task_id, "echec", str(exc))
        log_action(username, "hyperlite_update", str(exc), "echec")


@router.post("/apply", status_code=202)
def apply_update(user: dict = Depends(require_role("admin"))):
    if _install_method() == "apt":
        task_id = create_task("hyperlite_update", "hyperlite", node=None, username=user["username"])
        threading.Thread(target=_run_update_job_apt, args=(task_id, user["username"]), daemon=True).start()
        return {"task_id": task_id, "statut": "en_cours"}

    branch = _current_branch()
    if _is_dirty():
        log_action(user["username"], "hyperlite_update", "arbre non propre", "echec")
        raise HTTPException(
            status_code=409,
            detail="Arbre de travail non propre (modifications non commitées) -- commitez ou annulez-les avant de mettre à jour.",
        )

    task_id = create_task("hyperlite_update", "hyperlite", node=None, username=user["username"])
    threading.Thread(target=_run_update_job, args=(task_id, user["username"], branch), daemon=True).start()
    return {"task_id": task_id, "statut": "en_cours"}
