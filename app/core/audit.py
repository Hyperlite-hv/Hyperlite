"""Journal d'audit. Voir _AUDIT_QUEUE plus bas pour pourquoi l'ecriture
elle-meme est asynchrone depuis le chantier 31 (2026-09-17)."""
import queue
import threading
from datetime import datetime, timezone
from app.core.database import get_conn
from app.core.tasks import _finish_task_in

# BUG REEL trouve en testant le chantier 29 (2026-09-17), a REELLEMENT
# IMPACTE L'UTILISATEUR EN SESSION ACTIVE (erreurs 500 visibles sur son
# tableau de bord pendant le test) : log_action() est appelee par la
# quasi-totalite des endpoints, MEME les simples GET en lecture seule --
# il n'existe quasiment pas de "lecteur pur" dans cette app, chaque
# requete HTTP est AUSSI une ecriture SQLite. Le mode WAL (chantier 11/13)
# resout la contention LECTEUR-contre-ECRIVAIN, PAS ecrivain-contre-
# ecrivain (un seul ecrivain a la fois, meme en WAL) -- sous plusieurs
# requetes concurrentes, `database is locked` peut ressurgir malgre le
# timeout 30s deja en place (confirme en conditions reelles).
#
# Plutot que d'ecrire directement dans SQLite depuis CHAQUE thread
# appelant (autant de candidats "ecrivain" simultanes que de requetes en
# vol), un unique thread dedie possede l'ecriture de l'audit log : chaque
# appelant depose l'entree sur une file (rapide, non bloquant, jamais de
# SQLite dans le thread de la requete) et repart immediatement ; le thread
# d'ecriture les traite un par un, donc un SEUL ecrivain a la fois pour ce
# chemin -- qui represente l'immense majorite du volume d'ecriture de
# l'app (chaque requete). Ne resout pas TOUTES les ecritures concurrentes
# possibles (d'autres tables sont encore ecrites directement ailleurs),
# mais elimine la source la plus frequente.
#
# maxsize borne : en cas de pic extreme (jamais rencontre en pratique),
# `put_nowait` echoue plutot que de bloquer indefiniment la requete
# appelante -- l'entree d'audit correspondante est alors perdue (loggee
# sur stderr), prefere a un frein sur l'app elle-meme pour un simple
# journal secondaire.
_AUDIT_QUEUE = queue.Queue(maxsize=10000)
_writer_started = False
_writer_lock = threading.Lock()


def _writer_loop():
    while True:
        username, action, resource, result, error_message, ts = _AUDIT_QUEUE.get()
        try:
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO audit_log (timestamp, username, action, resource, result, error_message) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (ts, username, action, resource, result, error_message),
                )
                conn.commit()
        except Exception as e:
            print(f"[audit] écriture échouée (entrée perdue) : {e!r}", flush=True)
        finally:
            _AUDIT_QUEUE.task_done()


def _ensure_writer_started():
    global _writer_started
    if _writer_started:
        return
    with _writer_lock:
        if not _writer_started:
            threading.Thread(target=_writer_loop, daemon=True, name="audit-writer").start()
            _writer_started = True


def log_action(username: str, action: str, resource: str, result: str, error_message: str = None, task_id: str = None):
    """task_id : quand fourni (voir app.core.tasks.create_task), cloture aussi
    la tache correspondante -- reste SYNCHRONE (contrairement a l'ecriture
    d'audit elle-meme, voir plus bas) car des appelants relisent le statut
    de la tache juste apres, une tache "en_cours" pour toujours le temps
    qu'une file se vide serait un vrai regression -- un log_action(...,
    "succes") ou (..., "echec") represente deja la fin de la tache pour
    tous les endpoints synchrones actuels, pas la peine de dupliquer l'appel."""
    if task_id:
        with get_conn() as conn:
            _finish_task_in(conn, task_id, "termine" if result == "succes" else "echec", error_message)
            conn.commit()

    _ensure_writer_started()
    entry = (username, action, resource, result, error_message, datetime.now(timezone.utc).isoformat())
    try:
        _AUDIT_QUEUE.put_nowait(entry)
    except queue.Full:
        print(f"[audit] file pleine, entrée perdue : {entry}", flush=True)

    # Notifications sortantes (chantier 28) : point d'entree UNIQUE plutot
    # que d'appeler notify() a chaque site d'appel de log_action() dans
    # tout le code. Lancee dans un thread separe (pas dans celui de la
    # requete appelante) : notify() fait du RESEAU (webhook/SMTP, jusqu'a
    # 10s de timeout par canal, chantier 28) -- bloquer la reponse HTTP le
    # temps qu'un webhook distant reponde (ou timeout) serait un probleme
    # de robustesse en soi, independant de SQLite.
    from app.core.notifications import notify, NOTIFY_EVENTS
    if action in NOTIFY_EVENTS:
        title = f"{NOTIFY_EVENTS[action]} — {resource}"
        message = error_message or f"{action} sur '{resource}' : {result}"
        threading.Thread(target=notify, args=(action, title, message, result), daemon=True).start()
