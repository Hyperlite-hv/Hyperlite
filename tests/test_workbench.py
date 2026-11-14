"""The Terminal's sessions (app/core/workbench.py, app/routers/workbench.py, app/daemon/ops/workbench.py), with
hyperlite-daemon's units stood in for."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core import api_tokens, permissions, privileged, self_node, signin_rules, workbench


@pytest.fixture()
def daemon(monkeypatch):
    calls = []

    def run(op, user=None, timeout=None, **args):
        calls.append((op, args.get("sid")))
        if op == "workbench.list":
            return {"sessions": [c[1] for c in calls if c[0] == "workbench.open"]}
        return {}

    monkeypatch.setattr(privileged, "run", run)
    monkeypatch.setattr(self_node, "name", lambda: "node-a")
    return calls


def admin():
    return {"username": "root", "role": "admin"}


def test_who_may_use_it(database, daemon):
    bob = {"username": "bob", "role": "viewer"}
    assert workbench.may_use(admin())
    assert not workbench.may_use(bob)
    permissions.create_acl("user", "bob", "maintainer", "node", "node-a")
    assert workbench.may_use(bob)
    assert not workbench.may_use({"username": "eve", "role": "viewer"})
    workbench.set_settings({"everyone": True})
    assert workbench.may_use({"username": "eve", "role": "viewer"})
    with pytest.raises(workbench.WorkbenchError):
        workbench.set_settings({"memory_mb": 1})


def test_open_close_and_its_token(database, daemon, make_user):
    make_user("root", "admin")
    row = workbench.open_session(admin(), "192.0.2.10")
    assert row["state"] == "open" and 700000 <= row["uid"] <= 799999
    assert workbench.uid_for("root") == row["uid"]  # stable
    token = api_tokens._accounts().list_tokens("root")[0]
    assert token["kind"] == "workbench" and token["expires_at"]
    assert workbench.opened_from(row["token_id"]) == "192.0.2.10"
    workbench.set_settings({"per_account": 1})
    with pytest.raises(workbench.WorkbenchError, match="At most 1"):
        workbench.open_session(admin(), "192.0.2.10")
    assert workbench.close(row["id"], "test")
    assert workbench.get(row["id"])["state"] == "ended"
    assert api_tokens._accounts().list_tokens("root") == []  # revoked with the session
    assert [op for op, _ in daemon] == ["workbench.open", "workbench.close"]


def test_a_failed_start_leaves_nothing(database, make_user, monkeypatch):
    from app.daemon.registry import DaemonError

    make_user("root", "admin")

    def refuse(op, **k):
        raise DaemonError("failed", "systemd-run said no")

    monkeypatch.setattr(privileged, "run", refuse)
    with pytest.raises(workbench.WorkbenchError, match="systemd-run"):
        workbench.open_session(admin(), "192.0.2.10")
    assert workbench.sessions() == [] and api_tokens._accounts().list_tokens("root") == []


def test_tickets_are_single_use_and_owned(database, daemon, make_user):
    make_user("root", "admin")
    row = workbench.open_session(admin(), "192.0.2.10")
    value = workbench.ticket(row["id"], admin())
    assert workbench.take_ticket(value) == (row["id"], "root")
    assert workbench.take_ticket(value) is None
    with pytest.raises(workbench.WorkbenchError):
        workbench.ticket(row["id"], {"username": "bob", "role": "admin"})


def test_sweep(database, daemon, make_user):
    make_user("root", "admin")
    idle = workbench.open_session(admin(), "192.0.2.10")
    workbench.set_settings({"per_account": 5})
    attached = workbench.open_session(admin(), "192.0.2.10")
    workbench.mark_attached(attached["id"], True)
    workbench.sweep(datetime.now(UTC) + timedelta(minutes=31))
    assert workbench.get(idle["id"])["ended_why"] == "idle"
    assert workbench.get(attached["id"])["state"] == "open"
    workbench.sweep(datetime.now(UTC) + timedelta(hours=9))
    assert workbench.get(attached["id"])["ended_why"] == "time limit"
    ruled = workbench.open_session(admin(), "192.0.2.10")
    workbench.mark_attached(ruled["id"], True)
    signin_rules.save({"name": "lan", "subject_type": "all", "networks": ["10.0.0.0/8"], "mode": "enforce"}, "root")
    signin_rules._forget()
    workbench.sweep()
    assert workbench.get(ruled["id"])["ended_why"] == "sign-in rule"


def test_adopt(database, daemon, make_user):
    make_user("root", "admin")
    kept = workbench.open_session(admin(), "192.0.2.10")
    workbench.set_settings({"per_account": 5})
    gone = workbench.open_session(admin(), "192.0.2.10")
    daemon[:] = [c for c in daemon if c[1] != gone["id"]]  # its box no longer runs
    workbench.adopt()
    assert workbench.get(kept["id"])["state"] == "open"
    assert workbench.get(gone["id"])["ended_why"] == "node restarted"


def test_a_terminal_token_is_held_to_the_browser_address(client, auth_headers, daemon):
    headers = auth_headers("root", "admin")
    row = client.post("/workbench/sessions", json={}, headers=headers).json()
    token_row = api_tokens._accounts().list_tokens("root")[0]
    user = {"username": "root", "role": "admin", "api_token_kind": "workbench", "api_token_id": token_row["id"]}
    assert workbench.opened_from(token_row["id"]) == row["from_address"]
    signin_rules.save({"name": "lan", "subject_type": "all", "networks": ["10.0.0.0/8"], "mode": "enforce"}, "root")
    signin_rules._forget()
    from app.core import security
    from app.core.audit import request_ip

    request_ip.set("127.0.0.1")  # the relay
    with pytest.raises(Exception, match="Sign-in rule"):
        security._refuse_by_signin_rules(user)


def test_routes(client, auth_headers, daemon):
    admin_h = auth_headers("root", "admin")
    viewer = auth_headers("bob", "viewer")
    assert client.get("/workbench", headers=viewer).json()["allowed"] is False
    assert client.post("/workbench/sessions", json={}, headers=viewer).status_code == 403
    row = client.post("/workbench/sessions", json={}, headers=admin_h).json()
    assert client.post(f"/workbench/sessions/{row['id']}/ticket", headers=admin_h).json()["ticket"]
    assert client.post(f"/workbench/sessions/{row['id']}/ticket", headers=viewer).status_code == 404
    assert [s["id"] for s in client.get("/workbench/sessions", headers=admin_h).json()["items"]] == [row["id"]]
    assert client.delete(f"/workbench/sessions/{row['id']}", headers=viewer).status_code == 404
    assert client.delete(f"/workbench/sessions/{row['id']}", headers=admin_h).status_code == 200
    assert client.put("/workbench/settings", json={"idle_min": 1}, headers=admin_h).status_code == 422
    assert client.put("/workbench/settings", json={"idle_min": 15}, headers=admin_h).json()["idle_min"] == 15


def test_an_api_token_does_not_open_a_terminal(client, auth_headers, daemon):
    made = client.post("/auth/tokens", json={"name": "script"}, headers=auth_headers("root", "admin")).json()
    token = {"Authorization": f"Bearer {made['token']}"}
    assert client.post("/workbench/sessions", json={}, headers=token).status_code in (401, 403)


def test_daemon_rules(database, make_user, daemon):
    from app.daemon.ops import workbench as ops

    make_user("root", "admin")
    make_user("bob", "viewer")
    row = workbench.open_session(admin(), "192.0.2.10")
    args = ops.SessionArgs(sid=row["id"])
    assert ops._owner({"username": "root", "role": "admin"}, args) is None
    assert ops._owner({"username": "bob", "role": "viewer"}, args) is not None
    _, other = api_tokens.create_token("bob", "x")
    wrong = ops.OpenArgs(sid=row["id"], token=other)
    assert "does not belong" in ops._may_open({"username": "root", "role": "admin"}, wrong)


def test_the_daemon_starts_and_stops_a_box(database, make_user, daemon, monkeypatch, tmp_path):
    """open_box and close with systemd, mount and mkfs stood in for: what runs, in which order, and the files."""
    from app.core import workbench_box
    from app.daemon.ops import workbench as ops

    make_user("root", "admin")
    row = workbench.open_session(admin(), "192.0.2.10")  # its daemon call stood in for by the fixture
    for name in ("RUN", "HOMES", "MOUNTS"):
        monkeypatch.setattr(workbench_box, name, tmp_path / name.lower())
    monkeypatch.setattr(ops.os, "chown", lambda *a: None)
    monkeypatch.setattr(ops, "_fingerprint", lambda: "ab" * 32)
    monkeypatch.setattr(ops, "_ensure_user", lambda uid: None)
    ran = []

    class Done:
        returncode, stdout, stderr = 0, "", ""

    def run(argv, timeout=60):
        ran.append(argv[0] if argv[0] != "systemd-run" else argv[3])
        if argv[0] == "mountpoint":
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})()
        if argv[0] == "systemd-run" and argv[3].endswith(f"{row['id']}.service"):
            (workbench_box.RUN / row["id"] / "tmux.sock").touch()
        return Done()

    monkeypatch.setattr(ops, "_run", run)
    token = api_tokens._accounts().list_tokens("root")[0]
    args = ops.OpenArgs.model_construct(sid=row["id"], token="hlt_x")
    assert ops.open_box(args, None) == {"started": row["id"]}
    assert ran[:4] == ["mountpoint", "mkfs.ext4", "e2fsck", "mount"]
    assert ran[4] == f"--unit=hyperlite-workbench-{row['id']}.service"
    assert ran[5] == f"--unit=hyperlite-workbench-{row['id']}-relay.service"
    config = (workbench_box.RUN / row["id"] / "hyperlite.json").read_text()
    assert '"url": "https://127.0.0.1:8443"' in config and '"token": "hlt_x"' in config
    assert token["id"] == row["token_id"]
    ran.clear()
    ops.close(ops.SessionArgs(sid=row["id"]), None)
    assert ran == ["systemctl", "systemctl", "umount"]
    assert not (workbench_box.RUN / row["id"]).exists()
