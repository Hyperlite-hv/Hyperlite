"""Hyperlite from its package on three Debian VMs (lab/vm.sh hyperlite), a cluster formed through the API as an
administrator forms it, and what only real members show: a request naming another member (?node=) is done by that
member, an ISO upload is relayed to it, a VM is made on one member and migrated to another, and the Terraform provider
manages objects of every member through one node.

    sudo env HYPERLITE_LAB_DEB=installer/hyperlite_X_amd64.deb [HYPERLITE_LAB_PROVIDER=dir-of-the-provider-binary] \\
        venv/bin/python -m pytest -q -s lab/tests

The VMs sit on the lab's own NAT network; their certificates are self-signed, so the checks of this test do not verify
them (the members pin each other's, which is what is tested).
"""

import csv
import hashlib
import io
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
# The consoles' WebSockets: websockets' sync client reads its socket in a thread while the test's thread writes to it.
# In TLS 1.3 the server's session tickets arrive after the handshake, and one OpenSSL connection read and written by
# two threads at once stalled the client before its HTTP request (the node's socket: handshake done, nothing more
# received) or failed with "[SSL] internal error". TLS 1.2 sends nothing after the handshake.
_insecure_ws = ssl.create_default_context()
_insecure_ws.check_hostname = False
_insecure_ws.verify_mode = ssl.CERT_NONE
_insecure_ws.maximum_version = ssl.TLSVersion.TLSv1_2


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
    # Load balancing is automatic by default: kept manual here, so that it moves no VM another test watches. Its own
    # test turns it back on.
    ok(api(1, "PUT", "/drs/settings", {"mode": "manual", "threshold": 3}, tokens[1]))
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


def test_the_permissions_are_written_by_the_daemon(cluster):
    """A group, its member, an ACL, an API token and a rename, written by hyperlite-daemon on a real node, never by
    the API (docs/design/privilege-separation.md, section 14.1): each appears in the daemon's journal as its own
    operation, and the API token then signs a request in."""
    t = cluster["tokens"][1]
    since = on(1, "date", "+%Y-%m-%d %H:%M:%S").strip()
    group = ok(api(1, "POST", "/groups", {"name": "lab-ops"}, t))["id"]
    ok(api(1, "POST", f"/groups/{group}/members", {"username": "admin"}, t))
    acl = {
        "subject_type": "group",
        "subject_id": str(group),
        "role": "operator",
        "resource_type": "vm",
        "resource_id": "labacl",
    }
    acl_id = ok(api(1, "POST", "/acl", acl, t))["id"]
    ok(api(1, "PATCH", f"/groups/{group}", {"name": "lab-ops2"}, t))
    made = ok(api(1, "POST", "/auth/tokens", {"name": "lab-4b", "expires_days": 1}, t))
    assert ok(api(1, "GET", "/auth/me", token=made["token"]))["username"] == "admin"
    journal = on(1, "journalctl", "-u", "hyperlite-daemon", "--since", since, "--no-pager", "-o", "cat")
    for op in ("group_create", "group_member_add", "acl_create", "rename_label", "token_create", "token_used"):
        assert f"access.{op} by" in journal, op
    ok(api(1, "DELETE", f"/acl/{acl_id}", token=t))
    ok(api(1, "DELETE", f"/auth/tokens/{made['id']}", token=t))
    ok(api(1, "DELETE", f"/groups/{group}", token=t))


SERVICES = ("hyperlite-scheduler", "hyperlite-ha-lrm", "hyperlite-ha-crm")


def test_the_services_run_beside_the_api(cluster):
    """hyperlite-scheduler (app/scheduler) and HA's two services (app/ha_services) on every member: running, started
    by the package with the API, and the API told to start none of their loops (docs/design/privilege-separation.md,
    section 14, 4c)."""
    for n in NODES:
        for service in SERVICES:
            assert on(n, "systemctl", "is-active", service).strip() == "active", (n, service)
            journal = on(n, "journalctl", "-u", service, "--no-pager", "-o", "cat")
            assert f"{service} started" in journal, (n, service)
        env = on(n, "systemctl", "show", "hyperlite", "--property=Environment", "--value")
        assert "HYPERLITE_SCHEDULER_SERVICE=1" in env and "HYPERLITE_HA_SERVICE=1" in env, n
    # A restart of the API restarts them with it (PartOf=): all always run the same version.
    before = {s: on(1, "systemctl", "show", s, "--property=MainPID", "--value").strip() for s in SERVICES}
    on(1, "systemctl", "restart", "hyperlite")
    wait("node 1's API back", lambda: signed_in(1, PASSWORD), timeout=120)
    for service in SERVICES:
        after = on(1, "systemctl", "show", service, "--property=MainPID", "--value").strip()
        assert after not in ("", "0", before[service]), service
        assert on(1, "systemctl", "is-active", service).strip() == "active", service


def test_the_daemon_makes_the_admin_account_at_its_start(cluster):
    """docs/design/privilege-separation.md, 4e: hyperlite-daemon made the admin account at its first start, with the
    password postinst wrote (the cluster fixture signed in with it), and the API made none."""
    for n in NODES:
        daemon = on(n, "journalctl", "-u", "hyperlite-daemon", "--no-pager", "-o", "cat")
        assert "Admin account created: the initial password is in" in daemon, n
        assert "Admin account created" not in on(n, "journalctl", "-u", "hyperlite", "--no-pager", "-o", "cat"), n


def test_a_node_that_is_no_member_is_refused(cluster):
    t = cluster["tokens"]
    status, body = api(1, "GET", "/networks?node=pve-nowhere", token=t[1])
    assert status == 409 and "not a member" in body["detail"]


