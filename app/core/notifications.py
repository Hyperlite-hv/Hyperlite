"""Notifications sortantes (chantier 28 de la roadmap vSphere/vCenter,
2026-09-17). Jusqu'ici, tout evenement notable (panne de nœud, alerte HA,
mise a jour, echec de tache...) ne vivait QUE dans l'audit log interne --
rien ne sortait de l'application. Deux canaux geres : webhook generique
(POST JSON -- compatible Discord/Slack/ntfy/n'importe quel receveur HTTP)
et email (SMTP). Pas de dependance externe : urllib + smtplib (stdlib),
pas de nouvelle entree dans requirements.txt.

N'ENVOIE PAS pour chaque appel de log_action() (bruit ingerable -- une
erreur de validation utilisateur n'est pas un evenement d'infrastructure)
: seulement pour les `action` listees dans NOTIFY_EVENTS, curatee
manuellement plutot que devinee a l'avance -- a etendre au besoin.
"""
import json
import smtplib
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage

from app.core.database import get_conn

NOTIFY_EVENTS = {
    "node_statut_change": "Changement d'état d'un nœud",
    "ha_alert": "Alerte HA (nœud protégé tombé)",
    "hyperlite_update": "Mise à jour Hyperlite",
    "create_vm": "Création de VM",
    "delete_vm": "Suppression de VM",
    "migrate_vm": "Migration de VM",
    "backup_vm": "Sauvegarde de VM",
    "restore_backup": "Restauration de sauvegarde",
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def list_channels():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM notification_channels ORDER BY id").fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["config"] = json.loads(d["config"])
        d["events"] = json.loads(d["events"])
        d["enabled"] = bool(d["enabled"])
        result.append(d)
    return result


def create_channel(type_, name, config, events, username):
    now = _now()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO notification_channels (type, name, config, events, enabled, created_by, created_at) "
            "VALUES (?, ?, ?, ?, 1, ?, ?)",
            (type_, name, json.dumps(config), json.dumps(events or []), username, now),
        )
        conn.commit()
        channel_id = cur.lastrowid
    return channel_id


def delete_channel(channel_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM notification_channels WHERE id = ?", (channel_id,))
        conn.commit()


def set_enabled(channel_id, enabled):
    with get_conn() as conn:
        conn.execute("UPDATE notification_channels SET enabled = ? WHERE id = ?", (1 if enabled else 0, channel_id))
        conn.commit()


def _send_webhook(config, title, message, event, result):
    url = config.get("url")
    if not url:
        raise ValueError("URL manquante")
    payload = json.dumps({
        "event": event, "title": title, "message": message, "result": result,
        "source": "hyperlite", "ts": _now(),
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"Réponse HTTP {resp.status}")


def _send_email(config, title, message, event, result):
    required = ["smtp_host", "smtp_port", "from_addr", "to_addr"]
    missing = [k for k in required if not config.get(k)]
    if missing:
        raise ValueError(f"Champs manquants : {', '.join(missing)}")

    msg = EmailMessage()
    msg["Subject"] = f"[Hyperlite] {title}"
    msg["From"] = config["from_addr"]
    msg["To"] = config["to_addr"]
    msg.set_content(f"{message}\n\n-- \nÉvénement : {event}\nRésultat : {result}\nHyperlite")

    host, port = config["smtp_host"], int(config["smtp_port"])
    use_tls = config.get("use_tls", True)
    smtp_cls = smtplib.SMTP_SSL if config.get("ssl") else smtplib.SMTP
    with smtp_cls(host, port, timeout=10) as smtp:
        if use_tls and not config.get("ssl"):
            smtp.starttls()
        if config.get("smtp_user"):
            smtp.login(config["smtp_user"], config.get("smtp_password", ""))
        smtp.send_message(msg)


_SENDERS = {"webhook": _send_webhook, "email": _send_email}


def send_to_channel(channel, title, message, event="test", result="succes"):
    """Envoie sur UN canal precis -- utilise aussi par le bouton 'Tester'
    de l'UI (event='test', jamais filtre par NOTIFY_EVENTS)."""
    sender = _SENDERS.get(channel["type"])
    if not sender:
        raise ValueError(f"Type de canal inconnu : {channel['type']}")
    sender(channel["config"], title, message, event, result)


def notify(event, title, message, result="succes"):
    """Point d'entree utilise par le reste de l'app (cluster.py, ha.py,
    audit.py...) -- best-effort total : une erreur d'envoi sur UN canal
    n'empeche jamais les autres, et ne remonte JAMAIS d'exception a
    l'appelant (l'envoi d'une notification ne doit jamais faire echouer
    l'operation qui l'a declenchee)."""
    if event not in NOTIFY_EVENTS:
        return
    try:
        channels = [c for c in list_channels() if c["enabled"] and (not c["events"] or event in c["events"])]
    except Exception:
        return
    for channel in channels:
        try:
            send_to_channel(channel, title, message, event, result)
        except Exception as e:
            # Import tardif : audit.py pourrait un jour appeler notify()
            # directement, evite un cycle si jamais.
            from app.core.audit import log_action
            log_action("system", "notification_echec", channel["name"], "echec", str(e)[:300])
