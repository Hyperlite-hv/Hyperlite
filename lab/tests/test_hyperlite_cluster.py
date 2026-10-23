"""Hyperlite from its package on three Debian VMs (lab/vm.sh hyperlite), a cluster formed through the API as an
administrator forms it, and what only real members show: a request naming another member (?node=) is done by that
member, an ISO upload is relayed to it, a VM is made on one member and migrated to another, and the Terraform provider
manages objects of every member through one node.

    sudo env HYPERLITE_LAB_DEB=installer/hyperlite_X_amd64.deb [HYPERLITE_LAB_PROVIDER=dir-of-the-provider-binary] \\
        venv/bin/python -m pytest -q -s lab/tests

The VMs sit on the lab's own NAT network; their certificates are self-signed, so the checks of this test do not verify
them (the members pin each other's, which is what is tested).
"""

import hashlib
import json
import os
import secrets
import shlex
import shutil
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

LAB = Path(__file__).resolve().parents[1] / "vm.sh"
DEB = os.environ.get("HYPERLITE_LAB_DEB")
PROVIDER = os.environ.get("HYPERLITE_LAB_PROVIDER")
DIR = Path(os.environ.get("HYPERLITE_LAB_DIR") or tempfile.mkdtemp(prefix="hyperlite-lab-"))
NODES = (1, 2, 3)
PASSWORD = "Lab-" + secrets.token_urlsafe(12)
CLUSTER = "lab"

pytestmark = pytest.mark.skipif(
    not DEB or os.geteuid() != 0, reason="needs root and HYPERLITE_LAB_DEB (the package to install on the VMs)"
)

_insecure = ssl.create_default_context()
_insecure.check_hostname = False
_insecure.verify_mode = ssl.CERT_NONE


def ip(n):
    return f"192.0.2.{n}"


def on(n, *command):
    """A command on node n, as root; its output. ssh joins its arguments into one line for the remote shell: each one
    is quoted, or `sh -c "a && b"` would run `sh -c a` and the rest beside it."""
    key = DIR / "id_ed25519"
    ssh = ["ssh", "-i", str(key), "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]
    ssh += ["-o", "LogLevel=ERROR", f"debian@{ip(n)}", shlex.join(["sudo", *command])]
    return subprocess.run(ssh, check=True, capture_output=True, text=True, timeout=600).stdout


def main_pid(n):
    """The process of node n's API, as systemd knows it ("0" while it is stopped)."""
    return on(n, "systemctl", "show", "--property=MainPID", "--value", "hyperlite").strip()


def api(n, method, path, body=None, token=None, form=None, content_type=None, timeout=120):
    """(status, decoded body) of a call to node n's API."""
    headers = {"Accept": "application/json"}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if form is not None:
        data, headers["Content-Type"] = form, content_type
    elif body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    prefix = "" if path == "/health" else "/api/v1"  # /health is served at the root, for load balancers
    url = f"https://{ip(n)}:8000{prefix}{path}"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, context=_insecure, timeout=timeout) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, raw.decode("utf-8", "replace")


def ok(result, *expected):
    status, body = result
    assert status in (expected or (200, 201, 202)), f"{status}: {body}"
    return body


def login(n, password):
    form = urllib.parse.urlencode({"username": "admin", "password": password}).encode()
    status, body = api(n, "POST", "/auth/login", form=form, content_type="application/x-www-form-urlencoded")
    return status, body


def signed_in(n, password):
    """A session token on node n, or None while its API is still starting (the connection refused)."""
    try:
        return (login(n, password)[1] or {}).get("access_token")
    except OSError:
        return None


def multipart(name, data):
    boundary = secrets.token_hex(12)
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    return head + data + f"\r\n--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def wait(what, check, timeout=300, every=3):
    deadline = time.monotonic() + timeout
    while True:
        last = check()
        if last:
            return last
        if time.monotonic() > deadline:
            raise AssertionError(f"{what}: not after {timeout} s")
        time.sleep(every)