def test_the_csv_exports_hold_every_members_rows(cluster):
    """The audit log's and the tasks' exports with cluster=true, from node 1: each member's own export read over the
    members' pinned HTTPS and merged newest first, every row naming the member that holds it."""
    t, names = cluster["tokens"], cluster["names"]
    # Every node holds audit entries of its own: the administrator's sign-ins and password change of the fixture.
    rows = list(csv.reader(io.StringIO(ok(api(1, "GET", "/audit/export.csv?cluster=true", token=t[1])))))
    assert rows[0][0] == "timestamp" and rows[0][-1] == "member", rows[0]
    assert {row[-1] for row in rows[1:]} == set(names.values()), rows[:5]
    assert [row[0] for row in rows[1:]] == sorted((row[0] for row in rows[1:]), reverse=True)

    # Each member records the export of its own entries.
    def exported(n):
        return any(
            e["action"] == "export_audit" for e in ok(api(n, "GET", "/audit?action=export_audit", token=t[n]))["items"]
        )

    wait("node 2's audit log naming the export", lambda: exported(2), 30, 1)
    rows = list(csv.reader(io.StringIO(ok(api(1, "GET", "/tasks/export.csv?cluster=true", token=t[1])))))
    assert rows[0][0] == "id" and rows[0][-1] == "member", rows[0]
    assert {row[-1] for row in rows[1:]} <= set(names.values())
    assert [row[1] for row in rows[1:]] == sorted((row[1] for row in rows[1:]), reverse=True)


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


# A handshake slower than this has the API's threads dumped while it waits (faulthandler on SIGUSR1, app/main.py);
# one slower than HANDSHAKE_S fails the test with what they were doing.
HANDSHAKE_DUMP_S = 10
HANDSHAKE_S = 90


def _busy_threads(dump):
    """The threads of a faulthandler dump that were doing something: not a loop waiting for its next turn."""
    blocks, current = [], []
    for line in dump.splitlines():
        if line.startswith(("Thread 0x", "Current thread")) and current:
            blocks.append(current)
            current = []
        current.append(line)
    if current:
        blocks.append(current)
    idle = ('threading.py", line 363 in wait', "in _worker", "in select", "in _wait_for_tstate_lock")
    return "\n".join("\n".join(b) for b in blocks if not any(i in "".join(b[1:3]) for i in idle))


def _loop_tasks(journal):
    """The last list of the event loop's tasks app/main.py logged (SIGUSR2)."""
    at = journal.rfind("event loop: ")
    return journal[at:] if at >= 0 else "(not logged)"


def console(n, path):
    """A WebSocket to node n's API, as the dashboard opens a console (its certificate not checked, as for api()). A
    slow handshake is measured, and the API's threads are dumped while it waits; one that does not come fails with
    what they were doing (the lab's logs are not always at hand)."""
    import threading

    from websockets.sync.client import connect

    # The threads (SIGUSR1), the event loop's tasks (SIGUSR2), and the port's sockets: a connection waiting in the
    # listening socket's queue was never accepted; one established with unread bytes was never read.
    dump = (
        "pid=$(systemctl show --property=MainPID --value hyperlite); kill -USR1 $pid; kill -USR2 $pid; "
        "ss -tnoi state all '( sport = :8000 )' | logger -t lab-sockets"
    )
    timer = threading.Timer(HANDSHAKE_DUMP_S, lambda: on(n, "sh", "-c", dump))
    began = time.monotonic()
    timer.start()
    try:
        ws = connect(f"wss://{ip(n)}:8000/api/v1{path}", ssl=_insecure_ws, open_timeout=HANDSHAKE_S, max_size=None)
    except TimeoutError:
        journal = on(n, "sh", "-c", "journalctl -u hyperlite -n 400 --no-pager -o cat")
        sockets = on(n, "sh", "-c", "journalctl -t lab-sockets -n 60 --no-pager -o cat")
        raise AssertionError(
            f"no WebSocket handshake from node {n} in {HANDSHAKE_S} s; its sockets at {HANDSHAKE_DUMP_S} s:\n"
            f"{sockets[-1500:]}\nits event loop's tasks:\n{_loop_tasks(journal)[-3000:]}\nits busy threads:\n"
            f"{_busy_threads(journal)[-2000:]}"
        ) from None
    finally:
        timer.cancel()
    took = time.monotonic() - began
    if took > HANDSHAKE_DUMP_S:
        journal = on(n, "sh", "-c", "journalctl -u hyperlite -n 400 --no-pager -o cat")
        print(f"[lab] WebSocket handshake from node {n} took {took:.0f} s; busy threads:\n{_busy_threads(journal)}")
    return ws


def read_until(ws, text, timeout=30):
    """What a terminal printed, up to `text`."""
    seen, deadline = "", time.monotonic() + timeout
    while text not in seen:
        assert time.monotonic() < deadline, f"not printed: {text!r}; seen: {seen[-500:]!r}"
        seen += ws.recv(timeout=max(0.1, deadline - time.monotonic()))
    return seen


def daemon_children(n):
    """The consoles' sessions hyperlite-daemon runs on node n now: its children but the read-only tunnels to the other
    nodes (`ssh -N -L`, app/core/node_tunnel.py)."""
    pid = on(n, "systemctl", "show", "--property=MainPID", "--value", "hyperlite-daemon").strip()
    running = on(n, "sh", "-c", f"ps -o args= --ppid {pid} || true")
    return [line for line in running.splitlines() if line.strip() and " -L " not in line]


