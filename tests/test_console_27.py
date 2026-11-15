"""Connection diagnosis, remote desktop and the assistant (docs/design/console-2.md, sections 8 to 10)."""

import ipaddress

import pytest

from app.core import assistant, net_diagnose, rdp_box

# ---- Connection diagnosis ----


def test_the_first_matching_rule_decides():
    fw = {"default_policy": "drop", "rules": [
        {"action": "accept", "direction": "out", "protocol": "tcp", "port": 443},
        {"action": "drop", "direction": "inout", "protocol": "all", "port": None},
    ]}  # fmt: skip
    assert net_diagnose.first_match(fw, "out", "tcp", 443) == ("accept", 0)
    assert net_diagnose.first_match(fw, "out", "tcp", 22) == ("drop", 1)
    assert net_diagnose.first_match({"default_policy": "accept", "rules": []}, "in", "tcp", 22) == ("accept", None)
    inbound_only = {
        "default_policy": "accept",
        "rules": [{"action": "drop", "direction": "in", "protocol": "tcp", "port": 22}],
    }
    assert net_diagnose.first_match(inbound_only, "out", "tcp", 22) == ("accept", None)


def test_security_groups_match_the_source(monkeypatch):
    from app.core import host_firewall

    monkeypatch.setattr(host_firewall, "expand_group", lambda doc, name: [
        {"action": "accept", "protocol": "tcp", "net": ipaddress.ip_network("10.0.0.0/24"), "ports": (22, 22)},
        {"action": "drop", "protocol": "all", "net": None, "ports": None},
    ])  # fmt: skip
    assert net_diagnose.group_match({}, ["web"], "10.0.0.5", "tcp", 22)[0] == "accept"
    assert net_diagnose.group_match({}, ["web"], "10.0.9.5", "tcp", 22)[0] == "drop"
    assert net_diagnose.group_match({}, ["web"], None, "tcp", 22)[0] == "unknown"


def test_the_verdict_names_what_stops_it():
    ok = [{"layer": "vm_firewall_out", "verdict": "pass"}]
    blocked = [*ok, {"layer": "vm_firewall_in", "verdict": "block"}]
    assert net_diagnose.verdict(ok, "OPEN", "YES") == "reachable"
    assert net_diagnose.verdict(blocked, "TIMEOUT", "YES") == "blocked_by_rule"
    assert net_diagnose.verdict(ok, "REFUSED", None) == "nothing_listening"
    assert net_diagnose.verdict(ok, "TIMEOUT", "YES") == "guest_firewall"
    assert net_diagnose.verdict(ok, "TIMEOUT", None) == "no_answer"
    assert net_diagnose.verdict([{"layer": "state", "verdict": "block"}], None, None) == "not_running"


def test_diagnosis_is_checked(client, auth_headers):
    admin = auth_headers("root2")
    assert (
        client.post("/network/diagnose", json={"from_vm": "a", "to": "b", "port": 0}, headers=admin).status_code == 422
    )
    r = client.post(
        "/network/diagnose", json={"from_vm": "a", "to": "b", "port": 22, "protocol": "sctp"}, headers=admin
    )
    assert r.status_code == 422


# ---- Remote desktop ----


def test_the_box_reaches_the_guest_only():
    props = rdp_box.properties("0123456789abcdef", "10.1.2.3", 1920, 1080)
    assert "DynamicUser=yes" in props and "IPAddressDeny=any" in props and "IPAddressAllow=10.1.2.3" in props
    assert "Environment=RDP_W=1920" in props and "PrivateTmp=yes" in props
    assert not any("/p:" in p for p in props)  # the password never on a command line or a property
    with pytest.raises(ValueError):
        rdp_box.properties("../etc", "10.1.2.3", 800, 600)
    with pytest.raises(ValueError):
        rdp_box.properties("0123456789abcdef", "example.com", 800, 600)
    assert rdp_box.size(100, 99999) == (640, 2160)


