"""Pushing the collected metrics to external metric servers, next to the Prometheus endpoint (GET /metrics).

After each collector tick (app/core/metrics.py) the samples of the host, the VMs and the registered nodes are sent to
every active server: InfluxDB 2 (HTTP line protocol, `/api/v2/write`) or Graphite (plaintext protocol over TCP).
A server that does not answer costs at most its short timeout and never stops the collection: its last error is kept
and shown on the page. The InfluxDB token is stored encrypted (app/core/secrets_crypto.py) and never returned.
"""

import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

from app.core import secrets_crypto
from app.core.database import get_conn
from app.core.http_safety import require_http_url

TIMEOUT_S = 4
FIELDS = ("cpu_pct", "mem_used_mb", "mem_total_mb", "disk_read_bps", "disk_write_bps", "net_rx_bps", "net_tx_bps")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")
IDENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")
PREFIX_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}(\.[A-Za-z0-9_-]{1,32}){0,3}$")
HOST_RE = re.compile(
    r"^(?=.{1,253}$)[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)


class ExportError(ValueError):
    """A server definition refused, with a message for the user."""


# ---- Formats ----


def _tag(value):
    return re.sub(r"([,= ])", r"\\\1", str(value))


def line_protocol(rows, ts):
    """InfluxDB line protocol: one line per sampled object, its values as fields, seconds precision."""
    lines = []
    for scope, target, *values in rows:
        fields = [f"{f}={float(v)}" for f, v in zip(FIELDS, values, strict=True) if v is not None]
        if fields:
            lines.append(f"hyperlite,scope={_tag(scope)},target={_tag(target)} {','.join(fields)} {int(ts)}")
    return "\n".join(lines) + ("\n" if lines else "")


def _path_part(value):
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(value)) or "_"


def graphite_lines(rows, ts, prefix):
    lines = []
    for scope, target, *values in rows:
        for f, v in zip(FIELDS, values, strict=True):
            if v is not None:
                lines.append(f"{prefix}.{_path_part(scope)}.{_path_part(target)}.{f} {float(v)} {int(ts)}")
    return "\n".join(lines) + ("\n" if lines else "")


# ---- Servers ----


def validate(payload):
    """Checked definition from what an administrator typed; raises ExportError."""
    name = (payload.get("nom") or "").strip()
    if not NAME_RE.match(name):
        raise ExportError("Invalid name: letters, digits, spaces, dots, dashes, 64 characters at most")
    kind = payload.get("type")
    clean = {"nom": name, "type": kind, "actif": bool(payload.get("actif", True))}
    if kind == "influxdb":
        try:
            url = require_http_url((payload.get("url") or "").strip().rstrip("/"))
        except ValueError as e:
            raise ExportError(str(e)) from None
        if urllib.parse.urlparse(url).query or urllib.parse.urlparse(url).fragment:
            raise ExportError("The URL is the server's address only, without a query")
        for key, label in (("org", "organization"), ("bucket", "bucket")):
            if not IDENT_RE.match((payload.get(key) or "").strip()):
                raise ExportError(f"Invalid {label}")
        clean |= {"url": url, "org": payload["org"].strip(), "bucket": payload["bucket"].strip()}
    elif kind == "graphite":
        host = (payload.get("hote") or "").strip()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not HOST_RE.match(host):
                raise ExportError("Invalid host: a name or an IP address") from None
        port = payload.get("port") or 2003
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ExportError("The port must be between 1 and 65535")
        prefix = (payload.get("prefixe") or "hyperlite").strip()
        if not PREFIX_RE.match(prefix):
            raise ExportError("Invalid prefix: dot-separated words of letters, digits, dashes and underscores")
        clean |= {"hote": host, "port": port, "prefixe": prefix}
    else:
        raise ExportError("type must be influxdb or graphite")
    return clean


def _public(row):
    d = dict(row)
    d["jeton_defini"] = bool(d.pop("jeton", None))
    d["actif"] = bool(d["actif"])
    return d


