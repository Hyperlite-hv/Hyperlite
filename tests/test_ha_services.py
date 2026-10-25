"""HA's two services (app/ha_services; docs/design/privilege-separation.md, section 14, 4c): the watchdog, the node's
heartbeat and its HA VMs in hyperlite-ha-lrm; the recovery and load balancing's evaluation in hyperlite-ha-crm; what
the API's pages read of them published."""

import os
import signal
import threading
import time
from pathlib import Path

import pytest

from app import ha_services
from app.core import drs, ha_watch

UNITS = Path(__file__).resolve().parents[1] / "installer"


@pytest.fixture()
def started(monkeypatch):
    names = []
    for module, name in (
        ("app.core.ha_selffence", "start_selffence"),
        ("app.core.ha_monitor", "start"),
        ("app.core.drs", "start_carry_out"),
        ("app.core.ha_watch", "start_ha_watch"),
        ("app.core.ha_recovery", "start"),
        ("app.core.drs", "start_evaluate"),
    ):
        key = f"{module.rsplit('.', 1)[1]}.{name}"
        monkeypatch.setattr(f"{module}.{name}", lambda key=key: names.append(key))
    return names


def test_each_service_starts_its_own_loops(started):
    ha_services.start(ha_services.LRM)
    assert started == ["ha_selffence.start_selffence", "ha_monitor.start", "drs.start_carry_out"]
    started.clear()
    ha_services.start(ha_services.CRM)
    assert started == ["ha_watch.start_ha_watch", "ha_recovery.start", "drs.start_evaluate"]
    started.clear()
    ha_services.start(None)  # the API, without the services
    assert len(started) == 6


def _run(monkeypatch, role):
    from app.ha_services import __main__ as entry

    ran = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr("app.core.database.init_db", lambda: ran.append("schema"))
    monkeypatch.setattr("app.core.tasks.close_interrupted_tasks", lambda: ran.append("tasks"))
    monkeypatch.setattr("app.core.statd.start", lambda: ran.append("heartbeat"))
    monkeypatch.setattr(
        "app.core.cluster_lead.start_lead_loop",
        lambda follow_setup, lock=None, healthy=None: ran.append(("lead", lock)),
    )
    monkeypatch.setattr(ha_services, "start", lambda role: ran.append(role))
    monkeypatch.setattr("app.core.ha_selffence.stop", lambda: ran.append("disarmed"))
    monkeypatch.delenv("HYPERLITE_SERVICE", raising=False)
    previous = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    timer = threading.Timer(0.3, os.kill, (os.getpid(), signal.SIGTERM))
    timer.start()
    try:
        entry.main([role])
    finally:
        timer.cancel()
        signal.signal(signal.SIGTERM, previous[0])
        signal.signal(signal.SIGINT, previous[1])
    service = os.environ.pop("HYPERLITE_SERVICE")
    return ran, service


def test_the_lrm_sends_the_heartbeat_and_disarms_the_watchdog_when_stopped(monkeypatch):
    """The leader judges a member lost by its heartbeat: it stops with the watchdog's feeding, never apart from it."""
    ran, service = _run(monkeypatch, "lrm")
    assert ran == ["schema", "tasks", "heartbeat", ha_services.LRM, "disarmed"]
    assert service == ha_services.LRM


def test_the_crm_renews_the_lead_and_never_touches_the_watchdog(monkeypatch):
    ran, service = _run(monkeypatch, "crm")
    assert ran == ["schema", "tasks", ("lead", "hyperlite-ha-lead"), ha_services.CRM]
    assert service == ha_services.CRM


def test_the_api_reads_load_balancing_as_the_crm_published_it(database, monkeypatch):
    monkeypatch.setenv(ha_services.SEPARATE_ENV, "1")
    monkeypatch.setenv("HYPERLITE_SERVICE", ha_services.CRM)
    monkeypatch.setitem(drs._state, "spread", 42.0)
    monkeypatch.setitem(drs._state, "paused", "2 migrations failed in a row")
    drs._publish()
    monkeypatch.setenv("HYPERLITE_SERVICE", "api")
    assert drs.state()["spread"] == 42.0 and drs.state()["paused"]
    # Saving the settings in the API resumes it in the CRM, at its next round.
    monkeypatch.setenv("HYPERLITE_SERVICE", ha_services.CRM)
    seen = drs._take_requests(drs._UNSEEN)  # its first round: nothing asked yet
    monkeypatch.setenv("HYPERLITE_SERVICE", "api")
    time.sleep(0.01)
    drs.resume()
    assert drs._state["paused"]  # the API's own memory is not the CRM's
    monkeypatch.setenv("HYPERLITE_SERVICE", ha_services.CRM)
    drs._take_requests(seen)
    assert drs._state["paused"] is None


