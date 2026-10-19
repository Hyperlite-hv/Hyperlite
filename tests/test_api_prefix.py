"""The API under /api/v1 (app/main.py, docs/design/api-v1.md): the old addresses answer 410 with what to do, what a
browser opens stays at the root, and a member not on 1.0 yet is still reached during the update to it."""

from app.core import rolling_update, sso


def test_an_old_address_is_gone_with_what_to_do(client, auth_headers):
    headers = auth_headers("ana")
    r = client.get("http://testserver/groups", headers=headers)
    assert r.status_code == 410 and r.json() == {
        "detail": "This address is gone since Hyperlite 1.0: use /api/v1/groups",
        "code": "api_gone",
    }
    r = client.get("http://testserver/vms/web", headers={**headers, "User-Agent": "hyperlite-cli/0.9.0"})
    assert r.status_code == 410 and "update it" in r.json()["detail"]
    assert client.get("/groups", headers=headers).status_code == 200  # the same route under the prefix


def test_the_dashboards_own_addresses_are_not_mistaken_for_old_routes(client):
    # A deep link of the dashboard (/vm/web, /node/local) is a page, never an old route of the API.
    for path in ("/vm/web", "/node/local", "/datacenter"):
        r = client.get("http://testserver" + path, headers={"Accept": "text/html"})
        assert r.status_code in (200, 503), path  # the page, or "not built" in a checkout without the dashboard


def test_what_a_browser_opens_stays_at_the_root(client):
    assert client.get("http://testserver/auth/sso/login").status_code == 400  # SSO not enabled: answered, not gone


def test_the_sso_hand_off_reaches_the_exchange_under_the_prefix(client, make_user):
    make_user("gia", "viewer")
    client.cookies.set("hl_sso_handoff", sso.create_handoff("gia"), path="/auth/sso")  # where it used to be set
    assert client.post("/auth/sso/exchange").status_code == 401  # a cookie for /auth/sso never reaches /api/v1/...
    client.cookies.clear()
    client.cookies.set("hl_sso_handoff", sso.create_handoff("gia"), path="/api/v1/auth/sso")
    assert client.post("/auth/sso/exchange").status_code == 200


def test_a_member_before_1_0_is_called_at_the_old_address(monkeypatch):
    calls = []

    def fake(node, username, method, path, body, timeout, connect_timeout):
        calls.append(path)
        return (404, {"detail": "Not Found"}) if path.startswith("/api/v1/") else (200, {"version": "2026.10.18.2047"})

    monkeypatch.setattr(rolling_update, "_peer_api", fake)
    assert rolling_update.peer_api({"name": "pve-b"}, "ana", "GET", "/update/check") == (
        200,
        {"version": "2026.10.18.2047"},
    )
    assert calls == ["/api/v1/update/check", "/update/check"]


def test_a_member_on_1_0_is_called_once(monkeypatch):
    calls = []
    monkeypatch.setattr(
        rolling_update, "_peer_api", lambda node, u, m, path, *a: calls.append(path) or (404, {"detail": "No VM web"})
    )
    assert rolling_update.peer_api({"name": "pve-b"}, "ana", "GET", "/vms/web") == (404, {"detail": "No VM web"})
    assert calls == ["/api/v1/vms/web"]  # a real 404 of the API is not a member before 1.0


def test_every_list_answers_items_and_total(client, auth_headers):
    headers = auth_headers("ana")
    client.post("/groups", json={"name": "ops"}, headers=headers)
    groups = client.get("/groups", headers=headers).json()
    assert (
        set(groups) == {"items", "total"} and [g["name"] for g in groups["items"]] == ["ops"] and groups["total"] == 1
    )
    tasks = client.get("/tasks", headers=headers).json()
    assert set(tasks) == {"items", "total"} and tasks["total"] == len(tasks["items"])
