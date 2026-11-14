"""Costs and budgets, and guest updates (docs/design/governance.md, sections 10 and 11)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core import costs, guest_updates, privileged


def test_vm_cost():
    p = {"vcpu": 10.0, "memory_gb": 5.0, "disk_gb": 0.1}
    vm = {"vcpu": 2, "memory_mb": 4096, "disks": [{"size_gb": 50}, {"size_gb": 50}]}
    assert costs.vm_cost(vm, True, p) == 2 * 10 + 4 * 5 + 100 * 0.1
    assert costs.vm_cost(vm, False, p) == 10.0  # a stopped VM: its disk only


def test_budgets_alert_once_per_level(database, monkeypatch):
    rep = {"total": 0.0, "vms": [], "by_tag": {}}
    monkeypatch.setattr(costs, "report", lambda: rep)
    logged = []
    monkeypatch.setattr("app.core.audit.log_action", lambda *a, **k: logged.append(a[4]))
    b = costs.save_budget({"name": "all", "scope": "cluster", "monthly": 100.0, "alert_percent": 80}, "root")
    assert b["spent"] == 0.0
    rep["total"] = 85.0
    costs.check_budgets()
    costs.check_budgets()
    rep["total"] = 120.0
    costs.check_budgets()
    assert len(logged) == 2 and "at 85 %" in logged[0] and "exceeded" in logged[1]
    with pytest.raises(ValueError):
        costs.save_budget({"name": "x", "scope": "planet", "monthly": 1.0, "alert_percent": 80}, "root")


def test_parse_scan():
    found = guest_updates.parse_scan("pkg openssl security\npkg curl\npkg curl\nfamily apt\n")
    assert found == {"family": "apt", "pending": 2, "security": 1, "packages": ["curl", "openssl"]}
    dnf = guest_updates.parse_scan("pkg kernel.x86_64\npkg bash.x86_64\nsec kernel.x86_64\nfamily dnf")
    assert dnf["security"] == 1 and dnf["pending"] == 2


@pytest.fixture()
def guest(monkeypatch):
    calls = []
    state = {"rc": ""}

    def run(op, user=None, timeout=None, **args):
        calls.append(op)
        if op == "vm.agent_info":
            return {"os": {"id": "debian"}}
        if op == "vm.power":
            return {}
        script = args["script"]
        if "family apt" in script:
            return {"stdout": "pkg openssl security\nfamily apt\n", "exit_code": 0}
        if script.startswith("nohup"):
            return {"stdout": "", "exit_code": 0}
        return {"stdout": f"{state['rc']}\n|\nyes\n|\nlog", "exit_code": 0}

    monkeypatch.setattr(privileged, "run", run)
    return calls, state


def test_scan_and_run(database, guest):
    calls, state = guest
    assert guest_updates.scan("web-1")["pending"] == 1
    now = datetime.now(UTC)
    run = guest_updates.schedule(["web-1"], now.isoformat(), (now + timedelta(hours=2)).isoformat(), True, "root")
    guest_updates.tick(now - timedelta(minutes=5))
    assert guest_updates.runs()[0]["status"] == "scheduled"  # its window has not opened
    guest_updates.tick(now)
    assert guest_updates.runs()[0]["results"]["web-1"]["state"] == "running"
    guest_updates.tick(now + timedelta(minutes=1))
    assert guest_updates.runs()[0]["status"] == "running"  # no exit code yet
    state["rc"] = "0"
    guest_updates.tick(now + timedelta(minutes=2))
    done = guest_updates.runs()[0]
    assert done["status"] == "done" and done["results"]["web-1"]["rebooted"] is True
    assert "vm.power" in calls
    assert guest_updates.cancel(run["id"]) is False


def test_routes(client, auth_headers):
    viewer = auth_headers("bob", "viewer")
    assert client.get("/costs", headers=viewer).status_code == 403
    admin = auth_headers("root", "admin")
    assert (
        client.put("/costs/prices", json={"vcpu": 5, "memory_gb": 2, "disk_gb": 0.05}, headers=admin).json()["vcpu"]
        == 5
    )
    now = datetime.now(UTC)
    body = {"vms": ["x"], "window_start": now.isoformat(), "window_end": (now - timedelta(hours=1)).isoformat()}
    assert client.post("/guest-updates/runs", json=body, headers=admin).status_code == 422
    assert client.post("/guest-updates/scan", json={"vms": ["x"]}, headers=viewer).status_code == 403