def test_the_credential_file():
    text = rdp_box.args_file("10.1.2.3", 3389, 1280, 800, "admin", "p@ss word", "CORP")
    lines = text.splitlines()
    assert lines[0] == "/v:10.1.2.3:3389" and "/p:p@ss word" in lines and "/d:CORP" in lines and "/cert:tofu" in lines
    with pytest.raises(ValueError):
        rdp_box.args_file("10.1.2.3", 3389, 1280, 800, "admin", "bad\n/v:evil", "")


def test_rdp_tickets_follow_console_rights(client, auth_headers):
    from app.routers.vms import rdp

    viewer = auth_headers("eve", role="viewer")  # observers keep the console (vm.console) at the global read level
    body = {"username": "Administrator", "password": "x"}
    r = client.post("/vms/win/rdp-ticket", json=body, headers=viewer)
    assert r.status_code == 200
    assert rdp._TICKETS[r.json()["ticket"]][0] == "win"
    assert client.post("/vms/win/rdp-ticket", json=body).status_code == 401
    assert "available" in client.get("/vms/win/rdp", headers=viewer).json()


# ---- Assistant ----


def test_assistant_settings_keep_the_key_sealed(database):
    s = assistant.set_settings({"provider": "anthropic", "api_key": "sk-test", "enabled": True})
    assert s["has_key"] and "api_key" not in s
    with pytest.raises(assistant.AssistantError):
        assistant.set_settings({"base_url": "file:///etc/passwd"})
    with pytest.raises(assistant.AssistantError):
        assistant.set_settings({"provider": "other"})


@pytest.mark.anyio
async def test_tools_read_and_summarize():
    async def get(path, params):
        if path == "/vms":
            return {"items": [{"name": "web", "state": "running", "node": "local", "secret": "x"},
                              {"name": "db", "state": "stopped", "node": "local"}]}  # fmt: skip
        if path.endswith("/metrics/history"):
            return {"items": [{"cpu_pct": 10, "mem_used_mb": 100}, {"cpu_pct": 30, "mem_used_mb": 300}]}
        raise AssertionError(path)

    rows = await assistant.run_tool(get, "list_vms", {"state": "running"})
    assert rows == [{"name": "web", "node": "local", "state": "running", "vcpu": None, "memory_mb": None, "os": None,
                     "ip": None}]  # fmt: skip
    m = await assistant.run_tool(get, "vm_metrics", {"name": "web"})
    assert m["cpu_pct"] == {"min": 10, "avg": 20.0, "max": 30, "last": 30}


def test_the_assistant_reads_with_the_accounts_rights(client, auth_headers, monkeypatch):
    seen = {}

    async def fake(s, history, question, call_tool, used):
        content, failed = await call_tool("alerts", {})
        seen["alerts"] = (content, failed)
        content, failed = await call_tool("journal", {"hours": 1})
        seen["journal"] = (content, failed)
        used.extend(["alerts", "journal"])
        return "All quiet."

    monkeypatch.setattr(assistant, "_ask_openai", fake)
    admin = auth_headers("root2")
    assert client.post("/assistant/ask", json={"question": "hi"}, headers=admin).status_code == 409  # off
    r = client.put("/assistant/settings", json={"enabled": True, "model": "llama3"}, headers=admin)
    assert r.status_code == 200, r.text
    viewer = auth_headers("eve", role="viewer")
    r = client.post("/assistant/ask", json={"question": "Anything firing?"}, headers=viewer)
    assert r.status_code == 200, r.text
    assert r.json() == {"answer": "All quiet.", "tools": ["alerts", "journal"]}
    assert seen["alerts"][1] is False  # read as eve, allowed
    assert seen["journal"][1] is True and "403" in seen["journal"][0]  # the journal is not eve's to read
    assert client.get("/assistant/settings", headers=viewer).status_code == 403
    assert client.get("/assistant/status", headers=viewer).json()["enabled"] is True
