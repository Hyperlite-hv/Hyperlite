"""Creating and joining a cluster from the dashboard, as Proxmox's "Create cluster" and "Join cluster".

Every node of a cluster runs Corosync, hyperlite-cfs in cluster mode and Hyperlite; all are equal. What the members
share lives in hyperlite-cfs:
- CONF_PATH: the cluster's name, its config version and its members (name, Corosync node id, address), from which
  each node writes its own /etc/corosync/corosync.conf;
- COROSYNC_KEY_ENTRY (under /priv, root only): Corosync's key, so any member can let a new node in;
- SSH_DIR/<node>: each member's cluster SSH key (app/core/cluster.py), which every member authorizes, so any node
  reaches any other.

Creating: Corosync's key and configuration are written with this node alone, hyperlite-cfs restarts in cluster mode,
this node's configuration is copied into it, and the node registers itself in the shared nodes table.

Joining, from the new node: the administrator copies the join information of a member (its address, the fingerprint
of its HTTPS certificate, and a one-time ticket valid TICKET_TTL_S) and pastes it into the new node. The new node calls
the member over HTTPS, checking the certificate against the fingerprint, and sends the ticket, its name, address and
SSH key. The member adds it to the cluster and answers with what the new node needs: the cluster's configuration,
Corosync's key and the keys of .env that decrypt the shared secrets and sign the sessions. The new node then drops
its own configuration: its hyperlite-cfs database is moved aside (a node joining with its own tree could otherwise be
chosen as the source of the cluster's state), its outbox is emptied at the next start, and the cluster's tree is
applied to its SQLite (app/repositories/cfs/inbound.py). Hyperlite restarts on it to take the new keys.

Every step that runs a command takes fixed arguments; names, addresses and keys are validated first.
"""

import base64
import contextlib
import hashlib
import hmac
import http.client
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import ssl
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core import self_node
from app.core.cfs_client import CfsError, NotFound

logger = logging.getLogger(__name__)

COROSYNC_CONF = Path("/etc/corosync/corosync.conf")
COROSYNC_KEY = Path("/etc/corosync/authkey")
COROSYNC_BIN = "/usr/sbin/corosync"
CFS_DEFAULTS = Path("/etc/default/hyperlite-cfs")
CFS_DB = Path("/var/lib/hyperlite-cfs/config.db")
PORT = int(os.environ.get("HYPERLITE_PORT", "8000"))

CONF_PATH = "/cluster/corosync.json"
COROSYNC_KEY_ENTRY = "/priv/cluster/authkey"
SSH_DIR = "/cluster/ssh"

TICKET_SETTING = "cluster_join_ticket"  # rows each node keeps for itself (app/repositories/cfs/tables.py)
JOIN_SETTING = "cfs_join_pending"
TICKET_TTL_S = 1800
WAIT_S = 60

CLUSTER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,14}$")
SSH_KEY_RE = re.compile(r"^ssh-ed25519 [A-Za-z0-9+/]{68}={0,2}( [A-Za-z0-9@._-]{1,64})?$")
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
TICKET_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
MARKER = "hyperlite-cluster-member"  # ends the authorized_keys lines this module manages


class ClusterError(Exception):
    """A sentence for the administrator: what is wrong and what to do."""


# ---- Small helpers, replaced in the tests ----


def _run(args, timeout=60):
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("%s: %s", " ".join(args), e)
        return False
    if done.returncode:
        logger.warning("%s failed: %s", " ".join(args), (done.stderr or done.stdout).strip()[:500])
    return done.returncode == 0


def _local_addresses():
    """The IP addresses configured on this host."""
    try:
        out = subprocess.run(["ip", "-j", "addr", "show"], capture_output=True, text=True, timeout=10, check=True)
        return {a["local"] for link in json.loads(out.stdout) for a in link.get("addr_info", []) if a.get("local")}
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as e:
        logger.warning("Local addresses unknown: %s", e)
        return set()


def _client():
    from app.repositories.cfs import shadow

    return shadow._get_client()


def _settings():
    from app.repositories.sqlite.settings import SqliteSettingsStore

    return SqliteSettingsStore()