def test_the_consoles_are_opened_by_the_daemon(cluster):
    """The host shell, and a VM's VNC and serial consoles on another member, opened by hyperlite-daemon as root and
    relayed by the API (docs/design/privilege-separation.md, the consoles domain). A session ends with its console."""
    t, names = cluster["tokens"], cluster["names"]
    ticket = ok(api(1, "POST", "/host/terminal-ticket", token=t[1]))["ticket"]
    with console(1, f"/host/terminal?ticket={ticket}") as ws:
        ws.send("echo hl-$((6*7)) uid-$(id -u) key-${HYPERLITE_SECRET_KEY:-none}\r")
        read_until(ws, "hl-42 uid-0 key-none")  # root's shell, never the service's environment
    wait("the host shell ended", lambda: daemon_children(1) == [], timeout=30, every=1)

    b = names[2]
    if not nested(2):
        pytest.skip("no nested virtualization on this host: the VMs of the lab cannot run a VM of their own")
    form, content_type = multipart("console.iso", b"\0" * (1 << 20))
    ok(api(1, "POST", f"/isos?node={b}", form=form, content_type=content_type, token=t[1]))
    spec = {"name": "labcon", "vcpu": 1, "memory_mb": 256, "disks": [{"size_gb": 1}], "iso": "console.iso"}
    finished(1, t[1], ok(api(1, "POST", f"/vms?node={b}&wait=false", spec, t[1]))["task_id"], node=b)
    if ok(api(1, "GET", f"/vms/labcon?node={b}", token=t[1]))["state"] != "active":
        ok(api(1, "POST", f"/vms/labcon/start?node={b}", token=t[1]))
    wait("labcon running", lambda: ok(api(1, "GET", f"/vms/labcon?node={b}", token=t[1]))["state"] == "active")

    # Node 1's daemon reaches the VNC server on node 2's loopback with `ssh -W` and the cluster's key.
    ticket = ok(api(1, "POST", f"/vms/labcon/console-ticket?node={b}", token=t[1]))["ticket"]
    with console(1, f"/vms/labcon/console?ticket={ticket}") as ws:
        assert ws.recv(timeout=30)[:4] == b"RFB ", "QEMU's VNC greeting"
    wait("the VNC relay ended", lambda: daemon_children(1) == [], timeout=30, every=1)

    # The serial console: a port added by the daemon on node 2 when the VM has none, then `virsh console` over SSH.
    status, answer = api(1, "POST", f"/vms/labcon/serial-ticket?node={b}", token=t[1])
    if status == 409:
        ok(api(1, "POST", f"/vms/labcon/stop?node={b}&force=true", token=t[1]))
        wait("labcon stopped", lambda: ok(api(1, "GET", f"/vms/labcon?node={b}", token=t[1]))["state"] == "stopped")
        ok(api(1, "POST", f"/vms/labcon/start?node={b}", token=t[1]))
        wait("labcon running", lambda: ok(api(1, "GET", f"/vms/labcon?node={b}", token=t[1]))["state"] == "active")
        status, answer = api(1, "POST", f"/vms/labcon/serial-ticket?node={b}", token=t[1])
    assert status == 200, answer
    with console(1, f"/vms/labcon/serial?ticket={answer['ticket']}") as ws:
        ws.send("\r")
        try:
            said = ws.recv(timeout=5)  # a guest without a system prints nothing: virsh just stays connected
        except TimeoutError:
            said = ""
        assert "error" not in said.lower() and "[hyperlite]" not in said, said
        assert daemon_children(1) != [], "virsh console runs while the console is open"
    wait("the serial console ended", lambda: daemon_children(1) == [], timeout=30, every=1)

    ok(api(1, "POST", f"/vms/labcon/stop?node={b}&force=true", token=t[1]))
    wait("labcon stopped", lambda: ok(api(1, "GET", f"/vms/labcon?node={b}", token=t[1]))["state"] == "stopped")
    ok(api(1, "DELETE", f"/vms/labcon?node={b}&confirm=true", token=t[1]))


def test_a_backup_is_made_verified_restored_and_deleted_by_the_daemon(cluster):
    """A directory backup of a stopped VM on a member, then its verification, its restore as a new VM and its
    deletion, each run by hyperlite-daemon (docs/design/privilege-separation.md, the backups domain, part 1a), the
    files checked on the node."""
    t = cluster["tokens"][2]
    form, content_type = multipart("bk.iso", b"\0" * (1 << 20))
    ok(api(2, "POST", "/isos", form=form, content_type=content_type, token=t))
    spec = {"name": "labbk", "vcpu": 1, "memory_mb": 256, "disks": [{"size_gb": 1}], "iso": "bk.iso"}
    finished(2, t, ok(api(2, "POST", "/vms?wait=false", spec, t))["task_id"])
    if ok(api(2, "GET", "/vms/labbk", token=t))["state"] != "stopped":
        ok(api(2, "POST", "/vms/labbk/stop?force=true", token=t))
        wait("labbk stopped", lambda: ok(api(2, "GET", "/vms/labbk", token=t))["state"] == "stopped")

    def backups():
        return ok(api(2, "GET", "/vms/labbk/backups", token=t))["items"]

    ok(api(2, "POST", "/vms/labbk/backups", {}, t))
    done = wait("the backup", lambda: [r for r in backups() if r["status"] in ("succeeded", "failed")], timeout=600)
    row = done[0]
    assert row["status"] == "succeeded", row
    path = row["path"]
    assert path.startswith("/var/lib/hyperlite/") and "/labbk/" in path, path
    assert "manifest.json" in on(2, "sh", "-c", f"ls {shlex.quote(path)}").split()

    verified = ok(api(2, "POST", f"/backups/{row['id']}/verify", token=t))
    if verified.get("task_id"):
        finished(2, t, verified["task_id"])
    wait("verified", lambda: next(r for r in backups() if r["id"] == row["id"])["verification"] == "verified", 300)

    restored = ok(api(2, "POST", f"/backups/{row['id']}/restore", {"mode": "new", "new_name": "labbk2"}, t))
    if restored.get("task_id"):
        finished(2, t, restored["task_id"])
    wait("labbk2 restored", lambda: api(2, "GET", "/vms/labbk2", token=t)[0] == 200, 600)

    ok(api(2, "DELETE", f"/backups/{row['id']}?confirm=true", token=t))
    assert on(2, "sh", "-c", f"test -e {shlex.quote(path)} && echo left || echo gone").strip() == "gone"
    for vm in ("labbk", "labbk2"):
        ok(api(2, "DELETE", f"/vms/{vm}?confirm=true", token=t))
    api(2, "DELETE", "/isos/bk.iso?confirm=true", token=t)


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


