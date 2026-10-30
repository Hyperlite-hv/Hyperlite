"""Per-node fencing settings (design docs/design/ha-automatic.md, step 1).

Fencing is how HA will one day prove that a failed node is really off before a protected VM is restarted
elsewhere. Methods:
  - "ipmi"       : the node's BMC over IPMI (iLO, iDRAC, XCC, Supermicro...), with the fence_ipmilan agent;
  - "redfish"    : the same BMCs over Redfish (HTTPS), fence_redfish;
  - "amt"        : Intel AMT on vPro PCs (HP EliteDesk, Lenovo ThinkCentre...), fence_amt_ws;
  - "lease_only" : no power control; the node is considered stopped only once its storage leases expired.

Two actions: a **status** query ("Test fencing"), which tells the administrator whether the credentials and the network
path to the BMC work, and **power_off**, the fence itself: the node powered off, then its power state read until it says
off (docs/design/ha-automatic.md, 4.2). Never a reboot. The fence agents come from the `fence-agents` package; they are
run without a shell, and their options, the password included, are passed on standard input, so they never show in the
process list. The password is stored encrypted (secrets_crypto), never returned by the API, and never in a message.
"""

import ipaddress
import logging
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime

from app.core import secrets_crypto


def _store():
    from app.repositories import registry

    return registry.ha().sync


logger = logging.getLogger(__name__)

AGENTS = {"ipmi": "fence_ipmilan", "redfish": "fence_redfish", "amt": "fence_amt_ws"}
# The Debian 13 package of each agent (the package recommends the first two). Debian packages no fence_amt_ws (its
# WS-MAN library is not in Debian): AMT needs power control of Hyperlite's own (docs/design/ha-automatic.md, step 2b).
PACKAGES = {"ipmi": "fence-agents-ipmilan", "redfish": "fence-agents-redfish", "amt": None}
METHODS = (*AGENTS, "lease_only")
HOST_RE = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)
USER_RE = re.compile(r"^[A-Za-z0-9_.@\\-]{1,64}$")
TEST_TIMEOUT_S = 40
OFF_TIMEOUT_S = 90  # the agent's own "off": it powers off, then waits for the BMC to say so
OFF_CONFIRM_S = 60  # then the power state is read again until off, at most this long
OFF_POLL_S = 5
IPMI_PASSWORD_MAX = 20  # bytes, IPMI 2.0


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


def validate(method, address, port, user):
    """None, or why these settings are refused. Everything here ends up in a fence agent's options."""
    if method not in METHODS:
        return f"Unknown fencing method '{method}' (expected one of: {', '.join(METHODS)})"
    if method == "lease_only":
        return None
    if not address or not _valid_host(address):
        return "The BMC address must be a host name or an IP address"
    if port is not None and not 1 <= int(port) <= 65535:
        return "The port must be between 1 and 65535"
    if not user or not USER_RE.match(user):
        return "The BMC user name may contain letters, digits and . _ @ - (64 characters at most)"
    return None


def get(node):
    """The settings of a node without the password ("secret_set" says whether one is stored), or None."""
    out = _store().fencing(node)
    if not out:
        return None
    out["secret_set"] = bool(out.pop("secret", None))
    out["tls_unverified"] = bool(out["tls_unverified"])
    return out


def list_all():
    return [get(n) for n in _store().fenced_nodes()]


def save(node, method, address=None, port=None, user=None, secret=None, tls_unverified=False, username="system"):
    """Create or update. secret None keeps the stored password (the UI never sees it to send it back)."""
    error = validate(method, address, port, user)
    if error:
        raise FencingError(error)
    if method == "ipmi" and secret and len(secret.encode()) > IPMI_PASSWORD_MAX:
        # ipmitool refuses it ("password is longer than 20 bytes"): the fence would fail only when it is needed.
        raise FencingError(f"An IPMI password is {IPMI_PASSWORD_MAX} bytes at most (IPMI 2.0)")
    now = datetime.now(UTC).isoformat()
    existing = _store().fencing(node)
    stored = secrets_crypto.encrypt(secret) if secret else (existing["secret"] if existing else None)
    if method == "lease_only":
        address = port = user = stored = None
    elif not stored:
        raise FencingError("A password is needed for this method")
    _store().save_fencing(node, method, address, port, user, stored, int(bool(tls_unverified)), username, now)
    return get(node)


def delete(node):
    return _store().delete_fencing(node)


