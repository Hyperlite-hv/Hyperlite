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
from app.core.http_safety import require_http_url


def _store():
    from app.repositories import registry

    return registry.settings().sync


NOTIFY_EVENTS = {
    "node_statut_change": "Node state change",
    "ha_alert": "HA alert (protected node down)",
    "hyperlite_update": "Hyperlite update",
    "update_available": "New Hyperlite version available (to be applied)",
    "create_vm": "VM creation",
    "delete_vm": "VM deletion",
    "host_reboot_required": "Node reboot needed (updates installed)",
    "vm_crashed": "VM stopped by accident (crash)",
    "migrate_vm": "VM migration",
    "backup_vm": "VM backup",
    "restore_backup": "Backup restore",
    "verify_backup": "Backup verification (a corrupted backup)",
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
    result = []
    for d in _store().channels():
        d["config"] = json.loads(d["config"])
        if d["type"] == "email" and d["config"].get("smtp_password"):
            try:
                d["config"]["smtp_password"] = secrets_crypto.decrypt(d["config"]["smtp_password"])
            except secrets_crypto.SecretUnreadable:
                d["config"]["smtp_password"] = None
                d["config"]["smtp_password_unreadable"] = True
        d["events"] = json.loads(d["events"])
        d["enabled"] = bool(d["enabled"])
        result.append(d)
    return result


def create_channel(type_, name, config, events, username):
    now = _now()
    config = dict(config)
    if type_ == "email" and config.get("smtp_password"):
        config["smtp_password"] = secrets_crypto.encrypt(config["smtp_password"])
    return _store().create_channel(type_, name, json.dumps(config), json.dumps(events or []), username, now)


def delete_channel(channel_id):
    """False when there is no such channel."""
    return _store().delete_channel(channel_id)


def get_channel_type(channel_id):
    row = _store().channel(channel_id)
    return row["type"] if row else None


def update_channel(channel_id, name=None, config=None, events=None, enabled=None, clear_smtp_password=False):
    """Change the given fields of a channel (None leaves a field as is). Returns False
    when there is no such channel.

    `config` replaces the whole configuration, except the SMTP password: the API never
    hands it back, so an edit form cannot resend it, and an empty or missing one keeps
    the stored password unless `clear_smtp_password` is set."""
    row = _store().channel(channel_id)
    if row is None:
        return False
    fields = {}
    if name is not None:
        fields["name"] = name
    if events is not None:
        fields["events"] = json.dumps(events)
    if enabled is not None:
        fields["enabled"] = 1 if enabled else 0
    if config is not None or clear_smtp_password:
        stored = json.loads(row["config"])
        new_config = dict(config) if config is not None else dict(stored)
        # Markers the API adds when it lists channels, never settings.
        for marker in ("smtp_password_set", "smtp_password_unreadable", "redacted"):
            new_config.pop(marker, None)
        if row["type"] == "email":
            if clear_smtp_password:
                new_config.pop("smtp_password", None)
            elif new_config.get("smtp_password"):
                new_config["smtp_password"] = secrets_crypto.encrypt(new_config["smtp_password"])
            elif stored.get("smtp_password"):
                new_config["smtp_password"] = stored["smtp_password"]  # still encrypted
            else:
                new_config.pop("smtp_password", None)
        fields["config"] = json.dumps(new_config)
    _store().update_channel(channel_id, fields)
    return True


def set_enabled(channel_id, enabled):
    return update_channel(channel_id, enabled=enabled)


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
        raise ValueError(f"Missing fields: {', '.join(missing)}")

    if config.get("smtp_password_unreadable"):
        raise ValueError("The stored SMTP password cannot be decrypted (the encryption key changed): enter it again")

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