def _write(path, data, mode):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data if isinstance(data, bytes) else data.encode())
    tmp.chmod(mode)
    tmp.replace(path)


def _now():
    return datetime.now(UTC)


# ---- Validation ----


def _address(value, local=False):
    try:
        addr = str(ipaddress.ip_address(str(value).strip()))
    except ValueError:
        raise ClusterError(f"'{value}' is not an IP address") from None
    if local and addr not in _local_addresses():
        raise ClusterError(f"{addr} is not an address of this node: choose the address of its cluster network")
    return addr


def _cluster_name(value):
    value = str(value or "").strip()
    if not CLUSTER_NAME_RE.match(value):
        raise ClusterError("Invalid cluster name (letters, digits and -, 1 to 15 characters)")
    return value


def _node_name(value):
    value = str(value or "").strip().lower()
    if not self_node.NAME_RE.match(value) or value == self_node.LOCAL:
        raise ClusterError(f"Invalid node name '{value}'")
    return value


# ---- Corosync's configuration ----


def render(conf):
    """corosync.conf for the shared configuration: knet, encrypted and authenticated with the cluster's key."""
    nodes = "".join(
        "  node {\n"
        f"    name: {m['nom']}\n"
        f"    nodeid: {int(m['nodeid'])}\n"
        "    quorum_votes: 1\n"
        f"    ring0_addr: {m['adresse']}\n"
        "  }\n"
        for m in sorted(conf["membres"], key=lambda m: int(m["nodeid"]))
    )
    return (
        "# Written by Hyperlite from the cluster's shared configuration: change it from the dashboard.\n"
        "totem {\n"
        "  version: 2\n"
        f"  cluster_name: {conf['cluster']}\n"
        f"  config_version: {int(conf['version'])}\n"
        "  transport: knet\n"
        "  crypto_cipher: aes256\n"
        "  crypto_hash: sha256\n"
        "}\n"
        f"nodelist {{\n{nodes}}}\n"
        "quorum {\n  provider: corosync_votequorum\n}\n"
        "logging {\n  to_syslog: yes\n}\n"
    )


def _check_conf(conf):
    """A configuration from the tree or from another node, checked before anything is written from it."""
    try:
        checked = {"cluster": _cluster_name(conf["cluster"]), "version": int(conf["version"]), "membres": []}
        for m in conf["membres"]:
            checked["membres"].append(
                {"nom": _node_name(m["nom"]), "nodeid": int(m["nodeid"]), "adresse": _address(m["adresse"])}
            )
    except (KeyError, TypeError, ValueError):
        raise ClusterError("The cluster's configuration is not valid") from None
    if not checked["membres"] or not 0 < checked["version"] < 2**31:
        raise ClusterError("The cluster's configuration is not valid")
    ids = [m["nodeid"] for m in checked["membres"]]
    if len(set(ids)) != len(ids) or len({m["nom"] for m in checked["membres"]}) != len(ids):
        raise ClusterError("The cluster's configuration is not valid")
    return checked


def _local_version():
    try:
        match = re.search(r"^\s*config_version:\s*(\d+)", COROSYNC_CONF.read_text(), re.MULTILINE)
    except OSError:
        return 0
    return int(match.group(1)) if match else 0


def shared_conf():
    try:
        return _check_conf(json.loads(_client().get(CONF_PATH).data))
    except NotFound:
        return None


# ---- State ----


def installed():
    return os.path.exists(COROSYNC_BIN)


def state():
    from app.core import cluster_lead

    mode = cluster_lead.mode()
    out = {"corosync_installe": installed(), "en_cluster": mode == "cluster", "nom": None, "membres": [], "noeud": None}
    with contextlib.suppress(ValueError):  # an invalid HYPERLITE_NODE_NAME: the page still opens
        out["noeud"] = self_node.name()
    if mode != "cluster":
        return out
    try:
        conf = shared_conf()
        status = _client().status()
    except ClusterError:
        out["erreur"] = "The cluster's configuration in hyperlite-cfs is not valid"
        return out
    except (CfsError, OSError) as e:
        logger.warning("Cluster state unknown: %s", e)
        out["erreur"] = "hyperlite-cfs does not answer"
        return out
    out["quorum"] = status.quorate
    if conf:
        out["nom"] = conf["cluster"]
        out["membres"] = conf["membres"]
    return out


