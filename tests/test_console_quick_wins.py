"""Tags managed in one place, alert silences and account end dates."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core import account_expiry, alert_rules, object_meta, self_node

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


# ---- Tags ----


def test_a_tag_is_counted_renamed_merged_and_removed_everywhere(database):
    object_meta.put("vm", "web", "", ["prod", "web"])
    object_meta.put("vm", "db", "keep me", ["prod"])
    object_meta.put("node", "hv-01", "", ["production"])
    counts = {c["tag"]: c for c in object_meta.tag_counts()}
    assert counts["prod"]["total"] == 2 and counts["prod"]["kinds"] == {"vm": 2}
    assert object_meta.retag("production", "prod") == 1  # merged
    assert {c["tag"]: c["total"] for c in object_meta.tag_counts()}["prod"] == 3
    assert object_meta.retag("prod") == 3  # removed everywhere
    assert object_meta.get("vm", "db")["notes"] == "keep me"
    assert object_meta.get("vm", "web")["tags"] == ["web"]
    assert object_meta.tag_counts() == [{"tag": "web", "total": 1, "kinds": {"vm": 1}}]


def test_renaming_a_tag_is_for_administrators(client, auth_headers):
    object_meta.put("vm", "web", "", ["prod"])
    viewer = auth_headers("eve", role="viewer")
    assert client.put("/meta/tags/prod", json={"new": "live"}, headers=viewer).status_code == 403
    admin = auth_headers("root2")
    assert client.put("/meta/tags/prod", json={"new": "Bad Tag!"}, headers=admin).status_code == 422
    r = client.put("/meta/tags/prod", json={"new": "live"}, headers=admin)
    assert r.status_code == 200 and r.json() == {"changed": 1}
    assert object_meta.get("vm", "web")["tags"] == ["live"]


# ---- Silences ----


def _rule():
    return {"name": "hot", "scope": "all_vms", "metric": "cpu_pct", "operator": "gt", "threshold": 80, "window_min": 5}


def _hot(target):
    return [
        {"target": target, "scope": "vm", "ts": (NOW - timedelta(minutes=m)).isoformat(), "cpu_pct": 95} for m in (1, 2)
    ]


def test_a_silence_is_checked():
    with pytest.raises(ValueError):
        alert_rules.add_silence("cluster", "x", "a", "b", "", "admin")
    with pytest.raises(ValueError):
        alert_rules.add_silence("vm", "", NOW.isoformat(), (NOW + timedelta(hours=1)).isoformat(), "", "admin")


def test_a_silenced_alert_is_journaled_but_not_sent(database, monkeypatch):
    sent, logged = [], []
    monkeypatch.setattr(alert_rules, "_send", lambda rule, title, message, result: sent.append(title))
    monkeypatch.setattr("app.core.audit.log_action", lambda *a, **k: logged.append(a[4] if len(a) > 4 else ""))
    alert_rules.create(_rule(), "admin")
    hot = _hot("db")
    monkeypatch.setattr("app.repositories.sqlite.metrics.SqliteMetricsStore.recent_raw", lambda self, since: hot)
    start, end = (NOW - timedelta(minutes=10)).isoformat(), (NOW + timedelta(hours=1)).isoformat()
    s = alert_rules.add_silence("vm", "db", start, end, "upgrade", "admin")
    alert_rules.evaluate(NOW)
    assert sent == [] and any("(silenced)" in line for line in logged)
    # Another VM is not covered by that silence.
    assert alert_rules.silenced({"id": "x"}, f"{self_node.name()}:web", "vm", NOW) is None
    assert alert_rules.silenced({"id": "x"}, f"{self_node.name()}:db", "vm", NOW)["id"] == s["id"]
    # Over (or removed): notifications come back.
    assert alert_rules.silenced({"id": "x"}, f"{self_node.name()}:db", "vm", NOW + timedelta(hours=2)) is None
    assert alert_rules.remove_silence(s["id"]) and not alert_rules.remove_silence(s["id"])


def test_silences_through_the_api(client, auth_headers):
    viewer = auth_headers("eve", role="viewer")
    body = {
        "target_kind": "all",
        "starts_at": NOW.isoformat(),
        "ends_at": (NOW + timedelta(hours=2)).isoformat(),
        "reason": "bay",
    }
    assert client.post("/alert-silences", json=body, headers=viewer).status_code == 403
    admin = auth_headers("root2")
    r = client.post("/alert-silences", json={**body, "ends_at": NOW.isoformat()}, headers=admin)
    assert r.status_code == 422
    r = client.post("/alert-silences", json=body, headers=admin)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    listed = client.get("/alert-silences", headers=admin).json()
    assert [x["id"] for x in listed.get("items", listed)] == [sid]
    assert client.delete(f"/alert-silences/{sid}", headers=admin).status_code in (200, 204)


# ---- Account end dates ----


def test_an_ended_account_is_refused_at_sign_in_and_on_every_request(client, auth_headers):
    admin = auth_headers("root2")
    bob = auth_headers("bob", role="viewer")
    assert client.get("/auth/me", headers=bob).status_code == 200
    soon = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    assert client.put("/auth/account-expiry/bob", json={"expires_at": soon}, headers=admin).status_code == 200
    assert client.get("/auth/me", headers=bob).status_code == 200  # not yet
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    client.put("/auth/account-expiry/bob", json={"expires_at": past}, headers=admin)
    r = client.get("/auth/me", headers=bob)
    assert r.status_code == 401 and "ended" in r.json()["detail"]
    r = client.post("/auth/login", data={"username": "bob", "password": "correct horse battery"})
    assert r.status_code == 403
    # Moving the date away brings it back.
    assert client.put("/auth/account-expiry/bob", json={"expires_at": None}, headers=admin).status_code == 200
    assert client.get("/auth/me", headers=bob).status_code == 200
    assert client.get("/auth/account-expiry", headers=admin).json() == {}


def test_an_end_date_is_set_by_an_administrator_on_someone_else(client, auth_headers):
    admin = auth_headers("root2")
    viewer = auth_headers("eve", role="viewer")
    when = {"expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()}
    assert client.put("/auth/account-expiry/root2", json=when, headers=viewer).status_code == 403
    assert client.put("/auth/account-expiry/root2", json=when, headers=admin).status_code == 422
    assert client.put("/auth/account-expiry/ghost", json=when, headers=admin).status_code == 404
    # A naive date is read as UTC.
    r = client.put("/auth/account-expiry/eve", json={"expires_at": "2030-01-01T10:00:00"}, headers=admin)
    assert r.json()["expires_at"] == "2030-01-01T10:00:00+00:00"
    assert account_expiry.expired("eve", datetime(2031, 1, 1, tzinfo=UTC)) == "2030-01-01T10:00:00+00:00"
    assert account_expiry.expired("eve", datetime(2029, 1, 1, tzinfo=UTC)) is None