def finished(n, token, task_id, node=None, timeout=900):
    """Follow a task through node n, on the member that runs it."""
    suffix = f"?node={node}" if node else ""

    def done():
        task = ok(api(n, "GET", f"/tasks/{task_id}{suffix}", token=token))
        return task if task["status"] not in ("pending", "running") else None

    task = wait(f"task {task_id}", done, timeout=timeout, every=5)
    assert task["status"] == "succeeded", task.get("error") or task  # the error first: the report cuts the task
    return task


@pytest.fixture(scope="module")
def lab():
    DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(["bash", str(LAB), "hyperlite", DEB, str(DIR)], check=True)
    try:
        yield
    finally:
        # The logs stay in DIR/nN/ (the workflow keeps them when a test failed).
        subprocess.run(["bash", str(LAB), "down", str(DIR)], check=False)


@pytest.fixture(scope="module")
def cluster(lab):
    """The three nodes in one cluster, formed as an administrator does: the first creates it, the others join with the
    information it gives. Returns the session token of each node and the members' names."""
    tokens = {}
    for n in NODES:
        initial = on(n, "cat", "/root/.hyperlite-initial-password").strip()
        token = ok(login(n, initial))["access_token"]
        ok(api(n, "POST", "/auth/me/password", {"current_password": initial, "new_password": PASSWORD}, token))
        tokens[n] = ok(login(n, PASSWORD))["access_token"]
    names = {n: ok(api(n, "GET", "/health", token=tokens[n]))["node"] for n in NODES}
    assert len(set(names.values())) == 3, names

    def quorate(members):
        """The cluster of `members` nodes has the quorum, as the Cluster page shows it before a node is added."""
        status, state = api(1, "GET", "/cluster", token=tokens[1])
        return status == 200 and len(state.get("members", [])) == members and state.get("quorum")

    ok(api(1, "POST", "/cluster/create", {"name": CLUSTER, "address": ip(1)}, tokens[1]))
    for n in (2, 3):
        began = time.monotonic()
        wait(f"the quorum before node {n} joins", lambda n=n: quorate(n - 1), timeout=120, every=1)
        print(f"[lab] quorum with {n - 1} node(s) after {time.monotonic() - began:.1f} s")
        info = ok(api(1, "POST", "/cluster/adhesion", token=tokens[1]))["information"]
        body = {"information": info, "address": ip(n), "confirmation": CLUSTER}
        before = main_pid(n)
        status, answer = api(
            n, "POST", "/cluster/join", body, tokens[n], timeout=420
        )  # the join waits up to 300 s (JOIN_WAIT_S)
        assert status == 200, (
            f"node {n} could not join: {status} {answer}; cluster: {api(1, 'GET', '/cluster', token=tokens[1])}"
        )
        # The answer comes first, the restart on the cluster's configuration a few seconds later: a call made in
        # between reaches the process about to stop.
        assert answer.get("restart"), answer
        wait(
            f"node {n} restarted after joining", lambda n=n, b=before: main_pid(n) not in (b, "0"), timeout=60, every=1
        )

    wait("a cluster of three", lambda: quorate(3))
    # Each joining node restarted on the cluster's configuration: sign in again where the session ended.
    for n in NODES:
        tokens[n] = wait(f"node {n} after joining", lambda n=n: signed_in(n, PASSWORD), timeout=120, every=1)
    return {"tokens": tokens, "names": names}


def test_a_network_is_made_read_and_removed_on_the_member_it_names(cluster):
    t, b = cluster["tokens"], cluster["names"][2]
    body = {"name": "labnet", "mode": "isolated", "subnet_address": "10.98.2.1", "dhcp_start": "10.98.2.10",
            "dhcp_end": "10.98.2.50"}  # fmt: skip
    ok(api(1, "POST", f"/networks?node={b}", body, t[1]))
    assert api(2, "GET", "/networks/labnet", token=t[2])[0] == 200  # made on node 2 itself
    assert api(1, "GET", "/networks/labnet", token=t[1])[0] == 404  # not on node 1
    assert ok(api(1, "GET", f"/networks/labnet?node={b}", token=t[1]))["name"] == "labnet"
    ok(api(1, "DELETE", f"/networks/labnet?node={b}&confirm=true", token=t[1]))
    assert api(2, "GET", "/networks/labnet", token=t[2])[0] == 404