# ---- Creating ----


def _wait_for_cluster():
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        try:
            status = _client().status()
            if status.mode == "cluster" and status.quorate:
                return
        except Exception as e:  # not up yet
            logger.debug("hyperlite-cfs not ready: %s", e)
        time.sleep(1)
    raise ClusterError("hyperlite-cfs did not come up in cluster mode: see journalctl -u corosync -u hyperlite-cfs")


def _preflight():
    from app.core import cluster_lead
    from app.repositories.cfs import shadow

    if not installed():
        raise ClusterError("Corosync is not installed on this node: run apt install corosync, then try again")
    if not os.path.exists(shadow.BINARY):
        raise ClusterError("hyperlite-cfs is not installed on this node: its build failed at the last upgrade")
    if cluster_lead.mode() == "cluster":
        raise ClusterError("This node is already in a cluster")


def _start_cluster_services():
    from app.repositories.cfs import shadow

    _write(CFS_DEFAULTS, "HYPERLITE_CFS_MODE=--cluster\n", 0o644)
    for args in (
        ["systemctl", "enable", "corosync"],
        ["systemctl", "restart", "corosync"],
        ["systemctl", "enable", shadow.SERVICE],
        ["systemctl", "restart", shadow.SERVICE],
    ):
        if not _run(args):
            raise ClusterError(f"'{' '.join(args)}' failed: see journalctl -u corosync -u hyperlite-cfs")


def _backup(path):
    if path.exists():
        stamp = _now().strftime("%Y%m%dT%H%M%SZ")
        shutil.copy2(path, path.with_name(f"{path.name}.before-hyperlite-{stamp}"))


def create(name, address, username):
    """Make this node the first member of a new cluster."""
    from app.core import cluster, cluster_lead
    from app.core.audit import log_action
    from app.repositories.cfs import shadow

    name = _cluster_name(name)
    address = _address(address, local=True)
    _preflight()
    me = self_node.name()
    conf = {"cluster": name, "version": 1, "membres": [{"nom": me, "nodeid": 1, "adresse": address}]}
    key = secrets.token_bytes(256)

    _backup(COROSYNC_CONF)
    _write(COROSYNC_KEY, key, 0o400)
    _write(COROSYNC_CONF, render(conf), 0o644)
    _start_cluster_services()
    cluster_lead.forget()
    _wait_for_cluster()
    _settings().set_app_setting(shadow.SETTING, "1")
    cluster_lead.forget()
    shadow.seed()  # this node's configuration becomes the cluster's
    client = _client()
    client.put(CONF_PATH, json.dumps(conf).encode())
    client.put(COROSYNC_KEY_ENTRY, base64.b64encode(key))
    client.put(f"{SSH_DIR}/{shadow.component(me)}", cluster.get_cluster_pubkey().encode())
    _register(me, address)
    log_action(username, "cluster_create", name, "succes", f"{me} ({address})")
    return state()


def _register(node, address):
    """The shared nodes table lists every member; each node leaves its own row out (app/repositories/sqlite/nodes.py)."""
    from app.domain.common import AlreadyExists
    from app.domain.node import NodeSpec
    from app.repositories.sqlite.nodes import SqliteNodeStore

    with contextlib.suppress(AlreadyExists):
        SqliteNodeStore().create(NodeSpec(name=node, hostname=address), _now().isoformat(), "en_ligne")


# ---- Letting a node in ----


def _certificate_fingerprint():
    from app.core.tls_certs import TLS_DIR

    pem = (TLS_DIR / "hyperlite.crt").read_text()
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()


