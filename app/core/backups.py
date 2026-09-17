"""Backup natif des VM (chantier 13 de la roadmap vSphere/vCenter,
2026-09-13) -- absent jusqu'ici (seuls les snapshots qcow2 existaient,
chantier 4, et ils ne survivent PAS a la perte du disque source : meme
fichier). Un backup est une copie complete et autonome, stockee ailleurs.

Deux modes :
  - "froid" (VM arretee) : simple copie du/des disque(s) qcow2 via
    qemu-img convert (support natif de la conversion/compression qcow2).
  - "chaud" (VM active) : snapshot EXTERNE (nouveau fichier overlay,
    chaine de backing files) pour figer le disque source a un instant T
    sans arreter la VM, copie du fichier fige, puis blockCommit+pivot pour
    fusionner l'overlay dans le disque courant et supprimer le snapshot --
    exactement le mecanisme prototype et teste au chantier 4 (a l'epoque
    ecarte pour les snapshots eux-memes a cause de la limite de
    restauration de libvirt/QEMU sur les snapshots externes, mais cette
    limite ne s'applique pas ici : on ne restaure jamais l'overlay
    directement, on l'utilise juste comme point de coherence transitoire
    avant de le refusionner).

Progression REELLE (contrairement aux snapshots, chantier 4, ou aucune stat
n'existe) : `qemu-img convert -p` ecrit un pourcentage sur stdout, parse ici
pour alimenter update_task_progress().
"""
import hashlib
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import libvirt

from app.core.audit import log_action
from app.core.database import get_conn
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import open_conn
from app.core.tasks import create_task, finish_task, update_task_progress
from app.core.vm_builder import IMAGES_DIR

DEFAULT_BACKUP_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "backups"
SCHEDULER_INTERVAL_S = 300  # verifie les jobs dus toutes les 5 minutes -- suffisant, la granularite est l'heure (HH:MM)

# BUG REEL trouve en testant ce chantier (verification de la retention,
# 2026-09-17) : 4 backups manuels declenches en rafale sur la meme VM ont
# fait planter des requetes SANS AUCUN RAPPORT (ex. GET /networks) avec
# 'database is locked', malgre le mode WAL + timeout 30s deja en place
# (chantier 11/13) -- 4 threads qui martelent la base en meme temps
# (insert/update de progression frequents pendant qemu-img convert, puis
# potentiellement plusieurs DELETE de retention simultanes) suffit a
# depasser meme un timeout genereux sous cette charge. Plutot que
# d'augmenter encore le timeout (repousse le probleme sans le resoudre),
# les backups sont serialises : un seul a la fois, les autres attendent
# leur tour. Sensé de toute facon independamment du probleme SQLite --
# plusieurs qemu-img convert simultanes sur le meme disque hote se
# battent deja pour la bande passante I/O.
_backup_lock = threading.Lock()

_PROGRESS_RE = re.compile(r"\((\d+(?:\.\d+)?)/100%\)")


def _now():
    return datetime.now(timezone.utc)


def _sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def domain_disk_paths(domain):
    import xml.etree.ElementTree as ET
    root = ET.fromstring(domain.XMLDesc(0))
    paths = []
    for disk in root.findall(".//devices/disk"):
        if disk.get("device") != "disk":
            continue
        source = disk.find("source")
        target = disk.find("target")
        if source is not None and source.get("file") and target is not None:
            paths.append((target.get("dev"), source.get("file")))
    return paths


