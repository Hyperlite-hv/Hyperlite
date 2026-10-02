"""hyperlite-cfs on a real Corosync: three nodes on one machine (lab.sh), driven through the Python client.

What it shows: a change made on one node is applied by every node; two nodes creating the same entry at once get one
success and one conflict; many changes from every node leave identical states; a large file crosses Corosync; a node
cut from the others refuses changes while the majority goes on; the locks of a node that left are released; a node
that comes back takes the majority's state.

Needs root, iproute2, corosync and the daemon: set HYPERLITE_CFS_BIN to the binary and HYPERLITE_CFS_CLUSTER=1 (the
CI's cfs job does). The scenarios share one cluster (conftest.py) and run in order; they leave it whole."""

import os
import threading

import pytest
from harness import NODES, get, lab, same_everywhere, until

from app.core.cfs_client import ANY_VERSION, MUST_NOT_EXIST, Conflict, ReadOnly

# Written for three nodes; test_two_nodes.py covers the two-node lab.
pytestmark = pytest.mark.skipif(len(NODES) != 3, reason="needs the three-node lab")


def test_a_change_made_on_one_node_is_on_every_node(nodes):
    version = nodes[0].put("/vms/100.json", b'{"name": "web"}')
    for c in nodes[1:]:
        entry = until(lambda c=c: get(c, "/vms/100.json"))
        assert entry.data == b'{"name": "web"}' and entry.version == version


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
    assert until(lambda: get(nodes[2], "/big")).data == data


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
