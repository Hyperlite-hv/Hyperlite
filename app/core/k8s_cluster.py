"""Kubernetes clusters on Hyperlite VMs: one k3s server VM and N worker VMs, created, joined and handed over as a
kubeconfig. Containers (Docker/OCI images) are then deployed with kubectl or Helm; nothing is installed on the
hosts themselves.

How a cluster is built:
- The VMs are ordinary Hyperlite VMs (Debian cloud image + cloud-init, see app/routers/vms/create.py), so they show
  in the VM list, back up and migrate like any other. They are named <cluster>-server and <cluster>-worker-<n>.
- k3s is not installed with `curl | sh`. The host downloads the k3s binary and its airgap image bundle once from the
  official GitHub release of the stable channel, checks both against the release's sha256 file, caches them, and
  copies them to each VM. Our own systemd units start the server and the agents. The cluster therefore bootstraps
  on a network without Internet access too (only workloads that pull their images need it).
- Everything runs over the automation SSH key that Hyperlite installs in its VMs (the same access as jobs).
  The join token never appears on a command line: it is sent on standard input.
- The kubeconfig is stored encrypted, rewritten to point at the server VM's address, and served to administrators.

First version: every VM of a cluster is created on the local host, since VM creation is local only.
"""

import hashlib
import json
import logging
import re
import secrets
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime

from app.core.audit import log_action
from app.core.database import get_conn
from app.core.error_messages import describe_exception
from app.core.secrets_crypto import decrypt, encrypt
from app.core.tasks import create_task, finish_task, update_task_progress
from app.core.vm_builder import PROJDIR

logger = logging.getLogger(__name__)

CHANNELS_URL = "https://update.k3s.io/v1-release/channels"
# The k3s project's own releases (a third-party source, unrelated to Hyperlite's APT address).
K3S_RELEASES = "https://github.com/k3s-io/k3s/releases"
RELEASE_URL = K3S_RELEASES + "/download/{tag}/{file}"
ARTIFACTS_DIR = PROJDIR / "data" / "k3s"
BINARY = "k3s"
IMAGES = "k3s-airgap-images-amd64.tar.zst"
CHECKSUMS = "sha256sum-amd64.txt"

VM_USER = "kube"
# Staging directory in the VM user's home (a fixed name under /tmp could be pre-created by another user).
REMOTE_DIR = "hyperlite-k3s"
MAX_WORKERS = 5
SSH_READY_TIMEOUT_S = 600
NODES_READY_TIMEOUT_S = 900
DOWNLOAD_TIMEOUT_S = 1800
STEP_TIMEOUT_S = 900

# A cluster name becomes a VM name prefix: "<name>-worker-5" must remain a valid VM name.
_NAME = re.compile(r"^[a-z][a-z0-9-]{1,40}[a-z0-9]$")
_TAG = re.compile(r"^v\d+\.\d+\.\d+\+k3s\d+$")

_busy = set()
_busy_lock = threading.Lock()


class ClusterError(Exception):
    """A refusal to report as is (bad request, conflict)."""

    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def _now():
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------- records


def _row(r):
    d = dict(r)
    d["workers"] = json.loads(d["workers"] or "[]")
    d.pop("jeton", None)
    d["kubeconfig_disponible"] = bool(d.pop("kubeconfig", None))
    return d


def list_clusters():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM k8s_clusters ORDER BY nom").fetchall()
    return [_row(r) for r in rows]


def get_cluster(name):
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM k8s_clusters WHERE nom = ?", (name,)).fetchone()
    return _row(r) if r else None


def _update(name, **fields):
    cols = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE k8s_clusters SET {cols} WHERE nom = ?", (*fields.values(), name))  # noqa: S608 - fixed column names
        conn.commit()


