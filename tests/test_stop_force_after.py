"""A shutdown request a guest ignored is sent again (twice in a 24 h lab run, just after a live migration, the guest
healthy); asked with force_after, a VM still running at the end is stopped by force."""

import pytest

from app.core import guest_agent, libvirt_utils
from app.routers.vms import lifecycle


class _Domain:
    def __init__(self, stops_after_requests):
        self.requests, self.destroyed, self.stops_after = 0, False, stops_after_requests

    def isActive(self):
        return not self.destroyed and self.requests < self.stops_after

    def destroy(self):
        self.destroyed = True

    def name(self):
        return "web"


class _Conn:
    def __init__(self, domain):
        self.domain = domain

    def lookupByName(self, name):
        return self.domain

    def close(self):
        pass


@pytest.fixture()
def run(database, monkeypatch, daemon_libvirt):
    clock = {"t": 0.0}
    monkeypatch.setattr(lifecycle.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(lifecycle.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(guest_agent, "shutdown", lambda d: setattr(d, "requests", d.requests + 1) or "acpi")

    def go(domain, seconds=60):
        monkeypatch.setattr(libvirt_utils, "open_conn", lambda node=None: _Conn(domain))
        daemon_libvirt(_Conn(domain))  # hyperlite-daemon asks again and forces, in this process in the tests
        domain.requests = 1  # the request the endpoint sent
        lifecycle._watch_stop("web", None, "alice", seconds)
        return domain

    return go


def test_a_guest_that_ignores_the_request_is_asked_again_then_forced(run):
    d = run(_Domain(stops_after_requests=99))
    assert d.requests == 2 and d.destroyed


def test_a_guest_that_stops_on_the_second_request_is_not_forced(run):
    d = run(_Domain(stops_after_requests=2))
    assert d.requests == 2 and not d.destroyed


def test_the_delay_is_bounded(client, auth_headers):
    r = client.post("/vms/web/stop?force_after=5", headers=auth_headers("root"))
    assert r.status_code == 422


def test_a_plain_stop_asks_again_and_never_forces(run, monkeypatch, daemon_libvirt):
    d = _Domain(stops_after_requests=99)
    monkeypatch.setattr(libvirt_utils, "open_conn", lambda node=None: _Conn(d))
    daemon_libvirt(_Conn(d))
    d.requests = 1
    lifecycle._watch_stop("web", None, "alice")
    assert d.requests == 3 and not d.destroyed  # asked again at 30 s and 90 s


def test_a_plain_stop_lost_once_stops_on_the_next_request(run, monkeypatch, daemon_libvirt):
    d = _Domain(stops_after_requests=2)
    monkeypatch.setattr(libvirt_utils, "open_conn", lambda node=None: _Conn(d))
    daemon_libvirt(_Conn(d))
    d.requests = 1
    lifecycle._watch_stop("web", None, "alice")
    assert d.requests == 2 and not d.destroyed


def test_a_long_force_delay_asks_again_twice_before(run):
    d = run(_Domain(stops_after_requests=99), seconds=180)
    assert d.requests == 3 and d.destroyed


def test_a_short_force_delay_asks_again_halfway(run):
    d = run(_Domain(stops_after_requests=99), seconds=20)
    assert d.requests == 2 and d.destroyed
