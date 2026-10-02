"""Two-node clusters (design, sections 5 and 8.1 step B4), on the namespaces lab sized for two.

With a QDevice (HYPERLITE_LAB_NODES=2 HYPERLITE_LAB_QDEVICE=1): one node carries on when the other dies, every
answered write survives, and a node that restarts alone stays read-only until it has seen the other once, so it can
never write over what the survivor wrote alone. Corosync's wait_for_all does not ensure that with a QDevice; the
daemon does (corosync.c, members_hold_a_quorum).

Without one (HYPERLITE_LAB_NODES=2): the survivor is read-only, as it must be, until the administrator overrides the
expected votes with `hyperlite-cfs expected-votes 1`, which refuses without the cluster's name typed or given.

The CI runs this file once per lab; the tests of the other lab are skipped."""

import time

import pytest
from harness import NODES, QDEVICE, check, clean, lab, settled, stop_writers, tool, until, writers_on

from app.core.cfs_client import ReadOnly, Synchronising

CLUSTER = "hlcfs-test"  # cluster_name in lab.sh's corosync.conf

pytestmark = pytest.mark.skipif(len(NODES) != 2, reason="needs the two-node lab")
with_qdevice = pytest.mark.skipif(not QDEVICE, reason="needs the two-node lab with its QDevice")
without_qdevice = pytest.mark.skipif(QDEVICE, reason="needs the two-node lab without a QDevice")


@pytest.fixture(autouse=True)
def whole(nodes):
    for n in NODES:
        lab("start", n)
    if QDEVICE:
        lab("qnetd", "start")
    settled(nodes)
    yield


def refuses(client, path="/b4/refused"):
    try:
        client.put(path, b"x")
    except (ReadOnly, Synchronising):
        return True
    return False


@with_qdevice
def test_with_a_qdevice_one_node_carries_on_and_loses_nothing(nodes, socks):
    writers = writers_on(socks, NODES, "/b4/qdevice")
    time.sleep(1)
    lab("crash", 2)
    # Node 1 and the QDevice are 2 votes of 3: node 1 writes on alone.
    until(lambda: nodes[0].put("/b4/alone", b"node 1") > 0, what="node 1 with the QDevice's vote")
    time.sleep(2)
    lab("start", 2)
    time.sleep(1)
    stop_writers(writers)
    assert check(nodes, writers) > 0
    assert nodes[1].get("/b4/alone").data == b"node 1"
    clean(nodes[0], "/b4")


@with_qdevice
def test_a_node_restarting_alone_waits_for_the_other(nodes, socks):
    # Node 1 dies; node 2 writes on alone with the QDevice's vote; node 2 dies; node 1 comes back alone, stale.
    lab("crash", 1)
    until(lambda: nodes[1].put("/b4/written-by-2-alone", b"node 2") > 0, what="node 2 with the QDevice's vote")
    lab("crash", 2)
    lab("start", 1)
    until(lambda: nodes[0].status(), what="node 1's daemon")  # the client reconnects to the new daemon
    # wait_for_all: node 1 and the QDevice would be 2 votes of 3, but node 1 has not seen node 2 since it started, so
    # it may be the stale half of a split: it stays read-only.
    time.sleep(15)
    assert not nodes[0].status().quorate
    assert refuses(nodes[0])
    lab("start", 2)
    settled(nodes)
    # Node 2's write, made alone, survived: node 1's stale state never got to win.
    assert nodes[0].get("/b4/written-by-2-alone").data == b"node 2"
    # Once both were seen, losing one is survivable again.
    lab("crash", 2)
    until(lambda: nodes[0].put("/b4/after-wait", b"x") > 0, what="node 1 alone after wait_for_all was met")
    lab("start", 2)
    settled(nodes)
    clean(nodes[0], "/b4")


@without_qdevice
def test_the_override_refuses_on_a_quorate_cluster(nodes, socks):
    done = tool(1, "expected-votes", "1", "--confirm", CLUSTER)
    assert done.returncode != 0
    assert "nothing to override" in done.stderr


@without_qdevice
def test_without_a_qdevice_the_survivor_writes_only_after_the_typed_override(nodes, socks):
    lab("crash", 2)
    until(lambda: not nodes[0].status().quorate, what="node 1 without quorum")
    assert refuses(nodes[0])

    # Without a confirmation, or with a wrong one, nothing changes; the message names the missing node.
    unconfirmed = tool(1, "expected-votes", "1")
    assert unconfirmed.returncode != 0
    assert "node 2" in unconfirmed.stderr and "MISSING" in unconfirmed.stderr
    assert "nothing was changed" in unconfirmed.stderr
    wrong = tool(1, "expected-votes", "1", "--confirm", "another-cluster")
    assert wrong.returncode != 0
    assert refuses(nodes[0])

    done = tool(1, "expected-votes", "1", "--confirm", CLUSTER)
    assert done.returncode == 0, done.stderr
    until(lambda: nodes[0].put("/b4/override", b"written alone") > 0, what="node 1 after the override")

    # Node 2 comes back, takes node 1's state, and the cluster is a normal two-node cluster again.
    lab("start", 2)
    settled(nodes)
    assert nodes[1].get("/b4/override").data == b"written alone"
    clean(nodes[0], "/b4")


@without_qdevice
def test_a_vote_count_that_would_not_make_the_partition_quorate_is_refused(nodes, socks):
    lab("crash", 2)
    until(lambda: not nodes[0].status().quorate, what="node 1 without quorum")
    done = tool(1, "expected-votes", "2", "--confirm", CLUSTER)
    assert done.returncode != 0
    assert "stay read-only" in done.stderr
    lab("start", 2)
    settled(nodes)