def test_root_drives_another_member_with_hyperlite_admin(cluster):
    """hyperlite-admin as root on node 1, with no token (docs/design/admin-cli.md): the package installed it, the
    daemon signs root@<node 1> in, node 2 does what it is asked and its audit log names root of node 1."""
    t, names = cluster["tokens"], cluster["names"]
    me, b = f"root@{names[1]}", names[2]
    listed = json.loads(on(1, "hyperlite-admin", "vm", "list", "--cluster", "--json"))
    assert listed["unreachable"] == [], listed
    # A range of its own: the provider's test makes 10.98.3.0/24 after this one.
    on(1, "hyperlite-admin", "post", "/networks", "--node", b, "name=labcli", "mode=isolated",
       "subnet_address=10.98.9.1", "dhcp_start=10.98.9.10", "dhcp_end=10.98.9.50")  # fmt: skip
    try:
        assert api(2, "GET", "/networks/labcli", token=t[2])[0] == 200  # made on node 2 itself
        shown = json.loads(on(1, "hyperlite-admin", "network", "show", "labcli", "--node", b, "--json"))
        assert shown["name"] == "labcli"

        def entries():
            return ok(api(2, "GET", "/audit?resource=labcli", token=t[2]))["items"]

        # The audit log is written by a thread of its own (app/core/audit.py): the entry comes a moment later.
        try:
            wait(f"node 2's audit log naming {me}", lambda: any(e["username"] == me for e in entries()), 30, 1)
        except AssertionError as e:
            raise AssertionError(f"{e}; what node 2 recorded for labcli: {entries()}") from None
    finally:
        on(1, "hyperlite-admin", "delete", "/networks/labcli", "--node", b, "confirm==true")
    assert api(2, "GET", "/networks/labcli", token=t[2])[0] == 404
    # api reads the description the package ships next to the code.
    assert on(1, "hyperlite-admin", "api", "/vms/x/migrate").startswith("POST /vms/{name}/migrate")


def test_a_node_that_is_no_member_is_refused(cluster):
    t = cluster["tokens"]
    status, body = api(1, "GET", "/networks?node=pve-nowhere", token=t[1])
    assert status == 409 and "not a member" in body["detail"]


def test_an_iso_upload_goes_to_the_member_it_names(cluster):
    t, c = cluster["tokens"], cluster["names"][3]
    image = os.urandom(5 << 20)  # above the 1 MB a form keeps in memory: read back from the spool on disk
    form, content_type = multipart("lab.iso", image)
    ok(api(1, "POST", f"/isos?node={c}", form=form, content_type=content_type, token=t[1], timeout=600))
    [item] = [i for i in ok(api(3, "GET", "/isos", token=t[3]))["items"] if i["name"] == "lab.iso"]
    path = f"{item['location']}/{item['name']}"  # the location is the ISO directory of the node
    assert on(3, "sha256sum", path).split()[0] == hashlib.sha256(image).hexdigest()
    assert all(i["name"] != "lab.iso" for i in ok(api(1, "GET", "/isos", token=t[1]))["items"])
    ok(api(1, "DELETE", f"/isos/lab.iso?node={c}&confirm=true", token=t[1]))