def test_an_ha_vm_is_restarted_elsewhere_when_its_node_is_cut_off(cluster):
    """#333 step 4 (docs/design/ha-automatic.md, 4): an HA VM on node 2, its disk on an NFS share; node 2 cut from
    Corosync loses the quorum, its watchdog resets it; the leader restarts the VM on another member, once; node 2,
    back, has the VM no more."""
    t, names = cluster["tokens"], cluster["names"]
    b = names[2]
    if not nested(2):
        pytest.skip("no nested virtualization on this host: the VMs of the lab cannot run a VM of their own")
    on(1, "sh", "-c", "DEBIAN_FRONTEND=noninteractive apt-get install -y -q nfs-kernel-server >/dev/null 2>&1")
    on(
        1,
        "sh",
        "-c",
        "mkdir -p /srv/ha && chmod 777 /srv/ha && echo '/srv/ha 192.0.2.0/24(rw,sync,no_root_squash,no_subtree_check)' > /etc/exports && exportfs -ra && systemctl restart nfs-kernel-server",
    )
    pool = {"name": "hapool", "type": "netfs", "nfs_host": ip(1), "nfs_export_path": "/srv/ha", "all_nodes": True}
    ok(api(1, "POST", "/storage", pool, t[1]), 200, 201, 202)
    form, content_type = multipart("haboot.iso", b"\0" * (1 << 20))
    ok(api(1, "POST", f"/isos?node={b}", form=form, content_type=content_type, token=t[1]))
    spec = {
        "name": "havm",
        "vcpu": 1,
        "memory_mb": 256,
        "disks": [{"size_gb": 1}],
        "iso": "haboot.iso",
        "storage_pool": "hapool",
    }
    finished(1, t[1], ok(api(1, "POST", f"/vms?node={b}&wait=false", spec, t[1]))["task_id"], node=b)
    if ok(api(1, "GET", f"/vms/havm?node={b}", token=t[1]))["state"] != "active":
        ok(api(1, "POST", f"/vms/havm/start?node={b}", token=t[1]))
    wait("havm running", lambda: ok(api(1, "GET", f"/vms/havm?node={b}", token=t[1]))["state"] == "active")
    ok(api(1, "POST", "/ha/havm/enable", {"node": b, "priority": "high"}, t[1]), 201)
    # Node 2 arms its watchdog: it hosts a running HA VM, in a cluster (app/core/ha_selffence.py).
    wait(
        "node 2's watchdog armed",
        lambda: (
            "softdog" in on(2, "sh", "-c", "lsmod | grep -o softdog || true")
            or on(2, "sh", "-c", "fuser /dev/watchdog 2>/dev/null || true").strip()
        ),
        timeout=90,
        every=5,
    )
    subprocess.run(["bash", str(LAB), "cut", "2"], check=True)
    began = time.monotonic()

    def running_elsewhere():
        where = [
            n
            for n in (1, 3)
            if api(n, "GET", "/vms/havm", token=t[n])[0] == 200
            and ok(api(n, "GET", "/vms/havm", token=t[n]))["state"] == "active"
        ]
        return where or None

    try:
        where = wait("havm restarted on another member", running_elsewhere, timeout=420, every=10)
    except AssertionError:
        for n in (1, 3):  # what the leader recorded, for the report
            print(f"[lab] node {n}: {api(n, 'GET', '/ha', token=t[n])}")
            print(
                on(n, "sh", "-c", "journalctl -u hyperlite --no-pager | grep -E 'HA |ha_recovery|Traceback' | tail -25")
            )
        raise
    print(f"[lab] havm restarted on node {where} {time.monotonic() - began:.0f} s after node 2 was cut off")
    assert len(where) == 1, where  # one copy
    subprocess.run(["bash", str(LAB), "heal", "2"], check=False)  # its reset cleared the cut already
    tokens2 = wait("node 2 back", lambda: signed_in(2, PASSWORD), timeout=420, every=10)
    wait("havm undefined on node 2", lambda: api(2, "GET", "/vms/havm", token=tokens2)[0] == 404, timeout=180, every=5)
    record = next(r for r in ok(api(1, "GET", "/ha", token=t[1]))["items"] if r["vm_name"] == "havm")
    assert record["node"] == names[where[0]] and record["ha_state"] == "ok" and record["priority"] == "high", record
    n = where[0]
    ok(api(1, "DELETE", "/ha/havm", token=t[1]))
    ok(api(1, "POST", f"/vms/havm/stop?node={names[n]}&force=true", token=t[1]))
    wait("havm stopped", lambda: ok(api(1, "GET", f"/vms/havm?node={names[n]}", token=t[1]))["state"] == "stopped")
    ok(api(1, "DELETE", f"/vms/havm?node={names[n]}&confirm=true", token=t[1]))


