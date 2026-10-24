"""The API asks hyperlite-daemon for what needs root (app/core/privileged.py, docs/design/privilege-separation.md, step
3): the operations it names exist, their arguments are checked, and without the daemon the routes say so."""

import pathlib
import re

import pytest

import app.daemon.ops  # noqa: F401  (registers the operations)
from app.core import privileged
from app.daemon.registry import OPERATIONS, DaemonError

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _named_operations():
    """Every operation name the API asks for: privileged.run("op", ...) and the routes' _ask(user, action, "op", ...)."""
    names = set()
    for path in (ROOT / "app").rglob("*.py"):
        if "daemon" in path.parts:
            continue
        text = path.read_text()
        names |= set(re.findall(r'privileged\.run\(\s*"([a-z_]+\.[a-z_]+)"', text))
        names |= set(re.findall(r'_ask\(\s*\w+,\s*"[a-z_]+",\s*"([a-z_]+\.[a-z_]+)"', text))
        names |= set(re.findall(r'_daemon\(\s*"([a-z_]+\.[a-z_]+)"', text))
    return names


def test_every_operation_the_api_asks_for_exists_in_the_daemon():
    named = _named_operations()
    assert {"host.set_dns", "host.upgrade", "host.schedule_power"} <= named  # the scan finds them
    assert {"network.firewall_apply", "network.vlan_aware_start", "network.vlan_apply_vm", "network.vlan_boot"} <= named
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
