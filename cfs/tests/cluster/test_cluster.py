"""hyperlite-cfs on a real Corosync: three nodes on one machine (lab.sh), driven through the Python client.

What it shows: a change made on one node is applied by every node; two nodes creating the same entry at once get one
success and one conflict; many changes from every node leave identical states; a large file crosses Corosync; a node
cut from the others refuses changes while the majority goes on; the locks of a node that left are released; a node
that comes back takes the majority's state.

Needs root, iproute2, corosync and the daemon: set HYPERLITE_CFS_BIN to the binary and HYPERLITE_CFS_CLUSTER=1 (the
CI's cfs job does). The scenarios share one cluster and run in order."""

import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from app.core.cfs_client import ANY_VERSION, MUST_NOT_EXIST, CfsClient, CfsError, Conflict, ReadOnly

BIN = os.environ.get("HYPERLITE_CFS_BIN", "")
# The lab: three namespaces on this machine (lab.sh, needs the daemon built here), or three Debian VMs that build
# their own (lab/vm.sh, set HYPERLITE_CFS_LAB to its path). Both scripts take the same commands.
LAB = Path(os.environ.get("HYPERLITE_CFS_LAB") or Path(__file__).with_name("lab.sh"))
VMS = LAB.name == "vm.sh"

pytestmark = pytest.mark.skipif(
    os.environ.get("HYPERLITE_CFS_CLUSTER") != "1" or os.geteuid() != 0 or not (VMS or BIN),
    reason="needs root and HYPERLITE_CFS_CLUSTER=1, with HYPERLITE_CFS_BIN (namespaces) or HYPERLITE_CFS_LAB (VMs)",
)


def lab(*args):
    subprocess.run(["bash", str(LAB), *map(str, args)], check=True)


def until(check, timeout=90, what="the cluster"):
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
        time.sleep(0.5)
    raise AssertionError(f"{what} did not settle: {last}")


def show_logs(work):
    for i in (1, 2, 3):
        for name in ("cfs.log", "corosync.log"):
            log = work / f"n{i}" / name
            if log.exists():
                print(f"--- node {i}, {name}\n{log.read_text()[-3000:]}")


@pytest.fixture(scope="module")
def nodes(tmp_path_factory):
    work = tmp_path_factory.mktemp("lab")
    lab("down")
    assert VMS or Path(BIN).is_file(), f"HYPERLITE_CFS_BIN names {BIN}, which does not exist"
    clients = []
    try:
        lab("up", BIN or "-", work)
        socks = [work / f"n{i}" / "cfs.sock" for i in (1, 2, 3)]
        until(lambda: all(s.exists() for s in socks), timeout=600 if VMS else 90, what="the daemons")
        clients = [CfsClient(str(s)) for s in socks]
        # Writable once the three members agree: a probe write from each node succeeds.
        for i, c in enumerate(clients):
            until(lambda c=c, i=i: c.put(f"/probe/{i}", b"x") > 0, what="the first agreement")
        yield clients
    finally:
        for c in clients:
            c.close()
        lab("down", work)  # the VM lab fetches each node's logs before destroying it
        show_logs(work)


def same_everywhere(clients):
    states = [c.status() for c in clients]
    return len({(s.version, s.checksum) for s in states}) == 1


def test_a_change_made_on_one_node_is_on_every_node(nodes):
    version = nodes[0].put("/vms/100.json", b'{"name": "web"}')
    for c in nodes[1:]:
        entry = until(lambda c=c: _get(c, "/vms/100.json"))
        assert entry.data == b'{"name": "web"}' and entry.version == version


def _get(client, path):
    try:
        return client.get(path)
    except CfsError:
        return None


def test_two_nodes_creating_the_same_entry_get_one_success_and_one_conflict(nodes):
    results = {}

    def create(i):
        try:
            nodes[i].put("/vms/101.json", f"node{i}".encode(), expected=MUST_NOT_EXIST)
            results[i] = "ok"
        except Conflict:
            results[i] = "conflict"

    threads = [threading.Thread(target=create, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results.values()) == ["conflict", "conflict", "ok"]
    until(lambda: same_everywhere(nodes), what="the states")


def test_many_changes_from_every_node_leave_identical_states(nodes):
    for i in range(150):
        nodes[i % 3].put(f"/load/{i % 20}", str(i).encode(), expected=ANY_VERSION)
    until(lambda: same_everywhere(nodes), what="the states")


def test_a_large_file_crosses_corosync(nodes):
    data = os.urandom(1024 * 1024)
    nodes[1].put("/big", data)
    assert until(lambda: _get(nodes[2], "/big")).data == data


def test_a_node_cut_from_the_others_refuses_changes_and_its_locks_are_released(nodes):
    nodes[2].lock("vm:100", "migrate@node3", ttl=600)
    lab("cut", 3)
    until(lambda: not nodes[2].status().quorate, what="node 3's quorum")
    with pytest.raises(ReadOnly):
        nodes[2].put("/x", b"minority")
    # The majority goes on, and the lock node 3 held is free again.
    until(lambda: nodes[0].put("/x", b"majority") > 0, what="the majority")

    def take():
        nodes[0].lock("vm:100", "backup@node1")
        return True

    until(take, what="the lock")
    assert nodes[1].get("/x").data == b"majority"


def test_a_node_back_after_the_partition_takes_the_majority_state(nodes):
    lab("heal", 3)
    # Node 3 missed the majority's changes: it takes the whole state, and every node is writable again.
    until(lambda: nodes[2].put("/after-merge", b"from node 3") > 0, timeout=120, what="the merge")
    until(lambda: same_everywhere(nodes), what="the states")
    assert nodes[2].get("/x").data == b"majority"
    assert nodes[0].get("/after-merge").data == b"from node 3"