def _agent_answers(n, vm):
    try:
        on(n, "virsh", "qemu-agent-command", vm, '{"execute":"guest-ping"}')
        return True
    except subprocess.CalledProcessError:
        return False


def test_a_hung_ha_vm_is_reset_by_its_node(cluster):
    """#333 step 6 (docs/design/ha-automatic.md, 4.9): an HA VM with its guest agent and monitoring on, its kernel
    made to panic (it hangs: no pvpanic device, kernel.panic=0); its node resets it in place, and its agent answers
    again."""
    t, names = cluster["tokens"], cluster["names"]
    if not nested(3):
        pytest.skip("no nested virtualization on this host: the VMs of the lab cannot run a VM of their own")
    if not any(p["name"] == "hapool" for p in ok(api(3, "GET", "/storage", token=t[3]))["items"]):
        pytest.skip("the NFS pool of the HA test is not there")
    # The cloud image the VM starts from, from this host's cache: no 350 MB download inside the lab.
    image = Path(os.environ.get("HYPERLITE_LAB_CACHE") or "/var/cache/hyperlite-lab") / "debian.qcow2"  # as lab/vm.sh
    key = DIR / "id_ed25519"
    subprocess.run(
        ["scp", "-q", "-i", str(key), "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
         "-o", "LogLevel=ERROR", str(image), f"debian@{ip(3)}:/tmp/base.qcow2"],
        check=True, timeout=600,
    )  # fmt: skip
    on(
        3,
        "sh",
        "-c",
        "mkdir -p /var/lib/libvirt/images/base && mv /tmp/base.qcow2 /var/lib/libvirt/images/base/debian-12-generic-amd64.qcow2",
    )
    spec = {
        "name": "hmvm",
        "vcpu": 1,
        "memory_mb": 512,
        "disks": [{"size_gb": 4}],
        "storage_pool": "hapool",
        "username": "debian",
        "password": "Lab-guest-" + secrets.token_hex(4),
    }
    finished(3, t[3], ok(api(3, "POST", "/vms?wait=false", spec, t[3]))["task_id"])
    if ok(api(3, "GET", "/vms/hmvm", token=t[3]))["state"] != "active":
        ok(api(3, "POST", "/vms/hmvm/start", token=t[3]))
    # cloud-init installs qemu-guest-agent from the network: minutes.
    wait("hmvm's guest agent", lambda: _agent_answers(3, "hmvm"), timeout=900, every=15)
    ok(api(3, "POST", "/ha/hmvm/enable", {"node": names[3], "monitoring": True}, t[3]), 201)
    assert ok(api(3, "GET", "/ha/hmvm", token=t[3]))["monitoring"] is True
    time.sleep(45)  # a round of the monitor (30 s) sees the agent answer: from then on it may reset the VM
    panic = {
        "execute": "guest-exec",
        "arguments": {
            "path": "/bin/sh",
            "arg": ["-c", "sysctl -w kernel.panic=0; echo 1 > /proc/sys/kernel/sysrq; echo c > /proc/sysrq-trigger"],
        },
    }
    try:
        on(3, "virsh", "qemu-agent-command", "hmvm", json.dumps(panic))
    except subprocess.CalledProcessError:  # an agent that refuses guest-exec: the same through the keyboard
        on(3, "virsh", "send-key", "hmvm", "KEY_LEFTALT", "KEY_SYSRQ", "KEY_C")
    began = time.monotonic()
    wait("hmvm's agent silent", lambda: not _agent_answers(3, "hmvm"), timeout=60, every=5)

    def reset():
        action = ok(api(3, "GET", "/ha/hmvm", token=t[3])).get("last_action") or ""
        return "reset" in action and action

    try:
        action = wait("hmvm reset by its node", reset, timeout=300, every=10)
    except AssertionError:
        print(
            on(
                3,
                "sh",
                "-c",
                "journalctl -u hyperlite --no-pager | grep -E 'HA monitoring|ha_monitor|Traceback' | tail -25",
            )
        )
        raise
    print(f"[lab] hmvm reset {time.monotonic() - began:.0f} s after its kernel panicked: {action}")
    assert ok(api(3, "GET", "/vms/hmvm", token=t[3]))["state"] == "active"  # in place: no failover
    wait("hmvm's guest agent after the reset", lambda: _agent_answers(3, "hmvm"), timeout=300, every=10)
    ok(api(3, "DELETE", "/ha/hmvm", token=t[3]))
    ok(api(3, "POST", "/vms/hmvm/stop?force=true", token=t[3]))
    wait("hmvm stopped", lambda: ok(api(3, "GET", "/vms/hmvm", token=t[3]))["state"] == "stopped")
    ok(api(3, "DELETE", "/vms/hmvm?confirm=true", token=t[3]))


