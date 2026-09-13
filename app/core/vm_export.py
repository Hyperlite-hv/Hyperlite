"""Export/Import de VM par fichier disque (chantier 23, 2026-09-13). Une
sauvegarde (chantier 13, app/core/backups.py) reste sur cet hote, pensee
pour une restauration locale ; un export produit au contraire un fichier
qcow2 telechargeable destine a QUITTER l'hote (migration vers un autre
serveur, archive externe, partage) -- et symetriquement, un disque importe
(voir app/routers/vm_disks.py) est un fichier arrive d'ailleurs, utilise
pour creer une nouvelle VM directement, sans passer par ISO+kickstart.

Reutilise les briques du chantier 13 (domain_disk_paths,
qemu_img_convert_with_progress, backup_cold/backup_hot avec leur mecanisme
chaud/froid deja teste) plutot que de redupliquer la logique -- restreint
volontairement au disque systeme (index 0, "sda") : une VM multi-disques
complete releve de la sauvegarde (chantier 13), pas de ce chantier. Cote
import, aucune tentative d'injecter cloud-init/cle SSH dans le disque
importe (contrairement aux VM crees depuis zero) : on ne sait rien de son
contenu (OS, systeme de fichiers, comptes existants) -- il demarre tel
quel, l'acces se fait avec les identifiants deja presents dessus."""
import shutil
from datetime import datetime, timezone
from pathlib import Path

import libvirt

from app.core.audit import log_action
from app.core.backups import backup_cold, backup_hot, domain_disk_paths
from app.core.error_messages import describe_exception
from app.core.libvirt_utils import open_conn
from app.core.tasks import create_task, finish_task, update_task_progress

EXPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "vm-exports"
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)


def list_exports():
    result = []
    for p in sorted(EXPORTS_DIR.glob("*.qcow2"), key=lambda p: p.stat().st_mtime, reverse=True):
        st = p.stat()
        result.append({"nom": p.name, "taille_octets": st.st_size, "modifie_le": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()})
    return result


def run_export(vm_name, username="system"):
    """Synchrone -- appele depuis un thread par l'endpoint (voir
    app/routers/vm_export.py), meme schema que run_backup()."""
    conn = open_conn()
    try:
        try:
            domain = conn.lookupByName(vm_name)
        except libvirt.libvirtError:
            raise RuntimeError(f"VM '{vm_name}' introuvable")

        all_disks = domain_disk_paths(domain)
        if not all_disks:
            raise RuntimeError("Aucun disque trouvé sur cette VM")
        disk0 = all_disks[:1]

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        task_id = create_task("export_vm", vm_name, node=conn.getHostname(), username=username)
        work_dir = EXPORTS_DIR / f".tmp-{vm_name}-{stamp}"
        work_dir.mkdir(parents=True, exist_ok=True)

        try:
            if domain.isActive():
                dest_paths = backup_hot(conn, domain, vm_name, work_dir, task_id, disks=disk0)
            else:
                dest_paths = backup_cold(domain, vm_name, work_dir, task_id, disks=disk0)

            final = EXPORTS_DIR / f"{vm_name}--{stamp}.qcow2"
            shutil.move(str(dest_paths[0]), str(final))
            update_task_progress(task_id, 100)
            finish_task(task_id, "termine")
            log_action(username, "export_vm", vm_name, "succes", f"-> {final.name}")
            return {"nom": final.name, "taille_octets": final.stat().st_size, "task_id": task_id}
        except Exception as e:
            msg = describe_exception(e) if isinstance(e, libvirt.libvirtError) else str(e)
            finish_task(task_id, "echec", msg)
            log_action(username, "export_vm", vm_name, "echec", msg)
            raise
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
    finally:
        conn.close()