def get_kubeconfig(name):
    with get_conn() as conn:
        r = conn.execute("SELECT kubeconfig FROM k8s_clusters WHERE nom = ?", (name,)).fetchone()
    if not r:
        raise ClusterError(f"Cluster '{name}' not found", 404)
    if not r["kubeconfig"]:
        raise ClusterError(f"Cluster '{name}' has no kubeconfig yet", 409)
    return decrypt(r["kubeconfig"])


def recover_interrupted():
    """At start-up: a cluster left 'creation' or 'suppression' by a restart will never finish; say so."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE k8s_clusters SET statut = 'echec', erreur = 'Interrupted by a restart of Hyperlite' "
            "WHERE statut IN ('creation', 'suppression')"
        )
        conn.commit()


def vm_names(name, workers):
    return f"{name}-server", [f"{name}-worker-{i}" for i in range(1, workers + 1)]


# ---------------------------------------------------------------- k3s artifacts


def _http_get(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - fixed https URLs
        return r.read()


def stable_version():
    data = json.loads(_http_get(CHANNELS_URL))
    for channel in data.get("data", []):
        if channel.get("id") == "stable" and _TAG.match(channel.get("latest") or ""):
            return channel["latest"]
    raise RuntimeError("Could not read the k3s stable version")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_artifacts(tag):
    """Directory holding the verified k3s binary and airgap images for this release, downloaded once."""
    if not _TAG.match(tag):
        raise RuntimeError(f"Unexpected k3s version '{tag}'")
    folder = ARTIFACTS_DIR / tag
    folder.mkdir(parents=True, exist_ok=True)
    quoted = urllib.parse.quote(tag, safe="")
    sums = {}
    for line in _http_get(RELEASE_URL.format(tag=quoted, file=CHECKSUMS)).decode().splitlines():
        parts = line.split()
        if len(parts) == 2:
            sums[parts[1]] = parts[0]
    for name in (BINARY, IMAGES):
        expected = sums.get(name)
        if not expected:
            raise RuntimeError(f"{name} is not in the checksums of k3s {tag}")
        dest = folder / name
        if dest.exists() and _sha256(dest) == expected:
            continue
        part = folder / f".{name}.part"
        url = RELEASE_URL.format(tag=quoted, file=name)
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_S) as r, open(part, "wb") as out:  # noqa: S310
            while chunk := r.read(1 << 20):
                out.write(chunk)
        if _sha256(part) != expected:
            part.unlink(missing_ok=True)
            raise RuntimeError(f"Checksum mismatch for {name} of k3s {tag}: download refused")
        part.replace(dest)
    (folder / BINARY).chmod(0o755)
    return folder


# ---------------------------------------------------------------- SSH to the cluster VMs


def _ssh_target(vm):
    from app.core.jobs import _resolve_vm_ssh

    ip, user, key = _resolve_vm_ssh(vm)
    opts = [
        "-i", str(key),
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "LogLevel=ERROR",
    ]  # fmt: skip
    return ip, user, opts


def vm_run(vm, script, secret="", timeout=STEP_TIMEOUT_S):
    """Run a bash script on a VM. The first line of standard input is read into $HL_SECRET, the rest is the script:
    neither the secret nor the script shows on a command line."""
    ip, user, opts = _ssh_target(vm)
    r = subprocess.run(
        ["ssh", *opts, f"{user}@{ip}", "bash -c 'read -r HL_SECRET; export HL_SECRET; exec bash -s'"],
        input=f"{secret}\n{script}",
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if r.returncode != 0:
        raise RuntimeError(f"{vm}: {(r.stderr or r.stdout).strip()[-400:] or f'exit code {r.returncode}'}")
    return r.stdout


def vm_copy(vm, local_paths, remote_dir):
    ip, user, opts = _ssh_target(vm)
    r = subprocess.run(
        ["scp", *opts, *map(str, local_paths), f"{user}@{ip}:{remote_dir}/"],
        capture_output=True,
        text=True,
        timeout=STEP_TIMEOUT_S,
    )
    if r.returncode != 0:
        raise RuntimeError(f"{vm}: copy failed: {r.stderr.strip()[-300:]}")


def wait_ssh(vm, timeout=SSH_READY_TIMEOUT_S):
    """Wait until the VM answers over SSH and cloud-init has finished; return its address."""
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            vm_run(vm, "cloud-init status --wait >/dev/null 2>&1 || true", timeout=300)
            return _ssh_target(vm)[0]
        except Exception as e:  # not up yet: no address, SSH refused, cloud-init still running
            last = str(e)
            time.sleep(5)
    raise RuntimeError(f"{vm} did not become reachable over SSH: {last}")


# ---------------------------------------------------------------- k3s setup

_UNIT = """[Unit]
Description=Kubernetes (k3s {role}, set up by Hyperlite)
Wants=network-online.target
After=network-online.target