def test_a_container_lives_and_goes_on_a_member(cluster):
    """A system container's whole life on a real node, its filesystem written by hyperlite-daemon: made from the
    local base, stopped, snapshotted, changed, restored, archived and restored from its archive, renamed, cloned and
    deleted, its files where the definition says (docs/design/privilege-separation.md, the containers domain)."""
    t = cluster["tokens"][2]
    if "lxc" not in on(2, "sh", "-c", "virsh -c lxc:///system uri 2>/dev/null || true"):
        pytest.skip("no LXC driver on the lab's nodes")
    on(2, "apt-get", "install", "-y", "-q", "debootstrap")  # the local base (not a dependency of the package)
    spec = {"name": "labct", "username": "lab", "password": PASSWORD, "network": "default"}
    status, answer = api(2, "POST", "/containers", spec, t, timeout=900)
    assert status == 201, answer
    root = "/var/lib/libvirt/containers"
    assert on(2, "test", "-d", f"{root}/labct/etc") == ""
    wait("labct running", lambda: ok(api(2, "GET", "/containers/labct", token=t))["state"] == "active")
    # Its root shell, opened by hyperlite-daemon (nsenter into its PID 1, found by the daemon).
    ticket = ok(api(2, "POST", "/containers/labct/shell-ticket", token=t))["ticket"]
    with console(2, f"/containers/labct/shell?ticket={ticket}") as ws:
        ws.send("echo ct-$((6*7)) $(cat /etc/hostname)\r")  # its own filesystem: the shell is inside
        read_until(ws, "ct-42 labct")
    wait("the container shell ended", lambda: daemon_children(2) == [], timeout=30, every=1)
    ok(api(2, "POST", "/containers/labct/stop?force=true", token=t))
    wait("labct stopped", lambda: ok(api(2, "GET", "/containers/labct", token=t))["state"] == "stopped")
    taken = ok(api(2, "POST", "/containers/labct/snapshots", {"name": "clean"}, t))
    finished(2, t, taken["task_id"])
    on(2, "sh", "-c", f"echo changed > {root}/labct/etc/motd")
    restored = ok(api(2, "POST", "/containers/labct/snapshots/clean/restore", token=t))
    finished(2, t, restored["task_id"])
    assert "changed" not in on(2, "cat", f"{root}/labct/etc/motd")
    # Its archive, made, restored as another container and deleted by hyperlite-daemon (the backups domain, part 4).
    archived = ok(api(2, "POST", "/containers/labct/backups", token=t))
    finished(2, t, archived["task_id"])
    archive = next(
        b for b in ok(api(2, "GET", "/containers/backups", token=t))["items"] if b["container_name"] == "labct"
    )
    assert archive["status"] == "succeeded" and on(2, "test", "-s", archive["path"]) == ""
    status, answer = api(
        2, "POST", f"/containers/backups/{archive['id']}/restore", {"new_name": "labct4"}, t, timeout=900
    )
    assert status == 201, answer
    assert on(2, "test", "-d", f"{root}/labct4/etc") == ""
    ok(api(2, "DELETE", f"/containers/backups/{archive['id']}?confirm=true", token=t))
    assert on(2, "sh", "-c", f"test -e {archive['path']} && echo left || echo gone").strip() == "gone"
    ok(api(2, "POST", "/containers/labct/rename", {"new_name": "labct2"}, t))
    assert on(2, "cat", f"{root}/labct2/etc/hostname").strip() == "labct2"
    status, answer = api(2, "POST", "/containers/labct2/clone", {"new_name": "labct3"}, t, timeout=900)
    assert status == 201, answer
    for name in ("labct2", "labct3", "labct4"):
        ok(api(2, "DELETE", f"/containers/{name}?confirm=true", token=t))
        assert on(2, "sh", "-c", f"test -e {root}/{name} && echo left || echo gone").strip() == "gone"
    assert ok(api(2, "GET", "/containers", token=t))["items"] == []


