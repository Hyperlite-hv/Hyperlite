"""One reading of the VMs serves the requests of the same moment, and any change drops it."""

import threading
import time

from app.core import inventory_cache


def test_a_reading_is_shared_for_a_moment_then_read_again(monkeypatch):
    inventory_cache.invalidate()
    calls = []
    read = lambda: calls.append(1) or len(calls)  # noqa: E731
    assert inventory_cache.get("k", read) == 1
    assert inventory_cache.get("k", read) == 1  # within TTL_S: the same reading
    monkeypatch.setattr(inventory_cache, "TTL_S", 0.0)
    assert inventory_cache.get("k", read) == 2


def test_requests_arriving_during_a_reading_wait_for_it():
    inventory_cache.invalidate()
    calls = []

    def slow():
        calls.append(1)
        time.sleep(0.2)
        return "vms"

    results = []
    threads = [threading.Thread(target=lambda: results.append(inventory_cache.get("k2", slow))) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == ["vms"] * 5 and len(calls) == 1


def test_a_change_drops_it_even_a_reading_in_progress(database):
    from app.core.audit import log_action

    inventory_cache.invalidate()
    assert inventory_cache.get("k3", lambda: "before") == "before"
    log_action("alice", "start_vm", "web", "succes")
    assert inventory_cache.get("k3", lambda: "after") == "after"
    log_action("alice", "list_vms", "vms", "succes")  # a read changes nothing
    assert inventory_cache.get("k3", lambda: "again") == "after"

    # A reading that started before a change is not kept: it may not show it.
    started, release = threading.Event(), threading.Event()

    def racing():
        started.set()
        release.wait(2)
        return "stale"

    t = threading.Thread(target=lambda: inventory_cache.get("k4", racing))
    t.start()
    started.wait(2)
    inventory_cache.invalidate()
    release.set()
    t.join()
    assert inventory_cache.get("k4", lambda: "fresh") == "fresh"


def test_a_request_that_changes_something_drops_it(client, auth_headers):
    inventory_cache.invalidate()
    inventory_cache.get("k5", lambda: "before")
    client.post("/auth/logout", headers=auth_headers("alice"))
    assert inventory_cache.get("k5", lambda: "after") == "after"
