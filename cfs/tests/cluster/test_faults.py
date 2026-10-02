"""Fault tests of hyperlite-cfs (design, sections 8.1 step B3 and 9), on the lab of test_cluster.py.

Each scenario runs writers on the nodes while a fault strikes, lets the cluster settle, then runs the checker: every
answered write is on every node, nothing refused is anywhere, and every node holds the same state. The faults: a
daemon killed while it sends, while it receives, while it is the source of a state transfer; a node's whole cluster
stack killed; a node back after a thousand writes; a lock holder that dies or hangs. Then the soak: random faults,
HYPERLITE_CFS_ROUNDS rounds (5 by default, 1,000 every night: the exit criterion of phase B)."""

import os
import time

import pytest
from harness import (
    NODES,
    VMS,
    check,
    clean,
    get,
    lab,
    pause,
    same_everywhere,
    seeded,
    settled,
    stop_writers,
    until,
    writers_on,
)

from app.core.cfs_client import Locked, ReadOnly, Synchronising

ROUNDS = int(os.environ.get("HYPERLITE_CFS_ROUNDS") or 5)


@pytest.fixture(autouse=True)
def whole(nodes):
    """Every scenario starts and ends on a whole, writable cluster, whatever the previous one left."""
    for n in NODES:
        lab("heal", n)
        lab("start", n)
    settled(nodes)
    yield


# Written for three nodes; test_two_nodes.py covers the two-node lab.
pytestmark = pytest.mark.skipif(len(NODES) != 3, reason="needs the three-node lab")


def test_the_sender_killed_mid_write_loses_no_answered_write(nodes, socks):
    writers = writers_on(socks, NODES, "/b3/sender")
    time.sleep(1)
    lab("stop", 3)  # node 3's daemon dies with its own changes in flight
    time.sleep(2)
    lab("start", 3)
    time.sleep(1)
    stop_writers(writers)
    assert check(nodes, writers) > 0
    clean(nodes[0], "/b3/sender")


def test_a_receiver_killed_mid_write_loses_no_answered_write(nodes, socks):
    writers = writers_on(socks, (1,), "/b3/receiver")
    time.sleep(1)
    lab("stop", 2)
    time.sleep(2)
    lab("start", 2)
    time.sleep(1)
    stop_writers(writers)
    assert check(nodes, writers) > 0
    clean(nodes[0], "/b3/receiver")


def test_a_node_whose_cluster_stack_crashes_comes_back(nodes, socks):
    writers = writers_on(socks, NODES, "/b3/crash")
    time.sleep(1)
    lab("crash", 2)  # Corosync and the daemon, as a kernel panic would leave them
    time.sleep(3)
    lab("start", 2)
    time.sleep(1)
    stop_writers(writers)
    assert check(nodes, writers) > 0
    clean(nodes[0], "/b3/crash")


def test_the_source_killed_during_a_transfer_another_member_takes_over(nodes, socks):
    # Node 3 misses enough data for the transfer to take a while, then comes back; the source of the transfer (node 1:
    # the latest term and version, then the lowest id) dies as soon as node 3 is back in.
    lab("cut", 3)
    until(lambda: not nodes[2].status().quorate, what="node 3's quorum")
    until(lambda: nodes[0].put("/b3/source/start", b"x") > 0, what="the majority")
    blob = os.urandom(512 * 1024)
    for i in range(48):  # 24 MiB
        nodes[i % 2].put(f"/b3/source/blob{i}", blob)
    behind = nodes[2].status().version
    lab("heal", 3)
    until(lambda: nodes[2].status().quorate, timeout=60, what="node 3 back in")
    lab("stop", 1)
    # Nodes 2 and 3 are a majority: node 2 now sends, and node 3 ends with the whole state.
    until(lambda: nodes[2].put("/b3/source/after", b"x") > 0, what="the two remaining nodes")
    assert nodes[2].status().version > behind
    assert nodes[2].get("/b3/source/blob47").data == blob
    lab("start", 1)
    settled(nodes)
    assert nodes[0].get("/b3/source/blob47").data == blob
    clean(nodes[0], "/b3/source")