def qemu_img_convert_with_progress(source, dest, task_id, base_pct, span_pct):
    """Copie via qemu-img convert -p, parse la progression reelle sur stdout
    et la reporte dans la tache (base_pct/span_pct permettent d'appeler ca
    plusieurs fois -- ex. plusieurs disques -- sans que chacun reparte de 0%)."""
    proc = subprocess.Popen(
        ["qemu-img", "convert", "-p", "-O", "qcow2", str(source), str(dest)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
    )
    # BUG REEL trouve en testant la retention (4 backups concurrents,
    # 2026-09-17) : un 'database is locked' remonte depuis
    # update_task_progress() DANS cette boucle laissait le processus
    # qemu-img deja termine mais jamais "wait()" -- zombie orphelin
    # (confirme via `ps aux`, plusieurs <defunct> apres le test). try/
    # finally : proc.wait() se produit TOUJOURS, meme si la lecture de
    # stdout ou update_task_progress() leve une exception -- le processus
    # est reap en tout cas, l'exception continue de se propager ensuite
    # normalement (gere par l'appelant, voir run_backup).
    try:
        last_pct = 0
        last_reported = -1
        last_write_time = 0.0
        for line in proc.stdout:
            m = _PROGRESS_RE.search(line)
            if m:
                last_pct = float(m.group(1))
                reported = int(base_pct + span_pct * last_pct / 100)
                # THROTTLE ajoute en corrigeant le meme bug de concurrence
                # (voir commentaire sur _backup_lock plus haut) : qemu-img
                # -p emet une ligne de progression tres frequemment (voire
                # plusieurs fois par seconde sur un disque rapide), et
                # chaque update_task_progress() est une ECRITURE SQLite --
                # sur cette base, meme un simple GET fait sa propre
                # ecriture (log_action() est appele partout, y compris
                # pour les lectures), donc le mode WAL ne protege pas
                # contre CE genre de contention ecrivain-contre-ecrivain
                # (WAL resout lecteur-contre-ecrivain, pas les deux sens).
                # N'ecrit que si le pourcentage ARRONDI a change ET qu'au
                # moins 0.5s s'est ecoulee depuis la derniere ecriture --
                # reduit le volume d'ecritures de backup d'un ou deux
                # ordres de grandeur sans perdre de granularite utile pour
                # une barre de progression (personne ne distingue 47% de
                # 48% affiche 10x par seconde).
                now = time.monotonic()
                if reported != last_reported and now - last_write_time >= 0.5:
                    update_task_progress(task_id, reported)
                    last_reported = reported
                    last_write_time = now
        stderr = proc.stderr.read()
    finally:
        proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"qemu-img convert a échoué : {stderr.strip()[:400]}")


def backup_cold(domain, vm_name, dest_dir, task_id, disks=None):
    disks = disks if disks is not None else domain_disk_paths(domain)
    if not disks:
        raise RuntimeError("Aucun disque trouvé sur cette VM")
    dest_paths = []
    span = 90 / len(disks)
    for i, (dev, source) in enumerate(disks):
        dest = dest_dir / f"{dev}.qcow2"
        qemu_img_convert_with_progress(source, dest, task_id, base_pct=5 + i * span, span_pct=span)
        dest_paths.append(dest)
    return dest_paths


