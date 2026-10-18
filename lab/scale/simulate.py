#!/usr/bin/env python3
"""Scale simulation (docs/design/scale-test.md, part 2): the real application, many simulated nodes.

    venv/bin/python lab/scale/simulate.py [--nodes 3,10,100] [--vms-total 1000] [--rtt-ms 0.5,20] [--ssh-ms 200]

Only the network edges are simulated, with the costs measured on the lab: opening a libvirt connection to another node
(qemu+ssh: an SSH handshake, --ssh-ms), every libvirt call on such a connection (one round trip, --rtt-ms: 0.5 on a
LAN, 20 over Tailscale), and a call to another member's API (rolling_update.peer_api: an SSH handshake plus a round
trip). Everything else is Hyperlite's own code: the routes, the inventory scan, the cluster views, the NAT address
search. The browser is modelled as the dashboard is (dashboard/src/api/client.js, fetchVMs): this node's VMs and the
nodes, then every other node's VMs, 6 requests at a time (a browser's limit per host over HTTP/1.1).

Prints one line per scenario and measure, in milliseconds. Touches nothing outside a temporary directory.
"""

import argparse
import concurrent.futures as cf
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
WORK = tempfile.mkdtemp(prefix="hyperlite-scale-")
os.environ.update(
    HYPERLITE_SECRET_KEY="scale-simulation-" + "0" * 48,
    HYPERLITE_DB_PATH=f"{WORK}/hyperlite.db",
    HYPERLITE_NODE_NAME="node000",
    HYPERLITE_CFS_SOCKET="/nonexistent/scale-simulation/socket",
    HYPERLITE_WATCHDOG_DEVICE="/nonexistent/scale-simulation/watchdog",
    HYPERLITE_INITIAL_PASSWORD_ROOT=f"{WORK}/initial",
    HYPERLITE_ACCESS_LOG_DIR=f"{WORK}/log",
    HYPERLITE_WATCHDOG="0",
)
from cryptography.fernet import Fernet  # noqa: E402

os.environ["HYPERLITE_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

import libvirt  # noqa: E402

XML = """<domain type='kvm'><name>{name}</name><uuid>{uuid}</uuid><memory unit='KiB'>262144</memory><vcpu>1</vcpu>
<os><type arch='x86_64' machine='q35'>hvm</type></os><devices>
<disk type='file' device='disk'><driver name='qemu' type='qcow2'/><source file='/var/lib/libvirt/images/{name}.qcow2'/>
<target dev='vda' bus='virtio'/></disk><interface type='network'><mac address='52:54:00:{a:02x}:{b:02x}:{c:02x}'/>
<source network='default'/></interface></devices></domain>"""
NET_XML = """<network><name>default</name><ip address='192.168.122.1' prefix='24'><dhcp>
<range start='192.168.122.2' end='192.168.122.254'/></dhcp></ip></network>"""


class Costs:
    rtt = 0.0005  # one remote libvirt call
    ssh = 0.2  # opening a remote connection


class FakeDomain:
    def __init__(self, conn, i, name, running):
        self._conn, self._name, self._id = conn, name, (i + 1) if running else -1
        self._xml = XML.format(name=name, uuid=f"00000000-0000-0000-0000-{i:012d}", a=i >> 16 & 255, b=i >> 8 & 255,
                               c=i & 255)  # fmt: skip

    def XMLDesc(self, flags=0):
        self._conn.wait()  # a remote procedure call
        return self._xml

    def ID(self):
        return self._id  # kept by the client library: no call

    def name(self):
        return self._name

    def UUIDString(self):
        return self._xml.split("<uuid>")[1].split("<")[0]

    def isActive(self):
        self._conn.wait()
        return self._id != -1

    def connect(self):
        return self._conn


class FakeNetwork:
    def __init__(self, conn):
        self._conn = conn

    def XMLDesc(self, flags=0):
        self._conn.wait()
        return NET_XML

    def DHCPLeases(self):
        self._conn.wait()
        return []


class FakeConn:
    def __init__(self, node, count, remote):
        self.remote = remote
        if remote:
            time.sleep(Costs.ssh)  # the SSH handshake of qemu+ssh://
        self.domains = [FakeDomain(self, i, f"vm-{node}-{i:04d}", i % 20 == 0) for i in range(count)]

    def wait(self):
        if self.remote:
            time.sleep(Costs.rtt)

    def getAllDomainStats(self, stats, flags=0):
        self.wait()
        return [(d, {"state.state": 1 if d.ID() != -1 else 5}) for d in self.domains]

    def listAllDomains(self, flags=0):
        self.wait()
        return list(self.domains)

    def listAllNetworks(self, flags=0):
        self.wait()
        return [FakeNetwork(self)]

    def networkLookupByName(self, name):
        self.wait()
        if name != "default":
            raise libvirt.libvirtError("no network")
        return FakeNetwork(self)

    def lookupByName(self, name):
        self.wait()
        for d in self.domains:
            if d.name() == name:
                return d
        raise libvirt.libvirtError("Domain not found")

    def close(self):
        pass


_ORIGINAL = {}


def setup(nodes, per_node):
    """The application on node000, with nodes-1 other members, per_node VMs on each."""
    from app.core import (
        cluster_lead,
        cluster_setup,
        database,
        inventory_cache,
        libvirt_utils,
        network_alloc,
        rolling_update,
    )
    from app.core.security import hash_password
    from app.routers.vms import inventory as vm_inventory

    # Rows are written as on a single node (in cluster mode their ids would come from hyperlite-cfs), then the
    # cluster mode is simulated for the measures.
    cluster_lead.mode, cluster_lead.read_only_reason = _ORIGINAL.setdefault(
        "lead", (cluster_lead.mode, cluster_lead.read_only_reason)
    )
    database.init_db()
    names = [f"node{i:03d}" for i in range(1, nodes)]
    with database.get_conn() as db:
        db.execute("DELETE FROM nodes")
        db.executemany(
            "INSERT INTO nodes (name, hostname, statut, added_at) VALUES (?, ?, 'en_ligne', '2026-10-18T00:00:00+00:00')",
            [(n, f"10.0.{i // 250}.{i % 250 + 1}") for i, n in enumerate(names)],
        )
        if not db.execute("SELECT 1 FROM users WHERE username = 'admin'").fetchone():
            db.execute("INSERT INTO users (username, hashed_password, role) VALUES ('admin', ?, 'admin')",
                       (hash_password("Scale-simulation-1"),))  # fmt: skip
        db.commit()

    def open_conn(node=None):
        remote = bool(node) and node != "local"
        return FakeConn(node or "node000", per_node, remote)

    for module in (libvirt_utils, vm_inventory):
        module.open_conn = open_conn
    import app.core.libvirt_utils as lu

    lu.open_conn = open_conn
    cluster_lead.mode = lambda record=True: "cluster"
    cluster_lead.read_only_reason = lambda: None
    cluster_setup.shared_conf = lambda: {"membres": [{"nom": n} for n in ["node000", *names]]}

    def peer_api(node, username, method, path, body=None, timeout=60, connect_timeout=5):
        time.sleep(Costs.ssh + Costs.rtt)  # an SSH session to the member, then its answer
        return 200, {"items": [], "unreachable": []} if "?" in path or path.startswith(("/tasks", "/audit")) else []

    rolling_update.peer_api = peer_api
    inventory_cache.TTL_S = 0  # every measure reads again, as the first request of a refresh does
    client = Http(_serve())
    r = client.post("/auth/login", data={"username": "admin", "password": "Scale-simulation-1"})
    token = r.json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}, names, network_alloc


