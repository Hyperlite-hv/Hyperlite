"""Verification automatique et periodique des mises a jour Hyperlite
(2026-09-17, suite directe du chantier 7bis/ISO-apt) -- demande explicite
d'Antho ("il faut tout harmonise que tout soit bien a jour") apres avoir
trouve serveur-antho silencieusement desynchronise de plusieurs versions :
son depot APT pointait vers un miroir GitHub Pages perime (voir CLAUDE.md,
"ISO appliance : installation via apt"), et rien ne signalait l'ecart tant
qu'un admin ne cliquait pas lui-meme sur "Verifier les mises a jour".

Meme principe de prudence que le chantier 17 (HA) : ce module DETECTE et
ALERTE, il n'applique JAMAIS de mise a jour tout seul -- une mise a jour
redemarre le service (meme si les VM actives ne sont pas touchees, voir
app/routers/update.py), pas anodin sans supervision humaine explicite.

Reutilise directement check_update() (meme logique que GET /update/check,
git ou apt selon _install_method()) plutot que de dupliquer la detection.
Notifie via le point d'entree unique de audit.py::log_action() (chantier
28) UNE SEULE FOIS par version distante detectee (update_check_state,
table a une ligne) -- pas a chaque cycle horaire tant que personne n'a
applique la mise a jour, sinon un webhook/email par heure indefiniment.
"""
import threading
import time
from datetime import datetime, timezone

from app.core.audit import log_action
from app.core.database import get_conn

CHECK_INTERVAL_S = 3600  # meme cadence que vm_cleanup.py -- une derive de version se compte en heures/jours, pas besoin de plus frequent


def _get_last_notified():
    with get_conn() as db:
        row = db.execute("SELECT last_notified_version FROM update_check_state WHERE id = 1").fetchone()
        return row["last_notified_version"] if row else None


def _mark_notified(version):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as db:
        db.execute(
            """
            INSERT INTO update_check_state (id, last_notified_version, last_checked_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET last_notified_version = excluded.last_notified_version,
                                           last_checked_at = excluded.last_checked_at
            """,
            (version, now),
        )
        db.commit()


def _touch_checked_at():
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as db:
        db.execute(
            """
            INSERT INTO update_check_state (id, last_checked_at)
            VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET last_checked_at = excluded.last_checked_at
            """,
            (now,),
        )
        db.commit()


def check_once():
    from app.routers.update import check_update  # import tardif : evite un cycle au chargement du module

    result = check_update(user={"role": "admin"})
    _touch_checked_at()

    if not result.get("verifiable") or result.get("a_jour"):
        return

    remote = result.get("commit_distant")
    if not remote or _get_last_notified() == remote:
        return  # pas de version distante exploitable, ou deja notifie pour CETTE version

    msg = f"Nouvelle version Hyperlite disponible : {remote} (version actuelle : {result.get('commit_local')})"
    log_action("system", "update_available", "hyperlite", "succes", msg)
    _mark_notified(remote)


def _loop():
    while True:
        try:
            check_once()
        except Exception as e:
            print(f"[update_check] cycle échoué : {e!r}", flush=True)
        time.sleep(CHECK_INTERVAL_S)


def start_update_check_scheduler():
    threading.Thread(target=_loop, daemon=True, name="update-auto-check").start()