def test_a_vm_is_made_on_a_member_and_migrated_to_another(cluster):
    t, names = cluster["tokens"], cluster["names"]
    b, c = names[2], names[3]
    if not nested(2):
        pytest.skip("no nested virtualization on this host: the VMs of the lab cannot run a VM of their own")
    form, content_type = multipart("boot.iso", b"\0" * (1 << 20))
    ok(api(1, "POST", f"/isos?node={b}", form=form, content_type=content_type, token=t[1]))
    spec = {"name": "labvm", "vcpu": 1, "memory_mb": 256, "disks": [{"size_gb": 1}], "iso": "boot.iso"}
    started = ok(api(1, "POST", f"/vms?node={b}&wait=false", spec, t[1]))
    finished(1, t[1], started["task_id"], node=b)
    assert api(2, "GET", "/vms/labvm", token=t[2])[0] == 200
    assert api(1, "GET", "/vms/labvm", token=t[1])[0] == 404
    # A live migration (the only kind there is; a stopped VM goes by export and import), its disk copied along.
    if ok(api(1, "GET", f"/vms/labvm?node={b}", token=t[1]))["state"] != "active":
        ok(api(1, "POST", f"/vms/labvm/start?node={b}", token=t[1]))
        wait("labvm running", lambda: ok(api(1, "GET", f"/vms/labvm?node={b}", token=t[1]))["state"] == "active")
    moved = ok(api(1, "POST", f"/vms/labvm/migrate?node={b}", {"target_node": c}, t[1]))
    if moved.get("task_id"):
        finished(1, t[1], moved["task_id"], node=b)
    wait("labvm on node 3", lambda: api(3, "GET", "/vms/labvm", token=t[3])[0] == 200)
    assert api(2, "GET", "/vms/labvm", token=t[2])[0] == 404
    assert ok(api(1, "GET", f"/vms/labvm?node={c}", token=t[1]))["state"] == "active"  # still running, there
    ok(api(1, "POST", f"/vms/labvm/stop?node={c}&force=true", token=t[1]))  # a running VM is not deleted
    wait("labvm stopped", lambda: ok(api(1, "GET", f"/vms/labvm?node={c}", token=t[1]))["state"] == "stopped")
    ok(api(1, "DELETE", f"/vms/labvm?node={c}&confirm=true", token=t[1]))


TERRAFORM_HEAD = """
terraform {{
  required_providers {{
    hyperlite = {{ source = "hyperlite-hv/hyperlite" }}
  }}
}}

provider "hyperlite" {{
  server      = "https://{server}:8000"
  fingerprint = "{fingerprint}"
}}
"""


def terraform_on_node_1(t, tmp_path):
    """terraform(*args) in tmp_path, the provider of HYPERLITE_LAB_PROVIDER talking to node 1 with an API token; the
    configuration is tmp_path/main.tf, next to the provider block written here (provider.tf)."""
    token = ok(api(1, "POST", "/auth/tokens", {"name": f"terraform-lab-{tmp_path.name}", "expires_days": 1}, t[1]))
    # The form the dashboard shows and the provider takes: hexadecimal, no colons.
    fingerprint = on(1, "sh", "-c", "openssl x509 -in /etc/hyperlite/local/hyperlite.crt -outform DER | sha256sum")
    fingerprint = fingerprint.split()[0].upper()
    (tmp_path / "rc").write_text(f'provider_installation {{\n  dev_overrides {{\n    "hyperlite-hv/hyperlite" = "{PROVIDER}"\n  }}\n  direct {{}}\n}}\n')  # fmt: skip
    (tmp_path / "provider.tf").write_text(TERRAFORM_HEAD.format(server=ip(1), fingerprint=fingerprint))
    env = {
        **os.environ,
        "TF_CLI_CONFIG_FILE": str(tmp_path / "rc"),
        "HYPERLITE_TOKEN": token["token"],
        "CHECKPOINT_DISABLE": "1",
    }

    def terraform(*args):
        return subprocess.run(["terraform", f"-chdir={tmp_path}", *args], env=env, capture_output=True, text=True)

    return terraform


needs_terraform = pytest.mark.skipif(
    not PROVIDER or not shutil.which("terraform"), reason="needs HYPERLITE_LAB_PROVIDER and terraform"
)