[Service]
Type={type}
ExecStart=/usr/local/bin/k3s {args}
KillMode=process
Delegate=yes
LimitNOFILE=1048576
LimitNPROC=infinity
LimitCORE=infinity
TasksMax=infinity
TimeoutStartSec=0
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target
"""

_INSTALL = """set -euo pipefail
D="$HOME/{remote_dir}"
sudo install -m 755 "$D/k3s" /usr/local/bin/k3s
sudo mkdir -p /var/lib/rancher/k3s/agent/images /etc/rancher/k3s
sudo install -m 644 "$D/{images}" /var/lib/rancher/k3s/agent/images/
for c in kubectl crictl ctr; do sudo ln -sf /usr/local/bin/k3s /usr/local/bin/$c; done
printf '%s' "$HL_SECRET" | sudo tee /etc/rancher/k3s/hyperlite-token >/dev/null
sudo chmod 600 /etc/rancher/k3s/hyperlite-token
sudo tee /etc/systemd/system/{service}.service >/dev/null <<'UNIT'
{unit}UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now {service}
rm -rf "$D"
"""


_IFACE = re.compile(r"^[A-Za-z0-9_.@-]{1,32}$")


def interface_of(vm, ip):
    """Name of the VM's interface that holds `ip`. flannel picks its interface from the default route, and a VM
    on an isolated network has none: k3s then stops with "unable to find default route". Naming it avoids that."""
    out = vm_run(vm, f"ip -o -4 addr show | awk '$4 ~ /^{re.escape(ip)}\\//{{print $2; exit}}'", timeout=60).strip()
    name = out.split("@")[0]
    if not _IFACE.match(name):
        raise RuntimeError(f"{vm}: no network interface holds {ip}")
    return name


def install_k3s(vm, folder, token, role, ip, server_ip=None):
    iface = interface_of(vm, ip)
    vm_run(vm, f"mkdir -p ~/{REMOTE_DIR}")
    vm_copy(vm, [folder / BINARY, folder / IMAGES], REMOTE_DIR)
    common = f"--token-file /etc/rancher/k3s/hyperlite-token --node-ip {ip} --flannel-iface {iface}"
    if role == "server":
        args, service, kind = f"server {common} --tls-san {ip} --write-kubeconfig-mode 0600", "k3s", "notify"
    else:
        args, service, kind = f"agent --server https://{server_ip}:6443 {common}", "k3s-agent", "exec"
    unit = _UNIT.format(role=role, type=kind, args=args)
    vm_run(vm, _INSTALL.format(remote_dir=REMOTE_DIR, images=IMAGES, service=service, unit=unit), secret=token)


def wait_nodes_ready(server_vm, expected, timeout=NODES_READY_TIMEOUT_S):
    script = (
        "sudo k3s kubectl get nodes -o "
        'jsonpath=\'{range .items[*]}{.status.conditions[?(@.type=="Ready")].status}{"\\n"}{end}\''
    )
    deadline = time.monotonic() + timeout
    ready = 0
    while time.monotonic() < deadline:
        try:
            ready = vm_run(server_vm, script, timeout=60).split().count("True")
            if ready >= expected:
                return
        except RuntimeError as e:  # the API server is still starting
            logger.debug("k3s not answering yet on %s: %s", server_vm, e)
        time.sleep(10)
    raise RuntimeError(f"Only {ready} of {expected} Kubernetes nodes became Ready")


def fetch_kubeconfig(server_vm, server_ip, name):
    raw = vm_run(server_vm, "sudo cat /etc/rancher/k3s/k3s.yaml", timeout=60)
    # k3s writes its loopback address; clients outside the VM need the server VM's own address.
    raw = re.sub(r"https://127\.0\.0\.1:(\d+)", rf"https://{server_ip}:\1", raw)
    return re.sub(r"(?m)^(\s*(?:-\s+)?(?:name|cluster|user|current-context):\s*)default\s*$", rf"\g<1>{name}", raw)


# ---------------------------------------------------------------- create / delete


def precheck(name, workers, vcpu, memory_mb, disk_gb, network):
    import libvirt

    from app.core.libvirt_utils import open_conn
    from app.core.vm_limits import validate_vm_resources

    if not _NAME.match(name or ""):
        raise ClusterError(
            "Cluster name: 3 to 42 characters, lowercase letters, digits and dashes, starting with a letter"
        )
    if not 1 <= workers <= MAX_WORKERS:
        raise ClusterError(f"Workers: between 1 and {MAX_WORKERS}")
    if memory_mb < 1024:
        raise ClusterError("Memory: at least 1024 MB per node for k3s")
    if disk_gb < 10:
        raise ClusterError("Disk: at least 10 GB per node (the image bundle alone takes about 1 GB unpacked)")
    errors = validate_vm_resources(vcpu, memory_mb, [disk_gb])
    if errors:
        raise ClusterError("; ".join(errors))
    if get_cluster(name):
        raise ClusterError(f"A cluster named '{name}' already exists", 409)
    server, agents = vm_names(name, workers)
    conn = open_conn()
    try:
        for vm in (server, *agents):
            try:
                conn.lookupByName(vm)
                raise ClusterError(f"A VM named '{vm}' already exists", 409)
            except libvirt.libvirtError:
                pass
        try:
            net = conn.networkLookupByName(network)
        except libvirt.libvirtError:
            raise ClusterError(f"Network '{network}' not found") from None
        if not net.isActive():
            raise ClusterError(f"Network '{network}' is not started")
    finally:
        conn.close()


def _create_vm(vm, vcpu, memory_mb, disk_gb, network, password, username):
    from app.routers.vms.create import VMCreate, _create_vm

    payload = VMCreate(
        name=vm,
        vcpu=vcpu,
        memory_mb=memory_mb,
        disks=[{"size_gb": disk_gb}],
        network=network,
        username=VM_USER,
        password=password,
    )
    _create_vm(payload, {"username": username})


def _start_vm(vm):
    from app.core.libvirt_utils import open_conn

    conn = open_conn()
    try:
        domain = conn.lookupByName(vm)
        if not domain.isActive():
            domain.create()
    finally:
        conn.close()


def start_create(name, workers, vcpu, memory_mb, disk_gb, network, username):
    precheck(name, workers, vcpu, memory_mb, disk_gb, network)
    with _busy_lock:
        if name in _busy:
            raise ClusterError(f"Cluster '{name}' is already being created", 409)
        _busy.add(name)
    server, agents = vm_names(name, workers)
    task_id = create_task("create_k8s_cluster", name, username=username)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO k8s_clusters (nom, reseau, serveur, workers, vcpu, memoire_mo, disque_go, statut, task_id, "
            "cree_par, cree_le) VALUES (?, ?, ?, ?, ?, ?, ?, 'creation', ?, ?, ?)",
            (name, network, server, json.dumps(agents), vcpu, memory_mb, disk_gb, task_id, username, _now()),
        )
        conn.commit()
    threading.Thread(
        target=_provision,
        args=(name, server, agents, vcpu, memory_mb, disk_gb, network, username, task_id),
        daemon=True,
    ).start()
    return task_id


def _provision(name, server, agents, vcpu, memory_mb, disk_gb, network, username, task_id):
    step = "preparing"
    try:
        step = "downloading k3s"
        tag = stable_version()
        _update(name, version=tag)
        folder = ensure_artifacts(tag)
        update_task_progress(task_id, 15)

        step = "creating the VMs"
        token = secrets.token_hex(32)
        _update(name, jeton=encrypt(token))
        password = secrets.token_urlsafe(24)
        for i, vm in enumerate((server, *agents)):
            _create_vm(vm, vcpu, memory_mb, disk_gb, network, password, username)
            _start_vm(vm)
            update_task_progress(task_id, 15 + int(20 * (i + 1) / (len(agents) + 1)))

        step = "waiting for the VMs"
        ips = {vm: wait_ssh(vm) for vm in (server, *agents)}
        update_task_progress(task_id, 45)

        step = "installing the k3s server"
        install_k3s(server, folder, token, "server", ips[server])
        wait_nodes_ready(server, 1)
        update_task_progress(task_id, 65)

        step = "joining the workers"
        for i, vm in enumerate(agents):
            install_k3s(vm, folder, token, "agent", ips[vm], server_ip=ips[server])
            update_task_progress(task_id, 65 + int(20 * (i + 1) / len(agents)))
        wait_nodes_ready(server, len(agents) + 1)

        step = "reading the kubeconfig"
        kubeconfig = fetch_kubeconfig(server, ips[server], name)
        _update(name, statut="pret", erreur=None, adresse=ips[server], kubeconfig=encrypt(kubeconfig))
        finish_task(task_id, "termine")
        log_action(username, "create_k8s_cluster", name, "succes", task_id=task_id)
    except Exception as e:
        msg = f"{step}: {describe_exception(e)}"
        logger.warning("Kubernetes cluster '%s' failed while %s", name, msg)
        _update(name, statut="echec", erreur=msg)
        finish_task(task_id, "echec", msg)
        log_action(username, "create_k8s_cluster", name, "echec", msg, task_id=task_id)
    finally:
        with _busy_lock:
            _busy.discard(name)


def delete_cluster(name, username):
    """Delete a cluster's VMs (stopped first, disks included) and its record."""
    import libvirt

    from app.core.libvirt_utils import open_conn
    from app.routers.vms.lifecycle import delete_vm

    cluster = get_cluster(name)
    if not cluster:
        raise ClusterError(f"Cluster '{name}' not found", 404)
    with _busy_lock:
        if name in _busy:
            raise ClusterError(f"Cluster '{name}' is being created: wait for it to finish or fail", 409)
        _busy.add(name)
    task_id = create_task("delete_k8s_cluster", name, username=username)
    try:
        _update(name, statut="suppression")
        conn = open_conn()
        try:
            for vm in (cluster["serveur"], *cluster["workers"]):
                try:
                    domain = conn.lookupByName(vm)
                except libvirt.libvirtError:
                    continue
                if domain.isActive():
                    domain.destroy()
                delete_vm(vm, confirm=True, node=None, user={"username": username})
        finally:
            conn.close()
        with get_conn() as db:
            db.execute("DELETE FROM k8s_clusters WHERE nom = ?", (name,))
            db.commit()
        finish_task(task_id, "termine")
        log_action(username, "delete_k8s_cluster", name, "succes", task_id=task_id)
    except Exception as e:
        msg = describe_exception(e) if not hasattr(e, "detail") else str(e.detail)
        _update(name, statut="echec", erreur=f"deletion: {msg}")
        finish_task(task_id, "echec", msg)
        log_action(username, "delete_k8s_cluster", name, "echec", msg, task_id=task_id)
        raise
    finally:
        with _busy_lock:
            _busy.discard(name)
