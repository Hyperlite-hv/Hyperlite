"""?node= on everything a node holds (app/core/cluster_view.py, on_node): a request about a VM, a template, a network, a
storage pool or an ISO image that names another member goes to that member, minus the parameter, in the name of the
same user, and its answer comes back as it is. This node, `local`, a node registered over SSH only and a console ticket
stay here; a file is never relayed."""

import asyncio

import pytest
from starlette.requests import HTTPConnection

from app.core import cluster_view, rolling_update
from app.main import app
from tests.test_cluster_view import TASK_A, peers  # noqa: F401 (the fixture)

# One route per method and router, each with what it sends.
SENT = [
    ("GET", "/vms/web/disks", None),
    ("PATCH", "/vms/web", {"vcpu": 2}),
    ("POST", "/vms/web/snapshots", {"name": "before"}),
    ("DELETE", "/vms/web?confirm=true", None),
    ("POST", "/templates/debian/deploy?wait=false", {"new_name": "web2"}),
    ("PUT", "/networks/lan/firewall", {"enabled": True}),
    ("POST", "/storage/data/volumes", {"name": "d1", "size_gb": 4}),
    ("DELETE", "/isos/debian.iso?confirm=true", None),
]


@pytest.mark.parametrize(("method", "path", "body"), SENT)
def test_a_request_naming_another_member_is_answered_by_it(client, auth_headers, peers, method, path, body):  # noqa: F811
    route, _, query = path.partition("?")
    peers.answers[("pve-a", route)] = (202, {"task_id": TASK_A})
    url = f"{route}?{query + '&' if query else ''}node=pve-a"
    r = client.request(method, url, json=body, headers=auth_headers("ana"))
    assert r.status_code == 202 and r.json() == {"task_id": TASK_A}
    # The member gets the same request without the parameter, in the same user's name.
    assert peers.calls == [("pve-a", "ana", method, path)]
    assert peers.bodies == [body]


def test_its_refusal_comes_back_and_a_silent_member_is_named(client, auth_headers, peers):  # noqa: F811
    headers = auth_headers("olga", "viewer")
    # The member checks the rights: its refusal is the answer.
    peers.answers[("pve-a", "/vms/web")] = (403, {"detail": "vm.delete is required"})
    r = client.delete("/vms/web?confirm=true&node=pve-a", headers=headers)
    assert r.status_code == 403 and r.json()["detail"] == "vm.delete is required"
    # Never a 401 (it would sign the dashboard out of this node), and a member down is named.
    peers.answers[("pve-a", "/vms/web")] = (401, {"detail": "Could not validate credentials"})
    assert client.get("/vms/web?node=pve-a", headers=headers).status_code == 502
    peers.down.add("pve-a")
    r = client.get("/vms/web?node=pve-a", headers=headers)
    assert r.status_code == 502 and "pve-a" in r.json()["detail"]


def test_without_a_session_nothing_is_sent(client, peers):  # noqa: F811
    assert client.delete("/vms/web?confirm=true&node=pve-a").status_code == 401
    assert peers.calls == []


@pytest.mark.parametrize("node", ["local", "hv-test", ""])
def test_this_node_is_answered_here(client, auth_headers, peers, node):  # noqa: F811
    r = client.get(f"/isos?node={node}", headers=auth_headers("ana"))
    assert r.status_code == 200
    assert peers.calls == []


def test_a_node_over_ssh_only_is_reached_only_by_routes_taking_node(client, auth_headers, peers):  # noqa: F811
    headers = auth_headers("ana")
    # DELETE /isos/{f} takes `node` itself: it handles pve-old here, as before (here: no such image there).
    r = client.delete("/isos/debian.iso?confirm=true&node=pve-old", headers=headers)
    assert r.status_code == 404 and "not a member" not in r.json()["detail"]
    # GET /isos does not: it would list this node's images under pve-old's name. Refused, as is a node that is none.
    for node in ("pve-old", "pve-z"):
        r = client.get(f"/isos?node={node}", headers=headers)
        assert r.status_code == 409 and f"{node} is not a member" in r.json()["detail"]
    assert peers.calls == []


def test_a_console_ticket_stays_here_where_its_websocket_opens(client, auth_headers, peers):  # noqa: F811
    headers = auth_headers("root")
    for path in (
        "console-ticket",
        "terminal-ticket",
        "serial-ticket",
        "spice-display",
        "spice-ticket",
        "tunnel-ticket",
    ):
        client.post(f"/vms/web/{path}?node=pve-a", headers=headers)
    assert peers.calls == []


def test_a_websocket_is_never_sent():
    ws = HTTPConnection({"type": "websocket", "path": "/vms/web/console", "query_string": b"node=pve-a", "headers": []})
    assert asyncio.run(cluster_view.on_node(ws, "pve-a")) is None


def test_a_file_is_not_relayed(client, auth_headers, peers):  # noqa: F811
    r = client.post(
        "/isos?node=pve-a",
        files={"file": ("debian.iso", b"ISO", "application/octet-stream")},
        headers=auth_headers("ana"),
    )
    assert r.status_code == 422 and "send it to pve-a itself" in r.json()["detail"]
    assert peers.calls == []


def test_a_write_may_take_as_long_as_its_work_a_read_does_not(client, auth_headers, peers, monkeypatch):  # noqa: F811
    timeouts = []

    def api(node, username, method, path, body=None, timeout=60, connect_timeout=10):
        timeouts.append((method, timeout))
        return 200, {}

    monkeypatch.setattr(rolling_update, "peer_api", api)
    headers = auth_headers("ana")
    client.get("/vms/web/disks?node=pve-a", headers=headers)
    client.post("/vms/web/stop?node=pve-a&force_after=180", headers=headers)
    assert timeouts == [("GET", 60), ("POST", cluster_view.SEND_WRITE_TIMEOUT_S)]
    assert cluster_view.SEND_WRITE_TIMEOUT_S >= 900


def test_a_task_started_with_node_is_followed_with_node(client, auth_headers, peers):  # noqa: F811
    peers.answers[("pve-a", f"/tasks/{TASK_A}")] = (200, {"id": TASK_A, "status": "running"})
    peers.answers[("pve-a", f"/tasks/{TASK_A}/log")] = (200, {"items": [], "total": 0})
    headers = auth_headers("ana")
    assert client.get(f"/tasks/{TASK_A}?node=pve-a", headers=headers).json()["status"] == "running"
    assert client.get(f"/tasks/{TASK_A}/log?node=pve-a", headers=headers).status_code == 200
    assert [c[3] for c in peers.calls] == [f"/tasks/{TASK_A}", f"/tasks/{TASK_A}/log"]


def test_every_route_of_these_objects_documents_node():
    spec = app.openapi()
    prefixes = ("/api/v1/vms", "/api/v1/templates", "/api/v1/networks", "/api/v1/storage", "/api/v1/isos")
    missing = []
    for path, operations in spec["paths"].items():
        if not path.startswith(prefixes):
            continue
        for method, op in operations.items():
            names = [p["name"] for p in op.get("parameters", []) if p.get("in") == "query"]
            if names.count("node") != 1:
                missing.append(f"{method.upper()} {path}: {names.count('node')}")
    assert not missing, missing


def test_a_session_whose_password_must_change_sends_nothing(client, auth_headers, peers):  # noqa: F811
    """As everywhere else: such a session only changes its password, here or through another member."""
    from app.core.security import flag_weak_password

    headers = auth_headers("newbie")
    flag_weak_password("newbie")
    assert client.delete("/vms/web?confirm=true&node=pve-a", headers=headers).status_code == 403
    assert peers.calls == []