def backup_hot(conn, domain, vm_name, dest_dir, task_id, disks=None):
    """Snapshot externe transitoire par disque -> copie du fichier gele ->
    blockCommit+pivot pour re-fusionner -- la VM continue de tourner sans
    interruption pendant toute l'operation (juste un tres bref gel au
    moment de la creation du snapshot lui-meme, comme n'importe quel
    snapshot externe QEMU). `disks` : sous-ensemble optionnel (ex. un seul
    disque pour un export, voir app/core/vm_export.py) -- toute la VM par
    defaut."""
    all_disks = domain_disk_paths(domain)
    if not all_disks:
        raise RuntimeError("Aucun disque trouvé sur cette VM")
    disks = disks if disks is not None else all_disks
    target_devs = {dev for dev, _ in disks}

    import xml.etree.ElementTree as ET
    overlay_paths = {}
    disk_xml_parts = []
    # Un disque APPARTENANT a la VM mais absent de `disks` (export partiel,
    # voir app/core/vm_export.py) doit rester explicitement exclu
    # (snapshot='no') -- sinon libvirt lui applique quand meme son
    # comportement de snapshot par defaut (interne), qu'on ne nettoierait
    # jamais puisque la boucle de fusion plus bas ne parcourt que `disks`.
    for dev, source in all_disks:
        if dev in target_devs:
            overlay = IMAGES_DIR / f"{vm_name}.backup-{int(time.time())}.{dev}.qcow2"
            overlay_paths[dev] = overlay
            disk_xml_parts.append(f"<disk name='{dev}' snapshot='external'><source file='{overlay}'/></disk>")
        else:
            disk_xml_parts.append(f"<disk name='{dev}' snapshot='no'/>")
    snap_name = f"hyperlite-backup-{int(time.time())}"
    snap_xml = f"<domainsnapshot><name>{snap_name}</name><disks>{''.join(disk_xml_parts)}</disks></domainsnapshot>"

    snap = domain.snapshotCreateXML(snap_xml, libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_DISK_ONLY)
    update_task_progress(task_id, 10)

    try:
        dest_paths = []
        span = 70 / len(disks)
        for i, (dev, source) in enumerate(disks):
            dest = dest_dir / f"{dev}.qcow2"
            # On copie la base GELEE (le fichier `source` original, plus
            # touche par la VM tant que l'overlay est actif) -- pas
            # l'overlay, qui continue de grossir avec l'activite de la VM.
            qemu_img_convert_with_progress(source, dest, task_id, base_pct=15 + i * span, span_pct=span)
            dest_paths.append(dest)
    finally:
        # Fusion de l'overlay dans la base pour CHAQUE disque, meme si la
        # copie a echoue sur l'un d'eux -- ne jamais laisser la VM tourner
        # indefiniment sur un overlay transitoire (chaine qui grossit sans
        # fin, orpheline si Hyperlite redemarre entre-temps).
        for dev, source in disks:
            try:
                domain.blockCommit(dev, str(source), None, 0, libvirt.VIR_DOMAIN_BLOCK_COMMIT_ACTIVE)
                for _ in range(60):
                    info = domain.blockJobInfo(dev, 0)
                    if not info or (info.get("end", 0) and info.get("cur", 0) >= info["end"]):
                        break
                    time.sleep(0.5)
                domain.blockJobAbort(dev, libvirt.VIR_DOMAIN_BLOCK_JOB_ABORT_PIVOT)
            except libvirt.libvirtError as e:
                log_action("system", "backup_commit_warning", vm_name, "echec", f"{dev}: {describe_exception(e)}")
        try:
            snap.delete(libvirt.VIR_DOMAIN_SNAPSHOT_DELETE_METADATA_ONLY)
        except libvirt.libvirtError:
            pass
        for overlay in overlay_paths.values():
            Path(overlay).unlink(missing_ok=True)

    return dest_paths


def run_backup(vm_name, target_dir=None, job_id=None, username="system"):
    """Lance un backup (choisit chaud/froid selon l'etat reel de la VM) et
    renvoie l'id de la ligne `backups` creee. Synchrone -- appele depuis un
    thread par l'endpoint (backup manuel) ou par le planificateur.

    _backup_lock : un seul backup a la fois sur TOUT le serveur (toutes VM
    confondues), voir le commentaire au-dessus de _backup_lock -- un
    appelant concurrent attend simplement son tour plutot que d'echouer."""
    with _backup_lock:
        return _run_backup_locked(vm_name, target_dir, job_id, username)