def test_a_node_back_after_a_thousand_writes_has_every_one(nodes, socks):
    lab("stop", 3)
    for i in range(1000):
        nodes[i % 2].put(f"/b3/thousand/{i}", str(i).encode())
    lab("start", 3)
    settled(nodes)
    names = {child.name for child in nodes[2].list("/b3/thousand")}
    assert names == {str(i) for i in range(1000)}
    assert nodes[2].get("/b3/thousand/999").data == b"999"
    clean(nodes[0], "/b3/thousand")


def test_a_lone_daemon_refuses_changes_even_when_corosync_is_quorate(nodes, socks):
    # The daemons of nodes 1 and 2 die, their Corosync runs on: Corosync has its quorum, but node 3's daemon alone
    # would be the only holder of what it accepted, and a later majority without it would lose that (design 8.4).
    lab("stop", 1)
    lab("stop", 2)
    until(lambda: not nodes[2].status().quorate, timeout=30, what="node 3 counting only the daemons' votes")
    with pytest.raises((ReadOnly, Synchronising)):
        nodes[2].put("/b3/lone", b"x")
    assert get(nodes[2], "/b3/lone") is None
    lab("start", 1)
    until(lambda: nodes[2].put("/b3/lone", b"x") > 0, what="two daemons, a majority again")
    lab("start", 2)
    settled(nodes)
    nodes[0].delete("/b3/lone")


def test_the_lock_of_a_daemon_that_died_is_released(nodes, socks):
    nodes[1].lock("vm:200", "migrate@node2", ttl=600)
    with pytest.raises(Locked):
        nodes[0].lock("vm:200", "backup@node1")
    lab("stop", 2)

    def take():
        nodes[0].lock("vm:200", "backup@node1")
        return True

    until(take, what="the lock of the node that left")
    nodes[0].unlock("vm:200", "backup@node1")


def test_the_lock_of_a_holder_that_hangs_expires(nodes, socks):
    # The holder (a migration task, say) hangs while its node stays a member: only the TTL frees the lock.
    nodes[1].lock("vm:201", "migrate@node2", ttl=2)
    with pytest.raises(Locked):
        nodes[0].lock("vm:201", "backup@node1")
    time.sleep(3)
    nodes[0].lock("vm:201", "backup@node1")
    nodes[0].unlock("vm:201", "backup@node1")


# The soak's faults: (name, how many nodes it hits, what to do to them, what undoes it).
FAULTS = [
    ("stop", 1, "stop", "start"),  # a daemon dies
    ("crash", 1, "crash", "start"),  # a node's cluster stack dies
    ("cut", 1, "cut", "heal"),  # a node is cut off
    ("stop-two", 2, "stop", "start"),  # the majority dies: the last node must refuse every change
    ("crash-two", 2, "crash", "start"),
]


def test_soak(nodes, socks):
    rng = seeded()
    total = 0
    started = time.monotonic()
    for r in range(ROUNDS):
        name, count, do, undo = rng.choice(FAULTS)
        victims = rng.sample(NODES, count)
        prefix = f"/soak/{r}"
        writers = writers_on(socks, NODES, prefix)
        pause(rng, 0.2, 1.5)
        for v in victims:
            lab(do, v)
            pause(rng, 0, 0.5)
        pause(rng, 0.5, 4 if VMS else 3)
        for v in victims:
            lab(undo, v)
        pause(rng, 0.2, 1.5)
        stop_writers(writers)
        try:
            total += check(nodes, writers)
        except AssertionError as e:
            raise AssertionError(f"round {r} ({name} on node(s) {victims}): {e}") from None
        clean(nodes[0], prefix)
        print(f"round {r + 1}/{ROUNDS}: {name} on {victims}, {total} answered writes checked so far", flush=True)
    assert same_everywhere(nodes)
    print(f"soak: {ROUNDS} rounds, {total} answered writes, {time.monotonic() - started:.0f} s")