def join_information():
    """What the administrator pastes into the new node. It lets one node in, within TICKET_TTL_S: it is a secret."""
    conf = shared_conf()
    if not conf:
        raise ClusterError("This node is not in a cluster")
    me = self_node.name()
    member = next((m for m in conf["membres"] if m["nom"] == me), None)
    if not member:
        raise ClusterError("This node is not listed among the cluster's members")
    ticket = secrets.token_urlsafe(32)
    expires = _now() + timedelta(seconds=TICKET_TTL_S)
    stored = {"sha256": hashlib.sha256(ticket.encode()).hexdigest(), "expire": expires.isoformat()}
    _settings().set_app_setting(TICKET_SETTING, json.dumps(stored))
    info = {
        "cluster": conf["cluster"],
        "adresse": member["adresse"],
        "port": PORT,
        "empreinte": _certificate_fingerprint(),
        "ticket": ticket,
    }
    return {
        "information": base64.urlsafe_b64encode(json.dumps(info).encode()).decode(),
        "cluster": conf["cluster"],
        "expire": expires.isoformat(),
    }


def _take_ticket(ticket):
    """True once for the ticket of the last join information, before it expires."""
    raw = _settings().app_setting(TICKET_SETTING)
    if not raw or not isinstance(ticket, str) or not TICKET_RE.match(ticket):
        return False
    try:
        stored = json.loads(raw)
        valid = hmac.compare_digest(stored["sha256"], hashlib.sha256(ticket.encode()).hexdigest())
        fresh = datetime.fromisoformat(stored["expire"]) > _now()
    except (ValueError, KeyError, TypeError):
        return False
    if valid:
        _settings().set_app_setting(TICKET_SETTING, "")  # one node per ticket
    return valid and fresh


_members_lock = threading.Lock()


def accept_member(ticket, name, address, ssh_key):
    """Run on a member, called by the joining node with the ticket of the join information."""
    from app.core.audit import log_action
    from app.core.config_copy import _read_keys
    from app.repositories.cfs import shadow

    with _members_lock:  # one node per ticket, even when two calls race
        if not _take_ticket(ticket):
            raise PermissionError("The join information is not valid or has expired: copy it again")
        name = _node_name(name)
        address = _address(address)
        if not isinstance(ssh_key, str) or not SSH_KEY_RE.match(ssh_key.strip()):
            raise ClusterError("Invalid SSH key")
        client = _client()
        conf = shared_conf()
        if not conf:
            raise ClusterError("This node is not in a cluster")
        if any(m["nom"] == name for m in conf["membres"]):
            raise ClusterError(f"A member of the cluster is already named {name}")
        if any(m["adresse"] == address for m in conf["membres"]):
            raise ClusterError(f"A member of the cluster already uses {address}")
        conf["membres"].append(
            {"nom": name, "nodeid": max(m["nodeid"] for m in conf["membres"]) + 1, "adresse": address}
        )
        conf["version"] += 1
        client.put(f"{SSH_DIR}/{shadow.component(name)}", ssh_key.strip().encode())
        client.put(CONF_PATH, json.dumps(conf).encode())
        sync()  # this node's Corosync knows the new member before it calls
        _register(name, address)
    keys = {k: v for k, v in _read_keys().items() if v}
    log_action("system", "cluster_join", name, "succes", f"{name} ({address}) joined {conf['cluster']}")
    return {
        "configuration": conf,
        "cle_corosync": client.get(COROSYNC_KEY_ENTRY).data.decode(),
        "cles": keys,
        "cles_ssh": _member_keys(client),
    }


def _member_keys(client):
    keys = {}
    try:
        children = client.list(SSH_DIR)
    except NotFound:
        return keys
    for child in children:
        if not child.is_dir:
            keys[child.name] = client.get(f"{SSH_DIR}/{child.name}").data.decode().strip()
    return keys


# ---- Joining ----


def _decode_information(text):
    try:
        info = json.loads(base64.urlsafe_b64decode(str(text).strip().encode() + b"==="))
        checked = {
            "cluster": _cluster_name(info["cluster"]),
            "adresse": _address(info["adresse"]),
            "port": int(info["port"]),
            "empreinte": str(info["empreinte"]).lower(),
            "ticket": str(info["ticket"]),
        }
    except (ValueError, KeyError, TypeError):
        raise ClusterError("The join information is not valid: copy it again from a member of the cluster") from None
    if not 0 < checked["port"] < 65536 or not FINGERPRINT_RE.match(checked["empreinte"]):
        raise ClusterError("The join information is not valid: copy it again from a member of the cluster")
    if not TICKET_RE.match(checked["ticket"]):
        raise ClusterError("The join information is not valid: copy it again from a member of the cluster")
    return checked