def list_servers():
    with get_conn() as conn:
        return [_public(r) for r in conn.execute("SELECT * FROM metric_servers ORDER BY nom").fetchall()]


def get_server(server_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM metric_servers WHERE id = ?", (server_id,)).fetchone()
    return dict(row) if row else None


def save_server(payload, server_id=None):
    clean = validate(payload)
    token = payload.get("jeton")
    if clean["type"] == "influxdb" and not token and server_id is None:
        raise ExportError("InfluxDB needs an API token with write access to the bucket")
    cols = ["nom", "type", "url", "hote", "port", "org", "bucket", "prefixe", "actif"]
    values = [clean.get(c) for c in cols]
    values[cols.index("prefixe")] = clean.get("prefixe") or "hyperlite"
    values[cols.index("actif")] = 1 if clean["actif"] else 0
    with get_conn() as conn:
        clash = conn.execute(
            "SELECT id FROM metric_servers WHERE nom = ? AND id IS NOT ?", (clean["nom"], server_id)
        ).fetchone()
        if clash:
            raise ExportError(f"A metric server named '{clean['nom']}' already exists")
        if server_id is None:
            cur = conn.execute(
                f"INSERT INTO metric_servers ({', '.join(cols)}, jeton) VALUES ({', '.join('?' * (len(cols) + 1))})",  # noqa: S608
                [*values, secrets_crypto.encrypt(token) if token else None],
            )
            server_id = cur.lastrowid
        else:
            conn.execute(
                f"UPDATE metric_servers SET {', '.join(f'{c} = ?' for c in cols)} WHERE id = ?",  # noqa: S608
                [*values, server_id],
            )
            if token:
                conn.execute(
                    "UPDATE metric_servers SET jeton = ? WHERE id = ?", (secrets_crypto.encrypt(token), server_id)
                )
        conn.commit()
    return _public(get_server(server_id))


def delete_server(server_id):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM metric_servers WHERE id = ?", (server_id,))
        conn.commit()
    return cur.rowcount > 0


# ---- Sending ----


def _send_influx(server, body):
    query = urllib.parse.urlencode({"org": server["org"], "bucket": server["bucket"], "precision": "s"})
    url = require_http_url(f"{server['url']}/api/v2/write?{query}")
    req = urllib.request.Request(  # noqa: S310 -- scheme checked by require_http_url
        url,
        data=body.encode(),
        method="POST",
        headers={
            "Authorization": f"Token {secrets_crypto.decrypt(server['jeton'])}",
            "Content-Type": "text/plain; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310
            resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:200].decode(errors="replace")
        raise ExportError(f"InfluxDB answered {e.code}: {detail}") from None


def _send_graphite(server, body):
    with socket.create_connection((server["hote"], server["port"]), timeout=TIMEOUT_S) as sock:
        sock.sendall(body.encode())


def send(server, rows, ts):
    """Send one set of samples to one server; raises on failure."""
    if not rows:
        return
    if server["type"] == "influxdb":
        _send_influx(server, line_protocol(rows, ts))
    else:
        _send_graphite(server, graphite_lines(rows, ts, server["prefixe"]))


def record_result(server_id, error):
    with get_conn() as conn:
        if error is None:
            conn.execute(
                "UPDATE metric_servers SET dernier_envoi = ?, derniere_erreur = NULL WHERE id = ?",
                (datetime.now(UTC).isoformat(timespec="seconds"), server_id),
            )
        else:
            conn.execute("UPDATE metric_servers SET derniere_erreur = ? WHERE id = ?", (str(error)[:300], server_id))
        conn.commit()


def push(rows, ts):
    """Called by the collector after each tick: every active server gets the samples; failures are recorded."""
    with get_conn() as conn:
        servers = [dict(r) for r in conn.execute("SELECT * FROM metric_servers WHERE actif = 1").fetchall()]
    for server in servers:
        try:
            send(server, rows, ts)
            record_result(server["id"], None)
        except Exception as e:
            record_result(server["id"], e)
