"""A stop asked with forcer_apres: the request is sent again halfway, and a VM still running at the end is stopped by
force (a guest ignored the ACPI button while still booting during the lab stability run)."""

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


class _Conn:
    def __init__(self, domain):
        self.domain = domain

    def lookupByName(self, name):
        return self.domain

    def close(self):
        pass


@pytest.fixture()
def run(database, monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(lifecycle.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(lifecycle.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(guest_agent, "shutdown", lambda d: setattr(d, "requests", d.requests + 1) or "acpi")

    def go(domain, seconds=60):
        monkeypatch.setattr(libvirt_utils, "open_conn", lambda node=None: _Conn(domain))
        domain.requests = 1  # the request the endpoint sent
        lifecycle._force_after("web", None, seconds, "alice")
        return domain

    return go


def test_a_guest_that_ignores_the_request_is_asked_again_then_forced(run):
    d = run(_Domain(stops_after_requests=99))
    assert d.requests == 2 and d.destroyed


def test_a_guest_that_stops_on_the_second_request_is_not_forced(run):
    d = run(_Domain(stops_after_requests=2))
    assert d.requests == 2 and not d.destroyed


def test_the_delay_is_bounded(client, auth_headers):
    r = client.post("/vms/web/stop?forcer_apres=5", headers=auth_headers("root"))
    assert r.status_code == 422
