"""SPICE consoles (docs/design/spice-console.md): a VM's SPICE display, the short-lived password a ticket sets on it,
and the .vv file virt-viewer opens.

QEMU serves SPICE in TLS only, on 127.0.0.1 of the VM's node (scripts/ensure-spice-tls.sh prepares each node). The
client reaches it through Hyperlite's proxy (app/core/spice_proxy.py) and checks the node's certificate against the CA
and the subject the .vv carries. A SPICE display without a password admits anyone who reaches its port: every display
added here carries a random password that is already expired, and only a ticket sets one that works, for a few
seconds; an open session outlives it.
"""

import os
import re
import secrets
import shlex
import subprocess
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path

import libvirt
from cryptography import x509

TLS_DIR = Path(os.environ.get("HYPERLITE_SPICE_TLS_DIR", "/etc/pki/libvirt-spice"))
QEMU_CONF = Path(os.environ.get("HYPERLITE_QEMU_CONF", "/etc/libvirt/qemu.conf"))
PROXY_PORT = int(os.environ.get("HYPERLITE_SPICE_PROXY_PORT", "3128"))
# Long enough for the browser to download the file and virt-viewer to open its channels (4 to 5 connections at once);
# the session itself outlives it.
TICKET_TTL_S = 30
SSH_TIMEOUT_S = 15
EXPIRED = "1970-01-01T00:00:00"
_SPICE_TLS_ON = re.compile(r"^\s*spice_tls\s*=\s*1\s*(#.*)?$", re.M)
# What a .vv may carry as the proxy's host: a host name or an address, never a line break or a key of its own.
_HOST = re.compile(r"^(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)$")


class SpiceUnavailable(Exception):
    """The node is not set up for SPICE (no TLS in its qemu.conf, or no certificate)."""


def spice_graphics(xml):
    """The domain's SPICE display element, or None."""
    devices_el = ET.fromstring(xml).find("devices")
    return None if devices_el is None else devices_el.find("graphics[@type='spice']")


def add_display(conn, domain):
    """Give a VM a SPICE display next to its VNC one, in its stored configuration (effective at its next start): TLS
    only, on 127.0.0.1, with a random password already expired. The secure XML keeps the other displays' passwords."""
    root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE | libvirt.VIR_DOMAIN_XML_SECURE))
    devices_el = root.find("devices")
    graphics = ET.SubElement(
        devices_el,
        "graphics",
        type="spice",
        autoport="yes",
        defaultMode="secure",
        passwd=secrets.token_urlsafe(24),
        passwdValidTo=EXPIRED,
    )
    ET.SubElement(graphics, "listen", type="address", address="127.0.0.1")
    conn.defineXML(ET.tostring(root, encoding="unicode"))


def set_ticket_password(domain, ttl_s=TICKET_TTL_S):
    """A new SPICE password on the running VM, valid ttl_s seconds; open sessions are kept. Returns it."""
    graphics = spice_graphics(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_SECURE))
    if graphics is None:
        raise SpiceUnavailable("The VM runs without a SPICE display")
    password = secrets.token_urlsafe(24)  # 32 characters: SPICE takes at most 60
    graphics.set("passwd", password)
    graphics.set("passwdValidTo", (datetime.now(UTC) + timedelta(seconds=ttl_s)).strftime("%Y-%m-%dT%H:%M:%S"))
    graphics.set("connected", "keep")
    domain.updateDeviceFlags(ET.tostring(graphics, encoding="unicode"), libvirt.VIR_DOMAIN_AFFECT_LIVE)
    return password


def tls_port(xml):
    """The TLS port QEMU listens on for the running VM's SPICE display, or None."""
    graphics = spice_graphics(xml)
    port = graphics.get("tlsPort") if graphics is not None else None
    return int(port) if port and port.isdigit() and int(port) > 0 else None


def _subject(cert_pem):
    """The certificate's subject as spice-gtk compares it (host-subject): its entries in the certificate's order."""
    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    return ",".join(rdn.rfc4514_string() for rdn in cert.subject.rdns)


def node_tls(node=None):
    """(CA certificate, server certificate subject) of the node a VM runs on (None: this host). Raises SpiceUnavailable
    when SPICE in TLS is not set up there: a VM with a TLS-only SPICE display would not even start."""
    if node:
        return _remote_node_tls(node)
    try:
        enabled = _SPICE_TLS_ON.search(QEMU_CONF.read_text()) is not None
        ca = (TLS_DIR / "ca-cert.pem").read_text()
        server = (TLS_DIR / "server-cert.pem").read_text()
    except OSError as e:
        raise SpiceUnavailable(f"SPICE is not set up on this node ({e.strerror}: {e.filename})") from None
    if not enabled:
        raise SpiceUnavailable(f"SPICE in TLS is not enabled in {QEMU_CONF} on this node")
    return ca, _subject(server)


def _remote_node_tls(node):
    from app.core import node_health
    from app.core.cluster import get_node, node_ssh_options

    target = get_node(node)
    if target is None:
        raise SpiceUnavailable(f"Node '{node}' not found")
    marker = "-----HYPERLITE-SPICE-SERVER-----"
    command = (
        f"grep -Eq '^[[:space:]]*spice_tls[[:space:]]*=[[:space:]]*1' {shlex.quote(str(QEMU_CONF))} || exit 3; "
        f"cat {shlex.quote(str(TLS_DIR / 'ca-cert.pem'))} && echo {marker} && "
        f"cat {shlex.quote(str(TLS_DIR / 'server-cert.pem'))}"
    )
    try:
        r = subprocess.run(
            [
                "ssh",
                *node_ssh_options(["-o", f"ConnectTimeout={node_health.CONNECT_TIMEOUT_S}"]),
                "-p",
                str(target["ssh_port"]),
                f"{target['ssh_user']}@{target['hostname']}",
                command,
            ],
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SpiceUnavailable(f"Node '{node}' does not answer: {e}") from None
    if r.returncode == 3:
        raise SpiceUnavailable(f"SPICE in TLS is not enabled in {QEMU_CONF} on node '{node}'")
    if r.returncode != 0 or marker not in r.stdout:
        raise SpiceUnavailable(f"SPICE is not set up on node '{node}': {(r.stderr or r.stdout).strip()}")
    ca, server = r.stdout.split(marker, 1)
    return ca.strip() + "\n", _subject(server.strip() + "\n")


def proxy_host(host_header):
    """The host the browser reached Hyperlite by (its Host header without the port): virt-viewer reaches the proxy
    there. None when it is not a plain host name or address."""
    host = (host_header or "").strip()
    if host.startswith("["):
        host = host[: host.find("]") + 1] if "]" in host else ""
    elif host.count(":") == 1:
        host = host.split(":", 1)[0]
    return host if host and _HOST.match(host) else None


def vv_file(*, title, ticket, port, password, proxy, ca_pem, subject):
    """The .vv file: virt-viewer connects through the proxy (`host` is the ticket the proxy admits), in TLS, checking
    the node's certificate, and deletes the file once read (it holds the password)."""
    lines = {
        "type": "spice",
        "title": title,
        "host": ticket,
        "tls-port": str(port),
        "password": password,
        "proxy": f"http://{proxy}:{PROXY_PORT}",
        "ca": ca_pem.strip().replace("\n", "\\n"),
        "host-subject": subject,
        "delete-this-file": "1",
        "toggle-fullscreen": "shift+f11",
        "release-cursor": "shift+f12",
    }
    return "[virt-viewer]\n" + "".join(f"{key}={value}\n" for key, value in lines.items())
