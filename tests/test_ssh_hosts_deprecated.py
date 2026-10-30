"""Hosts driven over SSH from one node are deprecated (docs/api.md, "Deprecated"): their routes work as before, are
marked deprecated in the API's description and answer with a Deprecation header (RFC 9745) and a Link to what
replaces them. The routes of the cluster's members are not."""

from app.core import config_copy
from app.routers import nodes

DEPRECATED = [
    ("post", "/nodes"),
    ("post", "/nodes/test"),
    ("get", "/nodes/cluster-pubkey"),
    ("get", "/nodes/config-copy"),
    ("post", "/nodes/config-copy"),
]


def test_the_ssh_host_routes_are_marked_deprecated_and_no_other(database):
    from app.main import app

    paths = app.openapi()["paths"]
    marked = {
        (m, p.removeprefix("/api/v1")) for p, ops in paths.items() for m, op in ops.items() if op.get("deprecated")
    }
    assert marked == set(DEPRECATED)


def test_they_still_answer_and_say_they_are_deprecated(client, auth_headers, monkeypatch):
    monkeypatch.setattr(config_copy, "status", lambda: [])
    headers = auth_headers("ana")
    r = client.get("/nodes/config-copy", headers=headers)
    assert r.status_code == 200 and r.json() == []
    assert r.headers["Deprecation"] == nodes.DEPRECATED_SINCE == "@1792800000"  # 2026-10-24T00:00:00Z
    assert r.headers["Link"] == f'<{nodes.SSH_HOSTS_DOC}>; rel="deprecation"'
    assert "Deprecation" not in client.get("/cluster", headers=headers).headers  # a member's route
