"""The copy between SQLite and hyperlite-cfs done by hyperlite-daemon (app/daemon/ops/cfs.py; docs/design/
privilege-separation.md, section 14.4): the API asks it to catch up and to confirm a request's changes, and never copies
anything itself."""

from pathlib import Path

import pytest

import app.daemon.ops  # noqa: F401  (registers the operations)
from app.core import privileged
from app.daemon.registry import Context, DaemonError, dispatch
from app.repositories.cfs import shadow

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def out_of_process(monkeypatch):
    return _out_of_process(monkeypatch)


def _out_of_process(monkeypatch):
    """The API as on a node: the daemon's operations go to its socket, stood in for here."""
    asked = []

    def call(op, args, user=None, timeout=None):
        asked.append((op, args, timeout))
        # From www-data, with the request's proof when there is one (none outside a request).
        answer = dispatch({"op": op, "args": args}, Context(None, None, 1, 33, proof=privileged.daemon_client._proof()))
        if not answer.get("ok"):
            raise DaemonError(answer["error"], answer.get("detail"))
        return answer["result"], []

    monkeypatch.delenv(privileged.INPROCESS_ENV)
    monkeypatch.setattr(privileged.daemon_client, "call_fds", call)
    return asked


def test_the_api_asks_the_daemon_to_confirm_and_never_copies_itself(database, out_of_process, monkeypatch):
    copied = []
    monkeypatch.setattr(shadow, "confirm_here", lambda wanted, timeout=None: copied.append(sorted(wanted)))
    shadow.confirm({3, 1, 2})
    assert out_of_process == [("cfs.confirm", {"ids": [1, 2, 3]}, shadow.CONFIRM_S + 10)]
    assert copied == [[1, 2, 3]]  # in the daemon's operation, not in the API


def test_what_the_cluster_kept_or_refused_reaches_the_request_as_before(database, out_of_process, monkeypatch):
    def changed(wanted, timeout=None):
        raise shadow.Changed(["/db/groups/7"])

    monkeypatch.setattr(shadow, "confirm_here", changed)
    with pytest.raises(shadow.Changed) as e:
        shadow.confirm({7})
    assert e.value.paths == ["/db/groups/7"]

    def refused(wanted, timeout=None):
        raise shadow.NotConfirmed("The cluster refused the change: no quorum")

    monkeypatch.setattr(shadow, "confirm_here", refused)
    with pytest.raises(shadow.NotConfirmed, match="no quorum"):
        shadow.confirm({7})


def test_without_the_daemon_nothing_is_confirmed(database, monkeypatch, tmp_path):
    monkeypatch.delenv(privileged.INPROCESS_ENV)
    monkeypatch.setenv("HYPERLITE_DAEMON_SOCKET", str(tmp_path / "none.sock"))
    with pytest.raises(shadow.NotConfirmed):
        shadow.confirm({1})


def test_the_catch_up_before_a_request_is_the_daemons(database, out_of_process, monkeypatch):
    done = []
    monkeypatch.setattr(shadow, "drain", lambda limit=500: done.append("out"))
    from app.repositories.cfs import inbound

    monkeypatch.setattr(inbound, "apply", lambda: done.append("in"))
    shadow.catch_up()
    assert [op for op, _, _ in out_of_process] == ["cfs.catch_up"] and done == ["out", "in"]


def test_the_daemon_takes_ids_only(database, out_of_process):
    with pytest.raises(DaemonError) as e:
        privileged.run("cfs.confirm", ids=[0])
    assert e.value.kind == "invalid_arguments"
    with pytest.raises(DaemonError):
        privileged.run("cfs.confirm", ids=[1], table="acl")


def test_the_copy_loops_run_in_the_daemon_and_in_the_api_only_in_process():
    daemon = (ROOT / "app" / "daemon" / "__main__.py").read_text()
    assert "shadow.start_shadow_copy()" in daemon and "inbound.start_apply_loop()" in daemon
    main = (ROOT / "app" / "main.py").read_text()
    start = main.index("start_shadow_copy()\n", main.index("def on_startup") if "def on_startup" in main else 0)
    guard = main.rindex('if os.environ.get(privileged.INPROCESS_ENV) == "1":', 0, start)
    assert all(line.startswith("        ") for line in main[guard:start].splitlines()[1:] if line.strip())
    # Forced shadow mode and the socket's path come from the node's env file: the daemon loads it as the API does, or
    # its copy loop would take shadow mode for off and empty the outbox.
    assert "EnvironmentFile=-/etc/hyperlite/priv/env" in (ROOT / "installer" / "hyperlite-daemon.service").read_text()


def test_the_administration_page_is_the_daemons_to_act_on(client, auth_headers, monkeypatch):
    """Starting hyperlite-cfs and writing its tree are root's: the routes ask the daemon, which checks the role."""
    admin = auth_headers("root")  # signed in first, as the API does today
    out_of_process = _out_of_process(monkeypatch)
    monkeypatch.setattr(shadow, "turn_on", lambda: {"written": 3, "deleted": 0})
    r = client.post("/cfs/shadow/enable", headers=admin)
    assert r.status_code == 200 and r.json()["written"] == 3
    assert "cfs.enable" in [op for op, _, _ in out_of_process]

    def forced():
        raise shadow.ShadowError("Shadow mode is forced")

    monkeypatch.setattr(shadow, "turn_off", forced)
    monkeypatch.setattr(shadow, "forced", lambda: True)
    assert client.post("/cfs/shadow/disable", headers=admin).status_code == 409
