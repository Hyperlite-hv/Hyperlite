"""Suppression automatique des VM inactives (chantier 19, 2026-09-17) --
option opt-in a la creation ("supprimer si arretee depuis N jours"),
pensee pour les VM de lab/test qu'on oublie de nettoyer. Desactivee par
defaut, VM par VM.

Regles de securite (verifiees dans cet ordre, chacune un motif de "skip"
silencieux pour CETTE VM -- ne doit jamais interrompre le cycle pour les
autres) :
- VM actuellement EN COURS D'EXECUTION : jamais supprimee, quel que soit
  le seuil -- le compteur ne s'incremente que pendant que la VM est
  ARRETEE (voir app/core/vm_meta.py::touch_vm_activity, appelee a chaque
  demarrage, qui repart a zero).
- VM protegee par la HA (chantier 17) : jamais supprimee automatiquement
  -- une VM HA est par definition consideree critique, l'oppose exact
  d'une VM jetable.
- Avertissement (notification, chantier 28) ~24h avant la suppression
  reelle -- pas de suppression surprise des le premier cycle qui detecte
  le depassement du seuil.
"""
import threading
import time
from datetime import datetime, timezone

import libvirt

from app.core.libvirt_utils import open_conn
from app.core.vm_meta import list_all_auto_cleanup, delete_vm_auto_cleanup
from app.core.audit import log_action
from app.core.database import get_conn

CHECK_INTERVAL_S = 3600  # un seuil se compte en JOURS -- pas besoin de plus frequent qu'horaire
WARNING_HOURS_BEFORE = 24


def _age_hours(iso_ts):
    dt = datetime.fromisoformat(iso_ts)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600


def _mark_warned(vm_name):
    with get_conn() as db:
        db.execute(
            "UPDATE vm_auto_cleanup SET warned_at = ? WHERE vm_name = ?",
            (datetime.now(timezone.utc).isoformat(), vm_name),
        )
        db.commit()


def check_once():
    from app.core.ha import get_protected  # import tardif : evite un cycle au chargement du module
    from app.routers.vms import _perform_vm_deletion

    rows = list_all_auto_cleanup()
    if not rows:
        return

    conn = open_conn()
    try:
        for row in rows:
            vm_name = row["vm_name"]
            try:
                domain = conn.lookupByName(vm_name)
            except libvirt.libvirtError:
                # VM deja supprimee par un autre chemin (admin, DELETE
                # /vms/{name} classique) -- nettoie l'entree orpheline
                # plutot que de la retenter indefiniment a chaque cycle.
                delete_vm_auto_cleanup(vm_name)
                continue

            if domain.isActive():
                continue  # le compteur ne court que pendant l'arret

            if get_protected(vm_name):
                continue  # VM HA : jamais touchee automatiquement

            age_h = _age_hours(row["last_active_at"])
            threshold_h = row["inactive_days"] * 24

            if age_h < threshold_h - WARNING_HOURS_BEFORE:
                continue  # encore loin du seuil, rien a faire

            if age_h < threshold_h:
                if not row["warned_at"]:
                    _mark_warned(vm_name)
                    msg = (
                        f"VM '{vm_name}' sera supprimée automatiquement dans ~24h "
                        f"(arrêtée depuis {row['inactive_days']}+ jours, seuil configuré à la création)"
                    )
                    log_action("system", "auto_cleanup_warning", vm_name, "succes", msg)
                continue

            # Seuil depasse : suppression reelle.
            try:
                _perform_vm_deletion(conn, domain, vm_name)
                msg = f"Suppression automatique : arrêtée depuis {row['inactive_days']}+ jours (seuil configuré à la création)"
                log_action("system", "delete_vm", vm_name, "succes", msg)
            except Exception as e:
                log_action("system", "delete_vm", vm_name, "echec", f"Suppression automatique échouée : {e!r}")
    finally:
        conn.close()


def _loop():
    while True:
        try:
            check_once()
        except Exception as e:
            print(f"[vm_cleanup] cycle échoué : {e!r}", flush=True)
        time.sleep(CHECK_INTERVAL_S)


def start_auto_cleanup_scheduler():
    threading.Thread(target=_loop, daemon=True, name="vm-auto-cleanup").start()
