"""Per-node fencing settings (design docs/design/ha-automatic.md, step 1).

Fencing is how HA will one day prove that a failed node is really off before a protected VM is restarted
elsewhere. Methods:
  - "ipmi"       : the node's BMC over IPMI (iLO, iDRAC, XCC, Supermicro...), with the fence_ipmilan agent;
  - "redfish"    : the same BMCs over Redfish (HTTPS), fence_redfish;
  - "amt"        : Intel AMT on vPro PCs (HP EliteDesk, Lenovo ThinkCentre...), fence_amt_ws;
  - "lease_only" : no power control; the node is considered stopped only once its storage leases expired.

This module never powers anything off. Its only action is a **status** query ("Test fencing"), which tells the
administrator whether the credentials and the network path to the BMC work. The fence agents come from the
`fence-agents` package; they are run without a shell, and their options, the password included, are passed on
standard input, so they never show in the process list. The password is stored encrypted (secrets_crypto) and
never returned by the API.
"""

import ipaddress
import logging
import re
import shutil
import subprocess
from datetime import UTC, datetime

from app.core import secrets_crypto
from app.core.database import get_conn

logger = logging.getLogger(__name__)

AGENTS = {"ipmi": "fence_ipmilan", "redfish": "fence_redfish", "amt": "fence_amt_ws"}
METHODS = (*AGENTS, "lease_only")
HOST_RE = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)
USER_RE = re.compile(r"^[A-Za-z0-9_.@\\-]{1,64}$")
TEST_TIMEOUT_S = 40


class FencingError(Exception):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.message = message
        self.status = status


def _valid_host(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return bool(HOST_RE.match(value or ""))


def validate(methode, adresse, port, utilisateur):
    """None, or why these settings are refused. Everything here ends up in a fence agent's options."""
    if methode not in METHODS:
        return f"Unknown fencing method '{methode}' (expected one of: {', '.join(METHODS)})"
    if methode == "lease_only":
        return None
    if not adresse or not _valid_host(adresse):
        return "The BMC address must be a host name or an IP address"
    if port is not None and not 1 <= int(port) <= 65535:
        return "The port must be between 1 and 65535"
    if not utilisateur or not USER_RE.match(utilisateur):
        return "The BMC user name may contain letters, digits and . _ @ - (64 characters at most)"
    return None


def get(node):
    """The settings of a node without the password ("secret_defini" says whether one is stored), or None."""
    with get_conn() as db:
        row = db.execute("SELECT * FROM node_fencing WHERE node = ?", (node,)).fetchone()
    if not row:
        return None
    out = dict(row)
    out["secret_defini"] = bool(out.pop("secret", None))
    out["tls_non_verifie"] = bool(out["tls_non_verifie"])
    return out


def list_all():
    with get_conn() as db:
        nodes = [r["node"] for r in db.execute("SELECT node FROM node_fencing ORDER BY node")]
    return [get(n) for n in nodes]


def save(
    node, methode, adresse=None, port=None, utilisateur=None, secret=None, tls_non_verifie=False, username="system"
):
    """Create or update. secret None keeps the stored password (the UI never sees it to send it back)."""
    error = validate(methode, adresse, port, utilisateur)
    if error:
        raise FencingError(error)
    now = datetime.now(UTC).isoformat()
    with get_conn() as db:
        existing = db.execute("SELECT secret FROM node_fencing WHERE node = ?", (node,)).fetchone()
        stored = secrets_crypto.encrypt(secret) if secret else (existing["secret"] if existing else None)
        if methode == "lease_only":
            adresse = port = utilisateur = stored = None
        elif not stored:
            raise FencingError("A password is needed for this method")
        db.execute(
            "INSERT INTO node_fencing (node, methode, adresse, port, utilisateur, secret, tls_non_verifie, modifie_par, modifie_le) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(node) DO UPDATE SET methode=excluded.methode, "
            "adresse=excluded.adresse, port=excluded.port, utilisateur=excluded.utilisateur, secret=excluded.secret, "
            "tls_non_verifie=excluded.tls_non_verifie, modifie_par=excluded.modifie_par, modifie_le=excluded.modifie_le",
            (node, methode, adresse, port, utilisateur, stored, int(bool(tls_non_verifie)), username, now),
        )
        db.commit()
    return get(node)


def delete(node):
    with get_conn() as db:
        cur = db.execute("DELETE FROM node_fencing WHERE node = ?", (node,))
        db.commit()
    return cur.rowcount > 0


def _agent_input(row, action):
    """The fence agent options, one "name=value" per line on stdin (the agents' documented stdin interface)."""
    lines = [f"action={action}", f"ip={row['adresse']}", f"username={row['utilisateur']}"]
    lines.append(f"password={secrets_crypto.decrypt(row['secret'])}")
    if row["port"]:
        lines.append(f"ipport={int(row['port'])}")
    if row["methode"] == "ipmi":
        lines.append("lanplus=1")  # IPMI 2.0: every BMC of the last fifteen years, and the only encrypted mode
    if row["methode"] in ("redfish", "amt"):
        lines.append("ssl=1")
        if row["tls_non_verifie"]:
            lines.append("ssl_insecure=1")
    return "\n".join(lines) + "\n"


def test(node):
    """Query the power state through the fence agent. Never powers anything off.
    Returns {"ok", "alimentation": "on"|"off"|None, "detail"}."""
    with get_conn() as db:
        row = db.execute("SELECT * FROM node_fencing WHERE node = ?", (node,)).fetchone()
    if row is None:
        raise FencingError("No fencing is set for this node", 404)
    if row["methode"] == "lease_only":
        return {"ok": True, "alimentation": None, "detail": "Leases only: there is no power control to test"}
    agent = AGENTS[row["methode"]]
    path = shutil.which(agent)
    if path is None:
        return {
            "ok": False,
            "alimentation": None,
            "detail": f"{agent} is not installed on this host: apt install fence-agents",
        }
    try:
        proc = subprocess.run(
            [path], input=_agent_input(row, "status"), capture_output=True, text=True, timeout=TEST_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "alimentation": None,
            "detail": f"No answer from {row['adresse']} within {TEST_TIMEOUT_S} s",
        }
    except OSError:
        # The details stay in the service log: an OS error text is not for the API.
        logger.warning("Fence agent %s could not run for %s", agent, node, exc_info=True)
        return {
            "ok": False,
            "alimentation": None,
            "detail": f"{agent} could not run on this host (see the service log)",
        }
    out = (proc.stdout or "") + (proc.stderr or "")
    state = re.search(r"Status:\s*(ON|OFF)", out, re.IGNORECASE)
    if state:
        return {"ok": True, "alimentation": state.group(1).lower(), "detail": f"Power state read through {agent}"}
    # Keep the agent's own words, without anything that could echo the password back.
    secret = secrets_crypto.decrypt(row["secret"]) or ""
    message = " ".join(line.strip() for line in out.splitlines() if line.strip())[-300:]
    if secret:
        message = message.replace(secret, "***")
    return {"ok": False, "alimentation": None, "detail": message or f"{agent} failed (exit code {proc.returncode})"}
