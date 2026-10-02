"""What the cluster tests share: the lab (lab.sh, or the VMs of lab/vm.sh), waiting for the cluster to settle, and the
checker of the design (docs/design/hyperlite-cfs.md, section 9): every answered write is on every node once the
cluster is whole again, and nothing refused or never sent is anywhere."""

import contextlib
import os
import random
import subprocess
import threading
import time
from pathlib import Path

from app.core.cfs_client import (
    MUST_NOT_EXIST,
    CfsClient,
    CfsError,
    Conflict,
    NotFound,
    ReadOnly,
    Synchronising,
    Uncertain,
)

BIN = os.environ.get("HYPERLITE_CFS_BIN", "")
# The lab: three namespaces on this machine (lab.sh, needs the daemon built here), or three Debian VMs that build
# their own (lab/vm.sh, set HYPERLITE_CFS_LAB to its path). Both scripts take the same commands.
LAB = Path(os.environ.get("HYPERLITE_CFS_LAB") or Path(__file__).with_name("lab.sh"))
VMS = LAB.name == "vm.sh"
# The namespaces lab takes its size and its QDevice arbiter from the environment (lab.sh); the VMs are always three.
NODES = tuple(range(1, int(os.environ.get("HYPERLITE_LAB_NODES") or 3) + 1))
QDEVICE = os.environ.get("HYPERLITE_LAB_QDEVICE") == "1"


def lab(*args):
    subprocess.run(["bash", str(LAB), *map(str, args)], check=True)


def tool(node, *args):
    """`hyperlite-cfs ARGS...` on a node of the lab; the completed process, whatever its exit status."""
    return subprocess.run(["bash", str(LAB), "tool", str(node), *args], capture_output=True, text=True)


def until(check, timeout=120, what="the cluster"):
    """Retry `check` until it returns a true value; the cluster needs a few seconds after each membership change."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (CfsError, OSError) as e:
            last = e
        time.sleep(0.2)
    raise AssertionError(f"{what} did not settle: {last}")


def get(client, path):
    try:
        return client.get(path)
    except NotFound:
        return None


def same_everywhere(clients):
    states = [c.status() for c in clients]
    return all(s.quorate for s in states) and len({(s.version, s.checksum) for s in states}) == 1


def writable(clients, probe="/probe"):
    """Every node accepts a change: the members agree and are quorate."""
    for i, c in enumerate(clients):
        c.put(f"{probe}/{i + 1}", b"x")
    return True


def settled(clients, what="the cluster"):
    until(lambda: writable(clients), what=what)
    until(lambda: same_everywhere(clients), what=what)


class Writer(threading.Thread):
    """Creates new entries on one node, one after the other, and records what each answer says about them:
    - acked: answered OK, so it must be on every node once the cluster is whole;
    - refused: refused before being sent (no quorum, members not agreeing), so it must be nowhere;
    - uncertain: no answer (connection lost, timeout) or "uncertain", so it may be anywhere or nowhere, but alike."""

    def __init__(self, sock, node, prefix):
        super().__init__(daemon=True)
        self.client = CfsClient(sock, timeout=5)
        self.node = node
        self.dir = f"{prefix}/n{node}"
        self.acked, self.refused, self.uncertain, self.errors = set(), set(), set(), []
        self.halt = threading.Event()

    def run(self):
        i = 0
        while not self.halt.is_set():
            name = str(i)
            i += 1
            try:
                self.client.put(f"{self.dir}/{name}", f"{self.node}:{name}".encode(), expected=MUST_NOT_EXIST)
                self.acked.add(name)
                continue
            except (ReadOnly, Synchronising):
                self.refused.add(name)
            except (Uncertain, OSError):
                self.uncertain.add(name)
            except Conflict as e:  # a name never used before cannot exist: a change applied twice, or a stale state
                self.errors.append(f"{name}: conflict on a new entry: {e}")
            except CfsError as e:
                self.errors.append(f"{name}: {e}")
            time.sleep(0.05)

    def stop(self):
        self.halt.set()
        self.join(timeout=30)
        self.client.close()


def check(clients, writers):
    """The checker: run once the cluster settled again."""
    settled(clients, what="the cluster after the fault")
    problems = []
    for w in writers:
        problems += [f"node {w.node} {e}" for e in w.errors]
        for k, c in enumerate(clients, start=1):
            try:
                present = {child.name for child in c.list(w.dir)}
            except NotFound:
                present = set()
            lost = w.acked - present
            if lost:
                problems.append(f"node {k} lost {len(lost)} answered write(s) of node {w.node}: {sorted(lost)[:10]}")
            ghost = present & w.refused
            if ghost:
                problems.append(f"node {k} holds {len(ghost)} refused write(s) of node {w.node}: {sorted(ghost)[:10]}")
            unknown = present - w.acked - w.uncertain - w.refused
            if unknown:
                problems.append(f"node {k} holds write(s) node {w.node} never sent: {sorted(unknown)[:10]}")
        # The data itself, on one node: the states are identical, so the others hold the same.
        for name in sorted(w.acked):
            entry = get(clients[0], f"{w.dir}/{name}")
            if entry is None or entry.data != f"{w.node}:{name}".encode():
                problems.append(f"{w.dir}/{name} holds {entry.data if entry else None!r}")
                break
    assert not problems, "\n".join(problems)
    return sum(len(w.acked) for w in writers)


def writers_on(socks, nodes, prefix):
    ws = [Writer(socks[n - 1], n, prefix) for n in nodes]
    for w in ws:
        w.start()
    return ws


def stop_writers(writers):
    for w in writers:
        w.stop()


def clean(client, prefix):
    """Delete everything under `prefix`, so the state stays small across a thousand rounds."""
    try:
        children = client.list(prefix)
    except NotFound:
        return
    for child in children:
        path = f"{prefix}/{child.name}"
        if child.is_dir:
            clean(client, path)
        else:
            until(lambda path=path: _delete(client, path))


def _delete(client, path):
    with contextlib.suppress(NotFound):
        client.delete(path)
    return True


def pause(rng, low, high):
    time.sleep(rng.uniform(low, high))


def seeded():
    """The soak's random source: HYPERLITE_CFS_SEED replays a run, otherwise a new seed is printed."""
    seed = int(os.environ.get("HYPERLITE_CFS_SEED") or random.SystemRandom().randrange(1 << 32))
    print(f"\nsoak seed: {seed} (HYPERLITE_CFS_SEED={seed} replays it)")
    return random.Random(seed)  # noqa: S311 - fault timing, not security: a seed must replay it