_server = None


def _serve():
    """The application in a real uvicorn server on a free local port (requests run concurrently, as on a node), once."""
    global _server
    if _server is None:
        import socket
        import threading

        import uvicorn

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        app = __import__("app.main", fromlist=["app"]).app
        # No start-up work (pollers, metrics, schedulers): only the requests are measured.
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False, lifespan="off")
        server = uvicorn.Server(config)
        threading.Thread(target=server.run, daemon=True).start()
        while not server.started:
            time.sleep(0.05)
        _server = f"http://127.0.0.1:{port}"
    return _server


class Http:
    """The few calls the measures need, over real HTTP (httpx, a development dependency)."""

    def __init__(self, base):
        import httpx

        self.c = httpx.Client(base_url=base, timeout=600, limits=httpx.Limits(max_connections=64))

    def get(self, path, headers=None):
        return self.c.get(path, headers=headers)

    def post(self, path, data=None):
        return self.c.post(path, data=data)


def timed(fn, runs):
    out = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t0) * 1000)
    out.sort()
    return out[len(out) // 2], out[-1]


def scenario(nodes, total, rtt_ms, ssh_ms, runs):
    Costs.rtt, Costs.ssh = rtt_ms / 1000, ssh_ms / 1000
    per_node = max(1, total // nodes)
    client, headers, names, network_alloc = setup(nodes, per_node)

    def ui_vm_list():
        with cf.ThreadPoolExecutor(2) as pool:
            a = pool.submit(client.get, "/vms", headers=headers)
            b = pool.submit(client.get, "/nodes", headers=headers)
            for r in (a.result(), b.result()):
                if r.status_code != 200:
                    raise RuntimeError(f"{r.request.url}: {r.status_code} {r.text[:200]}")
        with cf.ThreadPoolExecutor(6) as pool:  # the browser: 6 requests at a time per host
            for r in pool.map(lambda n: client.get(f"/vms?node={n}", headers=headers), names):
                if r.status_code != 200:
                    raise RuntimeError(f"{r.request.url}: {r.status_code} {r.text[:200]}")

    label = f"{nodes:>3} nodes x {per_node:>4} VMs, rtt {rtt_ms:>4} ms, ssh {ssh_ms} ms"
    for name, fn in (
        ("UI VM list", ui_vm_list),
        ("Activity: tasks of every node", lambda: client.get("/tasks?limit=50&cluster=true", headers=headers)),
        ("Audit of every node", lambda: client.get("/audit?limit=50&cluster=true", headers=headers)),
        ("NAT address search (new VM)", lambda: network_alloc.cluster_used_ips("default")),
    ):
        p50, worst = timed(fn, runs)
        print(f"{label} | {name:32s} p50 {p50:8.0f} ms  max {worst:8.0f} ms", flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--nodes", default="3,10,100")
    p.add_argument("--vms-total", type=int, default=1000)
    p.add_argument("--rtt-ms", default="0.5,20")
    p.add_argument("--ssh-ms", type=float, default=200)
    p.add_argument("--runs", type=int, default=3)
    a = p.parse_args()
    for rtt in (float(x) for x in a.rtt_ms.split(",")):
        for n in (int(x) for x in a.nodes.split(",")):
            scenario(n, a.vms_total, rtt, a.ssh_ms, a.runs)


if __name__ == "__main__":
    main()
