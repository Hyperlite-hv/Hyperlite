"""Outbound notifications. Every notable event (node failure, HA alert,
update, task failure...) used to live ONLY in the internal audit log: nothing
left the application. Two channel types are supported: a generic webhook (JSON
POST, compatible with Discord, Slack, ntfy or any HTTP receiver) and email
(SMTP). No external dependency: urllib + smtplib from the standard library.

It does NOT send for every log_action() call (unmanageable noise: a user
validation error is not an infrastructure event), only for the `action` values
listed in NOTIFY_EVENTS, curated by hand rather than guessed in advance. Extend
it as needed.

"""

import json
import smtplib
import urllib.error
import urllib.request
from datetime import UTC, datetime
from email.message import EmailMessage

from app.core import secrets_crypto
from app.core.database import get_conn
from app.core.http_safety import require_http_url

NOTIFY_EVENTS = {
    "node_statut_change": "Node state change",
    "ha_alert": "HA alert (protected node down)",
    "hyperlite_update": "Hyperlite update",
    "update_available": "New Hyperlite version available (to be applied)",
    "create_vm": "VM creation",
    "delete_vm": "VM deletion",
    "migrate_vm": "Migration de VM",
    "backup_vm": "VM backup",
    "restore_backup": "Backup restore",
    # "delete_vm" (already above) also covers the automatic deletion of an
    # inactive VM: same notification event, and the message text tells an
    # "automatic deletion" apart from a manual one.
    "auto_cleanup_warning": "Automatic deletion warning (inactive VM)",
}


def _now():
    return datetime.now(UTC).isoformat()


def list_channels():
    """Return the DECRYPTED SMTP password: INTERNAL use only (actual sending through
    send_to_channel/notify). NEVER expose this result as is through the API
    (see app/routers/notifications.py, which redacts the password before
    answering the client)."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM notification_channels ORDER BY id").fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["config"] = json.loads(d["config"])
        if d["type"] == "email" and d["config"].get("smtp_password"):
            d["config"]["smtp_password"] = secrets_crypto.decrypt(d["config"]["smtp_password"])
        d["events"] = json.loads(d["events"])
        d["enabled"] = bool(d["enabled"])
        result.append(d)
    return result


def create_channel(type_, name, config, events, username):
    now = _now()
    config = dict(config)
    if type_ == "email" and config.get("smtp_password"):
        config["smtp_password"] = secrets_crypto.encrypt(config["smtp_password"])
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
    require_http_url(url)
    payload = json.dumps(
        {
            "event": event,
            "title": title,
            "message": message,
            "result": result,
            "source": "hyperlite",
            "ts": _now(),
        }
    ).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")  # noqa: S310 -- URL scheme validated by require_http_url() or a constant https URL
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 -- scheme validated by require_http_url
        if resp.status >= 300:
            raise RuntimeError(f"HTTP response {resp.status}")


def _send_email(config, title, message, event, result):
    required = ["smtp_host", "smtp_port", "from_addr", "to_addr"]
    missing = [k for k in required if not config.get(k)]
    if missing:
        raise ValueError(f"Champs manquants : {', '.join(missing)}")

    msg = EmailMessage()
    msg["Subject"] = f"[Hyperlite] {title}"
    msg["From"] = config["from_addr"]
    msg["To"] = config["to_addr"]
    msg.set_content(f"{message}\n\n-- \nEvent: {event}\nResult: {result}\nHyperlite")

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
    """Send to ONE specific channel. Also used by the UI's "Test" button
    (event='test', never filtered by NOTIFY_EVENTS)."""
    sender = _SENDERS.get(channel["type"])
    if not sender:
        raise ValueError(f"Unknown channel type: {channel['type']}")
    sender(channel["config"], title, message, event, result)


def notify(event, title, message, result="succes"):
    """Entry point used by the rest of the application (cluster.py, ha.py,
    audit.py...). Fully best-effort: a sending error on ONE channel never
    prevents the others and NEVER raises to the caller (sending a notification
    must never make the operation that triggered it fail)."""
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
            # Late import: audit.py might one day call notify() directly, this avoids a
            # cycle if so.
            from app.core.audit import log_action

            log_action("system", "notification_echec", channel["name"], "echec", str(e)[:300])
