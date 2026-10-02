"""One cluster for every test of this directory: the VM lab takes minutes to build, so the tests share it, in file
order (test_cluster.py leaves it whole for test_faults.py)."""

import os
from pathlib import Path

import pytest
from harness import BIN, NODES, VMS, lab, until, writable

from app.core.cfs_client import CfsClient

ENABLED = os.environ.get("HYPERLITE_CFS_CLUSTER") == "1" and os.geteuid() == 0 and (VMS or BIN)


def pytest_collection_modifyitems(config, items):
    if ENABLED:
        return
    skip = pytest.mark.skip(
        reason="needs root and HYPERLITE_CFS_CLUSTER=1, with HYPERLITE_CFS_BIN (namespaces) or HYPERLITE_CFS_LAB (VMs)"
    )
    for item in items:
        item.add_marker(skip)


def show_logs(work):
    for i in NODES:
        for name in ("cfs.log", "corosync.log"):
            log = work / f"n{i}" / name
            if log.exists():
                print(f"--- node {i}, {name}\n{log.read_text()[-3000:]}")


@pytest.fixture(scope="session")
def lab_dir(tmp_path_factory):
    work = tmp_path_factory.mktemp("lab")
    lab("down")
    assert VMS or Path(BIN).is_file(), f"HYPERLITE_CFS_BIN names {BIN}, which does not exist"
    try:
        lab("up", BIN or "-", work)
        socks = [work / f"n{i}" / "cfs.sock" for i in NODES]
        until(lambda: all(s.exists() for s in socks), timeout=600 if VMS else 90, what="the daemons")
        yield work
    finally:
        lab("down", work)  # the VM lab fetches each node's logs before destroying it
        show_logs(work)


@pytest.fixture(scope="session")
def socks(lab_dir):
    return [str(lab_dir / f"n{i}" / "cfs.sock") for i in NODES]


@pytest.fixture(scope="session")
def nodes(socks):
    clients = [CfsClient(s) for s in socks]
    # Writable once the three members agree: a probe write from each node succeeds.
    until(lambda: writable(clients), what="the first agreement")
    yield clients
    for c in clients:
        c.close()