def _call_member(info, body):
    """POST to the member's /cluster/membres over HTTPS, trusting only the certificate of the join information."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # the certificate is checked against the fingerprint below, before any data
    conn = http.client.HTTPSConnection(info["adresse"], info["port"], timeout=120, context=context)
    try:
        conn.connect()
        der = conn.sock.getpeercert(binary_form=True)
        if not hmac.compare_digest(hashlib.sha256(der).hexdigest(), info["empreinte"]):
            raise ClusterError(
                "The member's certificate does not match the join information: copy it again from the member"
            )
        conn.request("POST", "/cluster/membres", json.dumps(body), {"Content-Type": "application/json"})
        response = conn.getresponse()
        data = response.read(1_000_000)
    except (OSError, http.client.HTTPException) as e:
        logger.warning("Joining through %s failed: %s", info["adresse"], e)
        raise ClusterError(f"The member {info['adresse']} cannot be reached on port {info['port']}") from None
    finally:
        conn.close()
    try:
        answer = json.loads(data)
    except ValueError:
        raise ClusterError("The member answered something unexpected") from None
    if response.status != 200:
        detail = answer.get("detail") if isinstance(answer, dict) else None
        raise ClusterError(f"The member refused: {detail}" if isinstance(detail, str) else "The member refused")
    return answer


def _write_env_keys(keys):
    from app.core.config_copy import KEY_NAMES, _env_path

    keys = {
        k: v
        for k, v in keys.items()
        if k in KEY_NAMES and isinstance(v, str) and re.match(r"^[A-Za-z0-9_=+/-]{16,200}$", v)
    }
    if set(keys) != set(KEY_NAMES):
        raise ClusterError("The member did not send the cluster's keys")
    env = _env_path()
    lines = env.read_text().splitlines() if env.exists() else []
    kept = [line for line in lines if line.partition("=")[0].strip() not in keys]
    _write(env, "\n".join(kept + [f"{k}={v}" for k, v in keys.items()]) + "\n", 0o600)


def _restart_service():
    from app.routers.update import _spawn_outside_service

    _spawn_outside_service("hyperlite-cluster-restart", ["bash", "-c", "sleep 2 && systemctl restart hyperlite"])


def join(information, address, confirmation, username):
    """Make this node a member of the cluster the join information comes from. Its own configuration is replaced by
    the cluster's: confirmation must be the cluster's name."""
    from app.core import cluster
    from app.core.audit import log_action
    from app.repositories.cfs import shadow

    info = _decode_information(information)
    if confirmation != info["cluster"]:
        raise ClusterError(f"Type the cluster's name, {info['cluster']}, to confirm")
    address = _address(address, local=True)
    _preflight()
    me = self_node.name()
    answer = _call_member(
        info, {"ticket": info["ticket"], "nom": me, "adresse": address, "cle_ssh": cluster.get_cluster_pubkey()}
    )
    try:
        conf = _check_conf(answer["configuration"])
        key = base64.b64decode(answer["cle_corosync"], validate=True)
        member_keys = answer.get("cles_ssh") or {}
        keys = answer["cles"]
    except (KeyError, TypeError, ValueError):
        raise ClusterError("The member answered something unexpected") from None
    if not any(m["nom"] == me and m["adresse"] == address for m in conf["membres"]) or not 128 <= len(key) <= 4096:
        raise ClusterError("The member answered something unexpected")

    # From here on, this node's configuration gives way to the cluster's. Its own keys and database stay usable until
    # Corosync and hyperlite-cfs are up in the cluster: only then are the cluster's keys written.
    _authorize(member_keys, me)
    _backup(COROSYNC_CONF)
    _write(COROSYNC_KEY, key, 0o400)
    _write(COROSYNC_CONF, render(conf), 0o644)
    _run(["systemctl", "stop", shadow.SERVICE])
    stamp = _now().strftime("%Y%m%dT%H%M%SZ")
    for suffix in ("", "-wal", "-shm"):
        path = Path(f"{CFS_DB}{suffix}")
        if path.exists():
            path.replace(path.with_name(f"{path.name}.before-join-{stamp}"))
    _start_cluster_services()
    _wait_for_cluster()
    settings = _settings()
    settings.set_app_setting(shadow.SETTING, "1")
    settings.set_app_setting(JOIN_SETTING, "1")  # the next start empties the outbox (finish_join)
    _write_env_keys(keys)
    log_action(username, "cluster_join", conf["cluster"], "succes", f"{me} ({address})")
    _restart_service()
    return {"cluster": conf["cluster"], "redemarrage": True}


def finish_join(db):
    """Run by init_db: after a join, nothing this node had before goes out to the cluster, and the cluster's tree is
    applied from its start (app/repositories/cfs/inbound.py)."""
    row = db.execute("SELECT valeur FROM app_settings WHERE cle = ?", (JOIN_SETTING,)).fetchone()
    if not row or row[0] != "1":
        return
    db.execute("DELETE FROM cfs_outbox")
    db.execute("DELETE FROM app_settings WHERE cle IN (?, ?)", (JOIN_SETTING, "cfs_applied_version"))
    logger.info("Joined a cluster: this node's configuration is replaced by the cluster's")


# ---- Removing a member ----


def remove_member(name, confirmation, username):
    """Take a member out for good (it must be off: it would come back with the same key otherwise)."""
    from app.core.audit import log_action
    from app.repositories.cfs import shadow
    from app.repositories.sqlite.nodes import SqliteNodeStore

    name = _node_name(name)
    if self_node.is_self(name):
        raise ClusterError("A node cannot remove itself: remove it from another member")
    if confirmation != name:
        raise ClusterError(f"Type the member's name, {name}, to confirm")
    with _members_lock:
        client = _client()
        conf = shared_conf()
        if not conf or not any(m["nom"] == name for m in conf["membres"]):
            raise ClusterError(f"{name} is not a member of the cluster")
        conf["membres"] = [m for m in conf["membres"] if m["nom"] != name]
        conf["version"] += 1
        client.put(CONF_PATH, json.dumps(conf).encode())
        with contextlib.suppress(NotFound):
            client.delete(f"{SSH_DIR}/{shadow.component(name)}")
        sync()
    SqliteNodeStore().delete(name)
    log_action(username, "cluster_remove_member", name, "succes", conf["cluster"])
    return state()


# ---- Keeping each node in line with the shared configuration ----


def _authorize(member_keys, me):
    """authorized_keys holds the cluster key of every other member, and only theirs among the lines marked MARKER."""
    from app.core.cluster import _authorized_keys_path

    path = _authorized_keys_path()
    lines = path.read_text().splitlines() if path.exists() else []
    kept = [line for line in lines if not line.rstrip().endswith(MARKER)]
    wanted = []
    from app.repositories.cfs import shadow

    for name, key in sorted(member_keys.items()):
        key = str(key).strip()
        if name == shadow.component(me) or not SSH_KEY_RE.match(key):
            continue
        kind, blob = key.split()[:2]
        wanted.append(f"{kind} {blob} {MARKER}")
    new = kept + wanted
    if new != lines:
        _write(path, "\n".join(new) + "\n", 0o600)


def sync():
    """Write corosync.conf when the shared one is newer, and authorize the members' SSH keys. Called in a cluster by
    the lead loop (app/core/cluster_lead.py) and after a member changed."""
    from app.repositories.cfs import shadow

    conf = shared_conf()
    if not conf:
        return False
    changed = False
    if conf["version"] > _local_version():
        _write(COROSYNC_CONF, render(conf), 0o644)
        _run(["corosync-cfgtool", "-R"])
        changed = True
    me = self_node.name()
    client = _client()
    own = f"{SSH_DIR}/{shadow.component(me)}"
    from app.core import cluster

    try:
        client.get(own)
    except NotFound:  # a node of a cluster made before the keys were shared: publish its own
        client.put(own, cluster.get_cluster_pubkey().encode())
    _authorize(_member_keys(client), me)
    return changed