def test_a_manual_move_cools_down_in_the_crm(database, monkeypatch):
    monkeypatch.setenv(ha_services.SEPARATE_ENV, "1")
    monkeypatch.setenv("HYPERLITE_SERVICE", "api")
    drs._cool_down_elsewhere("web")
    monkeypatch.setenv("HYPERLITE_SERVICE", ha_services.CRM)
    monkeypatch.setattr(drs, "_moved", {})
    drs._take_requests(drs._UNSEEN)
    assert time.monotonic() - drs._moved["web"] < 5


def test_the_api_reads_has_dry_run_as_the_crm_published_it(database, monkeypatch):
    monkeypatch.setenv(ha_services.SEPARATE_ENV, "1")
    monkeypatch.setenv("HYPERLITE_SERVICE", "api")
    assert ha_watch._states() == {}
    from app.core import service_state

    service_state.publish(ha_watch.PUBLISHED, {"n2": {"failures": 3, "state": "suspect", "verified_at": None}})
    assert ha_watch._states()["n2"]["state"] == "suspect"


def test_the_units_split_ha_from_the_api():
    lrm = (UNITS / "hyperlite-ha-lrm.service").read_text()
    crm = (UNITS / "hyperlite-ha-crm.service").read_text()
    assert "-m app.ha_services lrm" in lrm and "-m app.ha_services crm" in crm
    for unit in (lrm, crm):
        assert "PartOf=hyperlite.service" in unit and "Environment=HYPERLITE_HA_SERVICE=1" in unit
    api = (UNITS / "hyperlite.service").read_text()
    assert "Environment=HYPERLITE_HA_SERVICE=1" in api
    assert "hyperlite-ha-lrm.service" in api and "hyperlite-ha-crm.service" in api
    postinst = (UNITS / "deb" / "postinst").read_text()
    assert "installer/hyperlite-ha-lrm.service" in postinst and "installer/hyperlite-ha-crm.service" in postinst


def test_the_lrms_heartbeat_carries_the_load_the_api_measured(database, monkeypatch):
    """Load balancing reads each member's load in its heartbeat: the LRM, which has no collector, sends the API's."""
    import time

    from app.core import metrics

    monkeypatch.setenv(ha_services.SEPARATE_ENV, "1")
    monkeypatch.setattr(metrics, "_latest", {"at": 0.0, "load": None})
    assert metrics.latest_load() is None
    metrics._record_load(time.time(), {"cpu_pct": 93.0, "mem_used_mb": 1, "mem_total_mb": 2}, {})
    monkeypatch.setattr(metrics, "_latest", {"at": 0.0, "load": None})  # the LRM: no collector of its own
    assert metrics.latest_load()["host"]["cpu_pct"] == 93.0


def test_a_crm_whose_rounds_stopped_lets_its_lead_go(monkeypatch):
    """A hung CRM on the leader would stop HA's recovery cluster-wide: it renews its own lock only while its rounds
    run, so the lock expires and another member's CRM takes over."""
    from app.core import cluster_lead

    taken = []

    class Client:
        def lock(self, name, holder, ttl):
            taken.append(name)

    monkeypatch.setattr(cluster_lead, "in_cluster", lambda: True)
    monkeypatch.setattr("app.repositories.cfs.shadow._get_client", lambda: Client())
    healthy = [True]
    monkeypatch.setattr(cluster_lead, "_lead", {"lock": cluster_lead.HA_LOCK, "healthy": lambda: healthy[0]})
    monkeypatch.setattr(cluster_lead, "_leading_until", 0.0)
    assert cluster_lead.renew() and cluster_lead.is_leader() and taken == [cluster_lead.HA_LOCK]
    healthy[0] = False
    assert not cluster_lead.renew() and not cluster_lead.is_leader() and taken == [cluster_lead.HA_LOCK]


def test_the_crm_is_healthy_while_its_rounds_run(monkeypatch):
    from app.core import ha_recovery

    monkeypatch.setattr(ha_recovery, "_round", [time.monotonic()])
    monkeypatch.setattr(drs, "_round", [time.monotonic()])
    assert ha_services.crm_healthy()
    monkeypatch.setattr(ha_recovery, "_round", [time.monotonic() - 3600])
    assert not ha_services.crm_healthy()


def test_the_pages_show_the_crms_lead(database, monkeypatch):
    monkeypatch.setenv(ha_services.SEPARATE_ENV, "1")
    assert not ha_services.crm_leads()  # nothing published: not leading
    monkeypatch.setenv("HYPERLITE_SERVICE", ha_services.CRM)
    monkeypatch.setattr("app.core.cluster_lead.is_leader", lambda: True)
    drs._publish()
    monkeypatch.setenv("HYPERLITE_SERVICE", "api")
    monkeypatch.setattr("app.core.cluster_lead.is_leader", lambda: False)  # the API's own lead does not count
    assert ha_services.crm_leads()
