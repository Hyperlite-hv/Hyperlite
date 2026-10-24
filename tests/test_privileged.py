"""The API asks hyperlite-daemon for what needs root (app/core/privileged.py, docs/design/privilege-separation.md, step
3): the operations it names exist, their arguments are checked, and without the daemon the routes say so."""

import pathlib
import re

import pytest

import app.daemon.ops  # noqa: F401  (registers the operations)
from app.core import privileged
from app.daemon.registry import KINDS, OPERATIONS, DaemonError

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _named_operations():
    """Every operation name the API asks for: privileged.run("op", ...), the routes' privileged.ask("op", ...) and
    network_firewall's _daemon("op", ...) and the storage routes' _pool_work(node, "op", ...)."""
    names = set()
    for path in (ROOT / "app").rglob("*.py"):
        if "daemon" in path.parts:
            continue
        text = path.read_text()
        names |= set(re.findall(r'privileged\.(?:run|ask)\(\s*"([a-z_]+\.[a-z_]+)"', text))
        names |= set(re.findall(r'_daemon\(\s*"([a-z_]+\.[a-z_]+)"', text))
        names |= set(re.findall(r'_pool_work\(\s*\w+,\s*"([a-z_]+\.[a-z_]+)"', text))
    return names


def test_every_operation_the_api_asks_for_exists_in_the_daemon():
    named = _named_operations()
    assert {"host.set_dns", "host.upgrade", "host.schedule_power"} <= named  # the scan finds them
    assert {"network.firewall_apply", "network.vlan_aware_start", "network.vlan_apply_vm", "network.vlan_boot"} <= named
    assert {"network.create", "storage.zfs_pool_create", "storage.zfs_volume_delete", "tls.import"} <= named
    assert {"storage.pool_create", "storage.pool_rename", "storage.pool_delete", "storage.pool_check"} <= named
    assert {"storage.volume_create", "storage.volume_delete", "storage.pool_refresh", "storage.lvm_volumes"} <= named
    assert named <= set(OPERATIONS), sorted(named - set(OPERATIONS))


def test_the_arguments_are_checked_as_the_daemon_checks_them():
    with pytest.raises(DaemonError) as e:
        privileged.run("host.set_hostname", name="pve1", extra="x")
    assert e.value.kind == "invalid_arguments"
    with pytest.raises(DaemonError) as e:
        privileged.run("host.schedule_power", action="format-the-disk")
    assert e.value.kind == "invalid_arguments"


def test_a_setting_the_host_refuses_is_the_daemons_refusal_with_its_reason(monkeypatch):
    from app.core import host_system

    def refuse(name):
        raise host_system.SettingError("Invalid host name")

    monkeypatch.setattr(host_system, "set_hostname", refuse)
    with pytest.raises(DaemonError) as e:
        privileged.run("host.set_hostname", name="no way")
    assert (e.value.kind, e.value.detail) == ("refused", "Invalid host name")


def test_without_the_daemon_a_setting_answers_unavailable(client, auth_headers, monkeypatch, tmp_path):
    admin = auth_headers("root")
    monkeypatch.delenv(privileged.INPROCESS_ENV)
    monkeypatch.setenv("HYPERLITE_DAEMON_SOCKET", str(tmp_path / "none.sock"))
    r = client.put("/host/system/dns", json={"servers": ["9.9.9.9"]}, headers=admin)
    assert r.status_code == 503 and "hyperlite-daemon" in r.json()["detail"]


def test_no_code_answers_a_kind_the_daemon_does_not_document():
    """A kind is a contract between the daemon and the API (app/daemon/__init__.py): a new one is added to KINDS, with
    its answer in privileged.http_error, never made up where it is raised."""
    raised = set()
    for path in [*(ROOT / "app" / "daemon").rglob("*.py"), ROOT / "app" / "core" / "daemon_client.py"]:
        raised |= set(re.findall(r'(?:DaemonError\(|_error\(|"error":)\s*"([a-z_]+)"', path.read_text()))
    assert {"refused", "not_found", "in_use", "unknown_operation", "bad_request"} <= raised  # the scan finds them
    assert raised <= KINDS, sorted(raised - KINDS)


def test_no_operation_has_an_argument_the_client_takes_for_itself():
    """privileged.run keeps `user` and `timeout`, privileged.ask `refused` and `failed`: an operation's argument of
    that name would never reach the daemon."""
    for name, (model, _fn) in OPERATIONS.items():
        assert not {"user", "timeout", "refused", "failed"} & set(model.model_fields), name


@pytest.mark.parametrize(
    "kind, status",
    [
        ("refused", 409),
        ("invalid_arguments", 409),
        ("not_found", 404),
        ("in_use", 409),
        ("unreachable", 502),
        ("unavailable", 503),
        ("failed", 500),
        ("forbidden", 500),
        ("bad_request", 500),
    ],
)
def test_a_route_answers_each_kind_with_the_status_of_the_api(kind, status):
    """A refusal gets the route's own status (409 here); a failure is this node's, 500 "internal": never 502, which
    names another member of the cluster (docs/api.md)."""
    e = privileged.http_error(DaemonError(kind, "why"), refused=409, failed="It failed: {}")
    assert e.status_code == status
    assert e.detail == ("It failed: why" if status == 500 else "why")


def test_every_kind_has_its_answer():
    """A kind added to KINDS without its answer would be a 500 by default: each is named in http_error."""
    import inspect

    source = inspect.getsource(privileged.http_error)
    for kind in KINDS - {"unknown_operation", "bad_request", "forbidden", "failed"}:
        assert kind in privileged.STATUS or f'"{kind}"' in source, kind


def test_a_route_writes_the_daemons_refusal_into_the_audit_log(client, auth_headers, monkeypatch, database):
    from app.core import audit, host_system

    def refuse(*args, **kwargs):
        raise host_system.SettingError("Not a DNS server: 999.1.1.1")

    monkeypatch.setattr(host_system, "set_dns", refuse)
    r = client.put("/host/system/dns", json={"servers": ["999.1.1.1"]}, headers=auth_headers("root"))
    assert (r.status_code, r.json()) == (422, {"detail": "Not a DNS server: 999.1.1.1", "code": "invalid"})
    audit._AUDIT_QUEUE.join()
    with database.get_conn() as conn:
        rows = conn.execute("SELECT resource, result, error_message FROM audit_log WHERE action = 'host_dns'")
        assert [tuple(r) for r in rows] == [("host", "failed", "Not a DNS server: 999.1.1.1")]


def test_a_daemon_failure_is_this_nodes_500_not_a_peers_502(client, auth_headers, monkeypatch):
    from app.core import host_system

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(host_system, "set_dns", fail)
    r = client.put("/host/system/dns", json={"servers": ["9.9.9.9"]}, headers=auth_headers("root"))
    assert r.status_code == 500 and r.json()["code"] == "internal"
    assert "details in the daemon's log" in r.json()["detail"]


def test_a_route_that_lets_the_daemons_error_through_still_says_it_is_missing():
    """The safety net of app/main.py: 503 with the reason, not the generic 500, for a call no route translated."""
    import asyncio
    import json

    from app.main import daemon_error_handler

    r = asyncio.run(daemon_error_handler(None, DaemonError("unavailable", "hyperlite-daemon is not running")))
    assert (r.status_code, json.loads(r.body)) == (
        503,
        {"detail": "hyperlite-daemon is not running", "code": "unavailable"},
    )