def _run_backup_locked(vm_name, target_dir, job_id, username):
    target_root = Path(target_dir) if target_dir else DEFAULT_BACKUP_DIR
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(vm_name)
        except libvirt.libvirtError:
            raise RuntimeError(f"VM '{vm_name}' introuvable")

        mode = "chaud" if domain.isActive() else "froid"
        stamp = _now().strftime("%Y%m%dT%H%M%SZ")
        dest_dir = target_root / vm_name / stamp
        dest_dir.mkdir(parents=True, exist_ok=True)

        task_id = create_task("backup_vm", vm_name, node=conn.getHostname(), username=username)
        with get_conn() as db:
            cur = db.execute(
                "INSERT INTO backups (vm_name, job_id, chemin, mode, cree_le, statut, task_id) "
                "VALUES (?, ?, ?, ?, ?, 'en_cours', ?)",
                (vm_name, job_id, str(dest_dir), mode, _now().isoformat(), task_id),
            )
            backup_id = cur.lastrowid
            db.commit()

        try:
            if mode == "froid":
                dest_paths = backup_cold(domain, vm_name, dest_dir, task_id)
            else:
                dest_paths = backup_hot(conn, domain, vm_name, dest_dir, task_id)

            update_task_progress(task_id, 95)
            total_size = sum(p.stat().st_size for p in dest_paths)
            checksum = _sha256_of(dest_paths[0]) if len(dest_paths) == 1 else None
            with get_conn() as db:
                db.execute(
                    "UPDATE backups SET statut = 'termine', taille_octets = ?, checksum_sha256 = ? WHERE id = ?",
                    (total_size, checksum, backup_id),
                )
                db.commit()
            finish_task(task_id, "termine")
            log_action(username, "backup_vm", vm_name, "succes", f"{mode}, {total_size} octets -> {dest_dir}")
            # BUG REEL trouve en verifiant ce chantier (2026-09-17) : la
            # retention (retention_count, deja dans le schema depuis le
            # chantier 13) n'etait appliquee QUE par le planificateur
            # (_scheduler_loop), jamais pour un backup MANUEL (POST
            # /vms/{name}/backups, sans job_id) -- une VM sans job planifie
            # mais sauvegardee ponctuellement a la main accumulait des
            # backups sans AUCUNE limite. Applique maintenant ici, au meme
            # endroit pour les deux cas (manuel et planifie), sur TOUTES
            # les sauvegardes de cette VM (pas seulement celles du meme
            # job_id) -- un retention_count configure pour une VM doit
            # plafonner le nombre total de ses sauvegardes, pas juste
            # celles issues d'un job precis. Ne fait rien si aucun
            # backup_jobs n'existe pour cette VM (pas de politique
            # configuree = pas de limite imposee, comportement inchange
            # pour un usage 100% manuel sans planification).
            with get_conn() as db:
                job_row = db.execute("SELECT retention_count FROM backup_jobs WHERE vm_name = ?", (vm_name,)).fetchone()
            if job_row:
                _apply_retention(vm_name, job_row["retention_count"])
        except Exception as e:
            msg = describe_exception(e) if isinstance(e, libvirt.libvirtError) else str(e)
            with get_conn() as db:
                db.execute("UPDATE backups SET statut = 'echec', erreur = ? WHERE id = ?", (msg, backup_id))
                db.commit()
            finish_task(task_id, "echec", msg)
            log_action(username, "backup_vm", vm_name, "echec", msg)
            shutil.rmtree(dest_dir, ignore_errors=True)
            raise
        return backup_id
    finally:
        conn.close()


def restore_backup(backup_id, mode, new_name=None, username="system"):
    """mode='overwrite' : ecrase les disques de la VM d'origine (doit etre
    arretee). mode='new' : definit une nouvelle VM a partir de la sauvegarde,
    avec un nouvel UUID/MAC (meme logique que le clonage, chantier 5)."""
    with get_conn() as db:
        row = db.execute("SELECT * FROM backups WHERE id = ?", (backup_id,)).fetchone()
    if not row:
        raise RuntimeError("Sauvegarde introuvable")
    if row["statut"] != "termine":
        raise RuntimeError("Cette sauvegarde n'est pas dans un état restaurable (échec ou en cours)")

    src_dir = Path(row["chemin"])
    disk_files = sorted(src_dir.glob("*.qcow2"))
    if not disk_files:
        raise RuntimeError("Aucun fichier disque trouvé dans cette sauvegarde")

    conn = open_conn()
    task_id = create_task("restore_backup", row["vm_name"], node=conn.getHostname(), username=username)
    try:
        if mode == "overwrite":
            target_name = row["vm_name"]
            try:
                domain = conn.lookupByName(target_name)
            except libvirt.libvirtError:
                raise RuntimeError(f"VM d'origine '{target_name}' introuvable -- utilisez la restauration vers un nouvel emplacement")
            if domain.isActive():
                raise RuntimeError("Arrêtez la VM avant de restaurer par-dessus")
            existing_disks = domain_disk_paths(domain)
            for i, (dev, dest_path) in enumerate(existing_disks):
                src = disk_files[min(i, len(disk_files) - 1)]
                update_task_progress(task_id, int(10 + 80 * i / max(len(existing_disks), 1)))
                shutil.copyfile(src, dest_path)
            finish_task(task_id, "termine")
            log_action(username, "restore_backup", target_name, "succes", f"écrasement depuis backup #{backup_id}")
            return {"vm": target_name, "mode": "overwrite"}

        elif mode == "new":
            from app.routers.vms import IMAGES_DIR as _IMAGES_DIR  # evite import circulaire au chargement du module
            from app.core.vm_builder import validate_name, build_domain_xml
            if not new_name:
                raise RuntimeError("new_name requis pour une restauration vers un nouvel emplacement")
            err = validate_name(new_name)
            if err:
                raise RuntimeError(err)
            try:
                conn.lookupByName(new_name)
                raise RuntimeError(f"Une VM '{new_name}' existe déjà")
            except libvirt.libvirtError:
                pass

            new_disk_paths = []
            for i, src in enumerate(disk_files):
                suffix = "" if i == 0 else f"-{i + 1}"
                dest = _IMAGES_DIR / f"{new_name}{suffix}.qcow2"
                update_task_progress(task_id, int(10 + 70 * i / len(disk_files)))
                shutil.copyfile(src, dest)
                new_disk_paths.append(dest)

            xml = build_domain_xml(new_name, 1, 1024, new_disk_paths, None, "default")
            conn.defineXML(xml)
            finish_task(task_id, "termine")
            log_action(username, "restore_backup", new_name, "succes", f"nouvelle VM depuis backup #{backup_id}")
            return {"vm": new_name, "mode": "new"}
        else:
            raise RuntimeError("mode invalide (attendu 'overwrite' ou 'new')")
    except Exception as e:
        finish_task(task_id, "echec", str(e))
        raise
    finally:
        conn.close()