def test_load_balancing_moves_a_busy_vm_off_its_member(cluster):
    """#334 step 4 (docs/design/drs.md): two VMs that spin their vCPU on node 1 push it above 90 % of its CPU; load
    balancing, automatic, live-migrates one of them to another member, and each still runs once."""
    t = cluster["tokens"]
    if not nested(1):
        pytest.skip("no nested virtualization on this host: the VMs of the lab cannot run a VM of their own")
    if not any(p["name"] == "hapool" for p in ok(api(1, "GET", "/storage", token=t[1]))["items"]):
        pytest.skip("the NFS pool of the HA test is not there")
    # A lab's minutes instead of production's quarter of an hour (app/core/drs.py), on every member.
    conf = (
        "[Service]\nEnvironment=HYPERLITE_DRS_EVALUATE_S=20 HYPERLITE_DRS_OBSERVE_S=5 HYPERLITE_DRS_CPU_WINDOW_S=40\n"
    )
    for n in NODES:
        on(
            n,
            "sh",
            "-c",
            # Load balancing runs in HA's services (app/ha_services), restarted with the API (PartOf=): each gets the
            # drop-in, the API too for a checkout's way of running everything in it.
            " && ".join(
                f"mkdir -p /etc/systemd/system/{unit}.service.d && printf '{conf}' > "
                f"/etc/systemd/system/{unit}.service.d/lab-drs.conf"
                for unit in ("hyperlite", "hyperlite-ha-lrm", "hyperlite-ha-crm")
            )
            + " && systemctl daemon-reload",
        )
    for n in NODES:
        before = main_pid(n)
        on(n, "systemctl", "restart", "hyperlite")
        wait(f"node {n} restarted", lambda n=n, b=before: main_pid(n) not in (b, "0"), timeout=60, every=1)
    for n in NODES:
        t[n] = wait(f"node {n} after its restart", lambda n=n: signed_in(n, PASSWORD), timeout=120, every=2)
    image = Path(os.environ.get("HYPERLITE_LAB_CACHE") or "/var/cache/hyperlite-lab") / "debian.qcow2"
    key = DIR / "id_ed25519"
    subprocess.run(
        ["scp", "-q", "-i", str(key), "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
         "-o", "LogLevel=ERROR", str(image), f"debian@{ip(1)}:/tmp/base.qcow2"],
        check=True, timeout=600,
    )  # fmt: skip
    on(
        1,
        "sh",
        "-c",
        "mkdir -p /var/lib/libvirt/images/base && mv /tmp/base.qcow2 /var/lib/libvirt/images/base/debian-12-generic-amd64.qcow2",
    )
    busy = ("busy1", "busy2")
    for vm in busy:
        spec = {"name": vm, "vcpu": 1, "memory_mb": 384, "disks": [{"size_gb": 4}], "storage_pool": "hapool",
                "username": "debian", "password": "Lab-guest-" + secrets.token_hex(4)}  # fmt: skip
        finished(1, t[1], ok(api(1, "POST", "/vms?wait=false", spec, t[1]))["task_id"])
        if ok(api(1, "GET", f"/vms/{vm}", token=t[1]))["state"] != "active":
            ok(api(1, "POST", f"/vms/{vm}/start", token=t[1]))
    for vm in busy:
        wait(f"{vm}'s guest agent", lambda vm=vm: _agent_answers(1, vm), timeout=900, every=15)
    spin = {
        "execute": "guest-exec",
        "arguments": {"path": "/bin/sh", "arg": ["-c", "nohup sh -c 'while :; do :; done' >/dev/null 2>&1 &"]},
    }
    for vm in busy:
        on(1, "virsh", "qemu-agent-command", vm, json.dumps(spin))
    ok(api(1, "PUT", "/drs/settings", {"mode": "automatic", "threshold": 3}, t[1]))
    began = time.monotonic()

    def where(vm):
        # One read per member: during a move, the member it leaves may answer neither 200 nor 404 for a moment.
        found = []
        for n in NODES:
            status, body = api(n, "GET", f"/vms/{vm}", token=t[n])
            if status == 200 and body["state"] == "active":
                found.append(n)
        return found

    def one_moved():
        places = {vm: where(vm) for vm in busy}
        moved = [vm for vm, nodes in places.items() if nodes and nodes != [1]]
        return moved and places

    try:
        places = wait("a busy VM moved off node 1", one_moved, timeout=600, every=10)
    except AssertionError:
        for n in NODES:
            print(f"[lab] node {n}: {api(n, 'GET', '/drs', token=t[n])[1]}")
            print(
                on(
                    n,
                    "sh",
                    "-c",
                    "journalctl -u hyperlite --no-pager | grep -E 'Load balancing|drs|Traceback' | tail -15",
                )
            )
        raise
    print(f"[lab] load balancing moved {places} {time.monotonic() - began:.0f} s after the VMs began to spin")
    assert all(len(nodes) == 1 for nodes in places.values()), places  # each runs once
    ok(api(1, "PUT", "/drs/settings", {"mode": "manual", "threshold": 3}, t[1]))

    def removed(vm):
        """Stopped, then deleted, through the member it runs on (which names itself no "?node="). A move load
        balancing started before the switch to manual finishes first: the VM is busy (409) until then, and stopped and
        deleted where it ends up."""
        for n in NODES:
            status, body = api(n, "GET", f"/vms/{vm}", token=t[n])
            if status != 200:
                continue
            if body["state"] != "stopped":
                api(n, "POST", f"/vms/{vm}/stop?force=true", token=t[n])
                return False
            return api(n, "DELETE", f"/vms/{vm}?confirm=true", token=t[n])[0] in (200, 202)
        return True

    for vm in busy:
        wait(f"{vm} stopped and deleted", lambda vm=vm: removed(vm), timeout=300, every=5)


def _boot_id(n):
    return on(n, "cat", "/proc/sys/kernel/random/boot_id").strip()


def _watchdog(t, name):
    """What member `name`'s watchdog published, as the leader's HA status shows it (armed, reset_within_s, stopped)."""
    return next(m for m in ok(api(1, "GET", "/ha/status", token=t[1]))["members"] if m["name"] == name)["watchdog"]


def _unit_pid(n, unit):
    return on(n, "systemctl", "show", "--property=MainPID", "--value", unit).strip()