def _agent_input(row, action):
    """The fence agent options, one "name=value" per line on stdin (the agents' documented stdin interface)."""
    lines = [f"action={action}", f"ip={row['address']}", f"username={row['user']}"]
    try:
        lines.append(f"password={secrets_crypto.decrypt(row['secret'])}")
    except secrets_crypto.SecretUnreadable as e:
        raise FencingError(f"{e} (fencing of {row['node']})", 409) from e
    if row["port"]:
        lines.append(f"ipport={int(row['port'])}")
    if row["method"] == "ipmi":
        lines.append("lanplus=1")  # IPMI 2.0: every BMC of the last fifteen years, and the only encrypted mode
    if row["method"] in ("redfish", "amt"):
        lines.append("ssl=1")
        if row["tls_unverified"]:
            lines.append("ssl_insecure=1")
    return "\n".join(lines) + "\n"


def _without_secret(row, text):
    """The agent's own words, without anything that could echo the password back."""
    try:
        secret = secrets_crypto.decrypt(row["secret"]) or ""
    except secrets_crypto.SecretUnreadable:
        secret = ""
    message = " ".join(line.strip() for line in text.splitlines() if line.strip())
    # Replaced before it is cut: a cut inside the secret left its end to read.
    return (message.replace(secret, "***") if secret else message)[-300:]


def _run(row, action, timeout):
    """(output, None) of the node's fence agent run with `action`, or (None, why it could not run)."""
    agent = AGENTS[row["method"]]
    path = shutil.which(agent)
    if path is None:
        package = PACKAGES[row["method"]]
        if package is None:
            return None, f"{agent} is not packaged by Debian 13: AMT power control is not available on this host yet"
        return None, f"{agent} is not installed on this host: apt install {package}"
    try:
        proc = subprocess.run([path], input=_agent_input(row, action), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"No answer from {row['address']} within {timeout} s"
    except OSError:
        # The details stay in the service log: an OS error text is not for the API.
        logger.warning("Fence agent %s could not run for %s", agent, row["node"], exc_info=True)
        return None, f"{agent} could not run on this host (see the service log)"
    out = (proc.stdout or "") + (proc.stderr or "")
    return out if out.strip() else f"{agent} exited with code {proc.returncode}", None


def _power_state(out):
    state = re.search(r"Status:\s*(ON|OFF)", out or "", re.IGNORECASE)
    return state.group(1).lower() if state else None


def test(node):
    """Query the power state through the fence agent. Never powers anything off.
    Returns {"ok", "power": "on"|"off"|None, "detail"}."""
    row = _store().fencing(node)
    if row is None:
        raise FencingError("No fencing is set for this node", 404)
    if row["method"] == "lease_only":
        return {"ok": True, "power": None, "detail": "Leases only: there is no power control to test"}
    agent = AGENTS[row["method"]]
    out, problem = _run(row, "status", TEST_TIMEOUT_S)
    if problem:
        return {"ok": False, "power": None, "detail": problem}
    power = _power_state(out)
    if power:
        return {"ok": True, "power": power, "detail": f"Power state read through {agent}"}
    return {"ok": False, "power": None, "detail": _without_secret(row, out)}


def power_off(node):
    """Power `node` off through its BMC or AMT, then read its power state until it says off: the proof that the copies
    of its VMs are stopped before they are started elsewhere (docs/design/ha-automatic.md, 4.2). Never "reboot": a node
    back on by itself before its VMs are restarted elsewhere is the double start this prevents.
    Returns {"fenced": bool, "detail"}; fenced only once the agent read the power state as off."""
    row = _store().fencing(node)
    if row is None or row["method"] not in AGENTS:
        return {"fenced": False, "detail": "No BMC or AMT is set for this node"}
    agent = AGENTS[row["method"]]
    out, problem = _run(row, "off", OFF_TIMEOUT_S)
    last = problem or _without_secret(row, out)
    deadline = time.monotonic() + OFF_CONFIRM_S
    while True:
        out, problem = _run(row, "status", TEST_TIMEOUT_S)
        if not problem and _power_state(out) == "off":
            return {"fenced": True, "detail": f"Powered off through {agent}, its power state read as off"}
        last = problem or _without_secret(row, out) or last
        if time.monotonic() >= deadline:
            return {"fenced": False, "detail": f"Not confirmed off through {agent} within {OFF_CONFIRM_S} s: {last}"}
        time.sleep(OFF_POLL_S)
