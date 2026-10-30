"""The CSV exports of a cluster (app/core/cluster_view.py): /tasks/export.csv and /audit/export.csv with cluster=true
read every other member's own export while it arrives, merge the rows newest first with a last column "member", and
name the members that did not answer in the Hyperlite-Unreachable header. A member that stops midway ends the
download with an error: a partial export is never passed off as a complete one."""

import csv
import io
import urllib.parse

import pytest

from app.core import cluster, cluster_setup, cluster_view, peer_auth, peer_https, rolling_update
from app.core.tasks import create_task

TASK_HEADER = ["id", "created", "type", "target", "node", "user", "status", "progress", "started", "finished", "error"]
AUDIT_HEADER = ["timestamp", "user", "action", "resource", "result", "error", "ip"]


def _csv(header, rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue()


def _rows(text):
    return list(csv.reader(io.StringIO(text)))


class Members:
    """The other members' exports: over HTTPS (read while it arrives) or, without a fingerprint, over SSH (whole)."""

    def __init__(self):
        self.https = {}  # (node, path) -> CSV text, or a list of lines ending with an exception
        self.ssh = {}  # (node, path) -> CSV text
        self.down = set()
        self.asked = []  # (node, way, path with its query)
        self.closed = []

    def stream(self, node, token, path, timeout=60, connect_timeout=5):
        self.asked.append((node["name"], "https", path))
        if node["name"] in self.down:
            raise peer_https.Unavailable(f"{node['name']} over HTTPS: refused")
        answer = self.https.get((node["name"], path.split("?")[0]))
        if answer is None:
            raise peer_https.Unavailable(f"{node['name']} published no certificate fingerprint")
        members = self

        class FakeStream:
            status = 200

            def __init__(self):
                self.lines = _lines(answer)

            def close(self):
                members.closed.append(node["name"])

        return FakeStream()

    def api(self, node, username, method, path, body=None, timeout=60, connect_timeout=10):
        self.asked.append((node["name"], "ssh", path))
        if node["name"] in self.down:
            raise rolling_update.PeerUnreachable(f"{node['name']} is not reachable over SSH")
        text = self.ssh.get((node["name"], path.split("?")[0]))
        return (200, text) if text is not None else (404, {"detail": "Not Found"})

    def query(self, node):
        for name, _, path in self.asked:
            if name == node:
                return dict(urllib.parse.parse_qsl(path.partition("?")[2]))
        return None


def _lines(answer):
    if isinstance(answer, str):
        yield from io.StringIO(answer, newline="")
        return
    for line in answer:
        if isinstance(line, Exception):
            raise line
        yield line


@pytest.fixture()
def members(database, monkeypatch):
    fake = Members()
    monkeypatch.setattr(cluster_view, "_mode", lambda: "cluster")
    conf = [{"name": n, "nodeid": i, "address": f"10.0.0.{i}"} for i, n in enumerate(("hv-test", "pve-a", "pve-b"), 1)]
    monkeypatch.setattr(cluster_setup, "shared_conf", lambda: {"cluster": "lab", "version": 3, "members": conf})
    nodes = [
        {"name": n, "hostname": f"{n}.lan", "ssh_user": "root", "ssh_port": 22, "status": "online"}
        for n in ("pve-a", "pve-b")
    ]
    monkeypatch.setattr(cluster, "list_nodes", lambda: nodes)
    monkeypatch.setattr(peer_auth, "ticket_for", lambda node, username: f"ticket-{username}")
    monkeypatch.setattr(peer_https, "stream", fake.stream)
    monkeypatch.setattr(rolling_update, "peer_api", fake.api)
    return fake


def _task(task_id, created, node):
    return [task_id, created, "backup", "web", node, "system", "succeeded", "100", created, created, ""]


def test_the_tasks_of_every_member_newest_first_with_the_member_that_holds_each(client, auth_headers, members):
    headers = auth_headers("ana")
    mine = create_task("start_vm", "web", None, "ana")
    members.https[("pve-a", "/api/v1/tasks/export.csv")] = _csv(
        TASK_HEADER,
        [_task("a2", "2999-01-02T00:00:00+00:00", "pve-a"), _task("a1", "2000-01-02T00:00:00+00:00", "pve-a")],
    )
    # pve-b published no certificate fingerprint: its export comes over SSH.
    members.ssh[("pve-b", "/tasks/export.csv")] = _csv(TASK_HEADER, [_task("b1", "2999-01-01T00:00:00+00:00", "pve-b")])
    r = client.get("/tasks/export.csv?cluster=true&type=start_vm&node=local", headers=headers)
    assert r.status_code == 200 and cluster_view.UNREACHABLE_HEADER not in r.headers
    rows = _rows(r.text)
    assert rows[0] == [*TASK_HEADER, "member"]
    assert [(row[0], row[-1]) for row in rows[1:]] == [
        ("a2", "pve-a"),
        ("b1", "pve-b"),
        (mine, "hv-test"),
        ("a1", "pve-a"),
    ]
    assert members.closed == ["pve-a"]  # its connection closed once read
    sent = members.query("pve-a")
    assert sent == {"type": "start_vm", "node": "hv-test"}  # this node's filter under its name, no cluster=true
    assert {(name, way) for name, way, _ in members.asked} == {("pve-a", "https"), ("pve-b", "https"), ("pve-b", "ssh")}


def test_without_cluster_the_export_is_this_nodes_own(client, auth_headers, members):
    headers = auth_headers("ana")
    create_task("start_vm", "web", None, "ana")
    rows = _rows(client.get("/tasks/export.csv", headers=headers).text)
    assert rows[0] == TASK_HEADER and len(rows) == 2
    assert members.asked == []


def test_outside_a_cluster_the_member_column_names_this_node(client, auth_headers, monkeypatch):
    headers = auth_headers("ana")
    create_task("start_vm", "web", None, "ana")
    rows = _rows(client.get("/tasks/export.csv?cluster=true", headers=headers).text)
    assert rows[0][-1] == "member" and [row[-1] for row in rows[1:]] == ["hv-test"]


def test_a_member_that_does_not_answer_is_named_in_a_header(client, auth_headers, members):
    headers = auth_headers("ana")
    members.down.add("pve-a")
    members.ssh[("pve-b", "/tasks/export.csv")] = _csv(TASK_HEADER, [])
    r = client.get("/tasks/export.csv?cluster=true", headers=headers)
    assert r.status_code == 200
    assert r.headers[cluster_view.UNREACHABLE_HEADER] == "pve-a"


def test_a_member_marked_offline_is_not_asked(client, auth_headers, members, monkeypatch):
    from app.core import node_health

    monkeypatch.setattr(node_health, "down", lambda nodes: {"pve-b"})
    members.https[("pve-a", "/api/v1/tasks/export.csv")] = _csv(TASK_HEADER, [])
    r = client.get("/tasks/export.csv?cluster=true", headers=auth_headers("ana"))
    assert r.headers[cluster_view.UNREACHABLE_HEADER] == "pve-b"
    assert {name for name, _, _ in members.asked} == {"pve-a"}


def test_a_members_columns_are_read_by_name(client, auth_headers, members):
    """A member on another version: its columns in another order, one missing, one more."""
    headers = auth_headers("ana")
    members.https[("pve-a", "/api/v1/tasks/export.csv")] = _csv(
        ["created", "id", "status", "extra"], [["2999-01-01T00:00:00+00:00", "a1", "failed", "x"]]
    )
    members.ssh[("pve-b", "/tasks/export.csv")] = _csv(TASK_HEADER, [])
    rows = _rows(client.get("/tasks/export.csv?cluster=true", headers=headers).text)
    first = dict(zip(rows[0], rows[1], strict=True))
    assert (first["id"], first["status"], first["type"], first["member"]) == ("a1", "failed", "", "pve-a")


def test_a_member_that_stops_midway_ends_the_download_with_an_error(client, auth_headers, members):
    headers = auth_headers("ana")
    lines = _csv(TASK_HEADER, [_task("a1", "2999-01-01T00:00:00+00:00", "pve-a")]).splitlines(keepends=True)
    members.https[("pve-a", "/api/v1/tasks/export.csv")] = [*lines, TimeoutError("timed out")]
    members.ssh[("pve-b", "/tasks/export.csv")] = _csv(TASK_HEADER, [])
    with pytest.raises(cluster_view.PeerError, match="pve-a stopped answering"):
        client.get("/tasks/export.csv?cluster=true", headers=headers)
    assert members.closed == ["pve-a"]


def test_the_audit_log_of_every_member(client, auth_headers, members, database):
    headers = auth_headers("alice")
    with database.get_conn() as conn:
        conn.execute(
            "INSERT INTO audit_log (timestamp, username, action, resource, result) "
            "VALUES ('2026-01-01T00:00:02', 'bob', 'start_vm', 'web', 'success')"
        )
        conn.commit()
    members.https[("pve-a", "/api/v1/audit/export.csv")] = _csv(
        AUDIT_HEADER,
        [
            ["2026-01-01T00:00:03", "bob", "start_vm", '\'=HYPERLINK("http://x")', "success", "", ""],
            ["2026-01-01T00:00:01", "bob", "start_vm", "db", "success", "", ""],
        ],
    )
    members.ssh[("pve-b", "/audit/export.csv")] = _csv(AUDIT_HEADER, [])
    rows = _rows(client.get("/audit/export.csv?cluster=true&action=start_vm", headers=headers).text)
    assert rows[0] == [*AUDIT_HEADER, "member"]
    assert [(row[0], row[3], row[-1]) for row in rows[1:]] == [
        ("2026-01-01T00:00:03", '\'=HYPERLINK("http://x")', "pve-a"),  # neutralised by the member, not twice
        ("2026-01-01T00:00:02", "web", "hv-test"),
        ("2026-01-01T00:00:01", "db", "pve-a"),
    ]
    assert members.query("pve-a") == {"action": "start_vm"}


def test_the_cluster_audit_export_is_for_administrators_only(client, auth_headers, members):
    r = client.get("/audit/export.csv?cluster=true", headers=auth_headers("olga", role="viewer"))
    assert r.status_code == 403
    assert members.asked == []