def _still(n, vm, boot, seconds, t):
    """For `seconds`: node n not reset, `vm` still running there, and running nowhere else."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        time.sleep(10)
        assert _boot_id(n) == boot, f"node {n} was reset"
        assert on(n, "virsh", "domstate", vm).strip() == "running", f"{vm} stopped on node {n}"
        for other in (m for m in NODES if m != n):
            code, body = api(other, "GET", f"/vms/{vm}", token=t[other])
            assert not (code == 200 and body.get("state") == "active"), f"{vm} started on node {other} too"


def test_only_the_lrm_keeps_a_node_with_an_ha_vm_alive(cluster):
    """HA's two services (#742, docs/design/privilege-separation.md, section 14, 4c), on node 3 running an HA VM: a
    crash of the CRM or of the API neither resets the node nor stops the VM; a normal stop disarms the watchdog and says
    so, and the leader leaves the VM where it is; a crash of the LRM is restarted by systemd within the watchdog's
    timeout, which it arms again."""
    t, names = cluster["tokens"], cluster["names"]
    c = names[3]
    if not nested(3):
        pytest.skip("no nested virtualization on this host: the VMs of the lab cannot run a VM of their own")
    on(1, "sh", "-c", "DEBIAN_FRONTEND=noninteractive apt-get install -y -q nfs-kernel-server >/dev/null 2>&1")
    on(
        1,
        "sh",
        "-c",
        "mkdir -p /srv/ha && chmod 777 /srv/ha && echo '/srv/ha 192.0.2.0/24(rw,sync,no_root_squash,no_subtree_check)' > /etc/exports && exportfs -ra && systemctl restart nfs-kernel-server",
    )
    if not any(p["name"] == "hapool" for p in ok(api(1, "GET", "/storage", token=t[1]))["items"]):
        pool = {"name": "hapool", "type": "netfs", "nfs_host": ip(1), "nfs_export_path": "/srv/ha", "all_nodes": True}
        ok(api(1, "POST", "/storage", pool, t[1]), 200, 201, 202)
    form, content_type = multipart("svcboot.iso", b"\0" * (1 << 20))
    ok(api(1, "POST", f"/isos?node={c}", form=form, content_type=content_type, token=t[1]))
    spec = {"name": "svcvm", "vcpu": 1, "memory_mb": 256, "disks": [{"size_gb": 1}], "iso": "svcboot.iso", "storage_pool": "hapool"}  # fmt: skip
    finished(1, t[1], ok(api(1, "POST", f"/vms?node={c}&wait=false", spec, t[1]))["task_id"], node=c)
    if ok(api(1, "GET", f"/vms/svcvm?node={c}", token=t[1]))["state"] != "active":
        ok(api(1, "POST", f"/vms/svcvm/start?node={c}", token=t[1]))
    wait("svcvm running", lambda: ok(api(1, "GET", f"/vms/svcvm?node={c}", token=t[1]))["state"] == "active")
    ok(api(1, "POST", "/ha/svcvm/enable", {"node": c}, t[1]), 201)
    wait("node 3's watchdog armed", lambda: _watchdog(t, c).get("armed"), timeout=120, every=5)
    reset_within = _watchdog(t, c).get("reset_within_s") or 60
    hold = reset_within + 30  # past the moment a node that stopped feeding would have been reset
    boot = _boot_id(3)
    print(f"[lab] node 3 armed, reset within {reset_within} s")

    # A crash of the CRM, then of the API: systemd restarts each; no reset, the HA VM keeps running.
    for unit in ("hyperlite-ha-crm", "hyperlite"):
        pid = _unit_pid(3, unit)
        on(3, "kill", "-9", pid)
        began = time.monotonic()
        wait(
            f"{unit} restarted",
            lambda unit=unit, pid=pid: _unit_pid(3, unit) not in ("0", "", pid),
            timeout=120,
            every=1,
        )
        print(f"[lab] kill -9 {unit}: restarted after {time.monotonic() - began:.0f} s")
        _still(3, "svcvm", boot, hold, t)
    t[3] = wait("node 3's API back", lambda: signed_in(3, PASSWORD), timeout=120)

    # A normal stop: the watchdog disarmed and "stopped" published; the leader leaves the VM where it is.
    on(3, "systemctl", "stop", "hyperlite")
    for unit in ("hyperlite-ha-lrm", "hyperlite-ha-crm", "hyperlite-scheduler"):
        assert on(3, "sh", "-c", f"systemctl is-active {unit} || true").strip() == "inactive", unit
    wait("node 3's stop published", lambda: _watchdog(t, c).get("stopped"), timeout=60, every=3)
    assert not _watchdog(t, c).get("armed")
    _still(3, "svcvm", boot, hold, t)
    on(3, "systemctl", "start", "hyperlite")
    t[3] = wait("node 3's API back", lambda: signed_in(3, PASSWORD), timeout=120)
    wait(
        "node 3 armed again",
        lambda: _watchdog(t, c).get("armed") and not _watchdog(t, c).get("stopped"),
        timeout=120,
        every=5,
    )

    # A crash of the LRM: no magic close, the device keeps counting; systemd restarts it (RestartSec=3), and the new LRM
    # opens the device again and feeds it before the timeout.
    since = on(3, "date", "+%s").strip()
    pid = _unit_pid(3, "hyperlite-ha-lrm")
    on(3, "kill", "-9", pid)
    began = time.monotonic()
    wait(
        "hyperlite-ha-lrm restarted",
        lambda: _unit_pid(3, "hyperlite-ha-lrm") not in ("0", "", pid),
        timeout=60,
        every=1,
    )
    restarted = time.monotonic() - began
    wait(
        "the new LRM armed the watchdog",
        lambda: (
            "Watchdog armed"
            in on(3, "journalctl", "-u", "hyperlite-ha-lrm", "--since", f"@{since}", "--no-pager", "-o", "cat")
        ),
        timeout=reset_within,
        every=2,
    )
    armed = time.monotonic() - began
    print(
        f"[lab] kill -9 hyperlite-ha-lrm: restarted after {restarted:.1f} s, watchdog armed again after {armed:.0f} s"
    )
    assert armed < reset_within, "armed again only after the watchdog's timeout"
    assert _watchdog(t, c).get("armed") and not _watchdog(t, c).get("stopped")
    _still(3, "svcvm", boot, hold, t)

    ok(api(1, "DELETE", "/ha/svcvm", token=t[1]))
    ok(api(1, "POST", f"/vms/svcvm/stop?node={c}&force=true", token=t[1]))
    wait("svcvm stopped", lambda: ok(api(1, "GET", f"/vms/svcvm?node={c}", token=t[1]))["state"] == "stopped")
    ok(api(1, "DELETE", f"/vms/svcvm?node={c}&confirm=true", token=t[1]))