@needs_terraform
def test_the_provider_manages_every_members_objects_through_one_node(cluster, tmp_path):
    t, names = cluster["tokens"], cluster["names"]
    b, c = names[2], names[3]
    terraform = terraform_on_node_1(t, tmp_path)
    (tmp_path / "image.iso").write_bytes(os.urandom(2 << 20))
    (tmp_path / "main.tf").write_text(f"""
resource "hyperlite_network" "b" {{
  name       = "tfnet"
  node       = "{b}"
  mode       = "isolated"
  address    = "10.98.3.1"
  dhcp_start = "10.98.3.10"
  dhcp_end   = "10.98.3.50"
}}

resource "hyperlite_iso" "c" {{
  name   = "tf.iso"
  node   = "{c}"
  source = "${{path.module}}/image.iso"
}}

data "hyperlite_network" "b" {{
  name = hyperlite_network.b.name
  node = hyperlite_network.b.node
}}
""")
    applied = terraform("apply", "-auto-approve", "-input=false")
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert api(2, "GET", "/networks/tfnet", token=t[2])[0] == 200
    assert any(i["name"] == "tf.iso" for i in ok(api(3, "GET", "/isos", token=t[3]))["items"])
    assert api(1, "GET", "/networks/tfnet", token=t[1])[0] == 404
    plan = terraform("plan", "-detailed-exitcode", "-input=false")
    assert plan.returncode == 0, plan.stdout + plan.stderr  # nothing to change once applied
    destroyed = terraform("destroy", "-auto-approve", "-input=false")
    assert destroyed.returncode == 0, destroyed.stdout + destroyed.stderr
    assert api(2, "GET", "/networks/tfnet", token=t[2])[0] == 404


def nested(n):
    """Whether node n can run a VM of its own (the host gave it nested virtualization)."""
    return on(n, "sh", "-c", "test -c /dev/kvm && echo yes || echo no").strip() == "yes"


@needs_terraform
def test_the_provider_moves_a_vm_to_the_member_its_node_names(cluster, tmp_path):
    """A VM made by Terraform on node 2 through node 1, then its `node` changed to node 3: the provider migrates it,
    live (resource_vm.go, migrate), and the next plan has nothing to change."""
    t, names = cluster["tokens"], cluster["names"]
    b, c = names[2], names[3]
    if not (nested(2) and nested(3)):
        pytest.skip("no nested virtualization on this host: the VMs of the lab cannot run a VM of their own")
    form, content_type = multipart("tfboot.iso", b"\0" * (1 << 20))
    ok(api(1, "POST", f"/isos?node={b}", form=form, content_type=content_type, token=t[1]))
    terraform = terraform_on_node_1(t, tmp_path)

    def vm_on(node):
        (tmp_path / "main.tf").write_text(f"""
resource "hyperlite_vm" "moved" {{
  name             = "tfvm"
  node             = "{node}"
  vcpu             = 1
  memory_mb        = 256
  iso              = "tfboot.iso"
  wait_for_ip      = "0s"
  force_stop       = true
  shutdown_timeout = "10s"
  disk {{ size_gb = 1 }}
  network_interface {{ network = "default" }}
}}
""")
        applied = terraform("apply", "-auto-approve", "-input=false")
        assert applied.returncode == 0, applied.stdout + applied.stderr

    try:
        vm_on(b)
        assert ok(api(2, "GET", "/vms/tfvm", token=t[2]))["state"] == "active"
        assert api(3, "GET", "/vms/tfvm", token=t[3])[0] == 404
        vm_on(c)  # migrated, still running
        assert ok(api(3, "GET", "/vms/tfvm", token=t[3]))["state"] == "active"
        assert api(2, "GET", "/vms/tfvm", token=t[2])[0] == 404
        plan = terraform("plan", "-detailed-exitcode", "-input=false")
        assert plan.returncode == 0, plan.stdout + plan.stderr  # its state read on node 3 matches the configuration
    finally:
        destroyed = terraform("destroy", "-auto-approve", "-input=false")
        api(1, "DELETE", f"/isos/tfboot.iso?node={b}&confirm=true", token=t[1])
    assert destroyed.returncode == 0, destroyed.stdout + destroyed.stderr
    assert api(3, "GET", "/vms/tfvm", token=t[3])[0] == 404