def _apply_retention(vm_name, retention_count):
    """Par VM, pas par job_id (voir le commentaire dans run_backup) : un
    retention_count configure pour une VM plafonne le nombre TOTAL de ses
    sauvegardes terminees, qu'elles viennent d'un job planifie ou d'un
    declenchement manuel."""
    with get_conn() as db:
        rows = db.execute(
            "SELECT id, chemin FROM backups WHERE vm_name = ? AND statut = 'termine' ORDER BY cree_le DESC",
            (vm_name,),
        ).fetchall()
        for row in rows[retention_count:]:
            shutil.rmtree(row["chemin"], ignore_errors=True)
            db.execute("DELETE FROM backups WHERE id = ?", (row["id"],))
        db.commit()


def _next_run(frequence, heure, from_time=None):
    base = from_time or _now()
    hh, mm = (int(x) for x in heure.split(":"))
    candidate = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if candidate <= base:
        candidate += timedelta(days=1)
    if frequence == "hebdomadaire":
        while candidate.weekday() != 0:  # execute le lundi
            candidate += timedelta(days=1)
    elif frequence == "mensuel":
        candidate = candidate.replace(day=1)
        if candidate <= base:
            month = candidate.month % 12 + 1
            year = candidate.year + (1 if candidate.month == 12 else 0)
            candidate = candidate.replace(year=year, month=month)
    return candidate


def _scheduler_loop():
    while True:
        try:
            now = _now()
            with get_conn() as db:
                due = db.execute(
                    "SELECT * FROM backup_jobs WHERE actif = 1 AND prochaine_execution <= ?", (now.isoformat(),)
                ).fetchall()
            for job in due:
                try:
                    # La retention est desormais appliquee DANS run_backup()
                    # elle-meme (voir son corps) -- couvre aussi les backups
                    # manuels de cette VM, pas seulement ceux du planificateur.
                    run_backup(job["vm_name"], job["cible_dir"], job_id=job["id"], username="scheduler")
                except Exception as e:
                    log_action("scheduler", "backup_job_echec", job["vm_name"], "echec", str(e))
                next_run = _next_run(job["frequence"], job["heure"], now)
                with get_conn() as db:
                    db.execute(
                        "UPDATE backup_jobs SET derniere_execution = ?, prochaine_execution = ? WHERE id = ?",
                        (now.isoformat(), next_run.isoformat(), job["id"]),
                    )
                    db.commit()
        except Exception as e:
            print(f"[backups] scheduler tick échoué : {e!r}", flush=True)
        time.sleep(SCHEDULER_INTERVAL_S)


def start_backup_scheduler():
    thread = threading.Thread(target=_scheduler_loop, daemon=True)
    thread.start()
    return thread
