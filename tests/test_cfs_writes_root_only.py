"""hyperlite-cfs takes a change from root only (docs/design/privilege-separation.md, section 14.7): the API, www-data from
4e, reads the replicated configuration through the socket's group, and asks hyperlite-daemon for its few writes. A row
it could write there would be applied as root by every member, their permissions included."""

import os
from pathlib import Path

import pytest

import app.daemon.ops  # noqa: F401  (registers the operations)
from app.core import privileged
from app.daemon.registry import Context, DaemonError, dispatch
from app.repositories.cfs import shadow

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def as_www_data(monkeypatch):
    return _as_www_data(monkeypatch)


def _as_www_data(monkeypatch):
    """The API once it is not root: the daemon's operations go to its socket, stood in for here (from uid 33, with the
    request's proof when there is one)."""
    asked = []

    def call(op, args, user=None, timeout=None):
        asked.append((op, args))
        proof = privileged.daemon_client._proof()
        answer = dispatch({"op": op, "args": args}, Context(user, None, 1, 33, proof=proof))
        if not answer.get("ok"):
            raise DaemonError(answer["error"], answer.get("detail"))
        return answer["result"], []

    monkeypatch.delenv(privileged.INPROCESS_ENV)
    monkeypatch.setattr(privileged.daemon_client, "call_fds", call)
    monkeypatch.setattr(os, "geteuid", lambda: 33)
    return asked


class FakeCfs:
    def __init__(self):
        self.counter, self.locks = 100, {}

    def next_id(self):
        self.counter += 1
        return self.counter

    def get(self, path):
        from app.core.cfs_client import NotFound

        raise NotFound(1, "no such entry")

    def lock(self, name, owner, ttl):
        from app.core.cfs_client import LOCKED, Locked

        if self.locks.get(name, owner) != owner:
            raise Locked(LOCKED, f"held by {self.locks[name]}")
        self.locks[name] = owner

    def unlock(self, name, owner):
        self.locks.pop(name, None)


@pytest.fixture()
def cfs(monkeypatch):
    fake = FakeCfs()
    monkeypatch.setattr(shadow, "_get_client", lambda: fake)
    return fake


def test_root_writes_itself_and_the_api_asks_the_daemon(database, cfs, monkeypatch):
    assert shadow.writer() is cfs  # in process (the test suite): as the daemon
    monkeypatch.delenv(privileged.INPROCESS_ENV)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert shadow.writer() is cfs  # root's services
    monkeypatch.setattr(os, "geteuid", lambda: 33)
    assert isinstance(shadow.writer(), shadow._ThroughTheDaemon)


def test_a_new_rows_id_comes_from_the_daemon(database, cfs, as_www_data, monkeypatch):
    from app.repositories.cfs import ids

    monkeypatch.setattr(ids, "_in_cluster", lambda: True)
    monkeypatch.setattr(ids, "_offset", None)
    assert ids.reserve() == 101
    assert as_www_data == [("cfs.next_id", {})]


def test_the_rolling_updates_lock_is_taken_through_the_daemon(cfs, auth_headers, monkeypatch):
    """The rolling update runs with its administrator's delegated proof: the daemon checks it, then takes the lock."""
    from app.core import peer_auth, rolling_update, self_node

    token = auth_headers("root")["Authorization"].split()[1]  # signed in first, as the API still does itself here
    as_www_data = _as_www_data(monkeypatch)
    monkeypatch.setattr(self_node, "name", lambda: "hv1")
    proof = peer_auth.CREDENTIAL.set(token)
    try:
        with rolling_update._LockKeeper():
            assert cfs.locks == {rolling_update.LOCK: "hv1"}
        assert cfs.locks == {}
        cfs.locks[rolling_update.LOCK] = "hv2"
        with pytest.raises(rolling_update.RollingError), rolling_update._LockKeeper():
            pass
    finally:
        peer_auth.CREDENTIAL.reset(proof)
    assert [op for op, _ in as_www_data if op.startswith("cfs.")] == ["cfs.lock", "cfs.unlock", "cfs.lock"]


def test_the_daemon_takes_no_other_lock_for_the_api(database, cfs):
    with pytest.raises(DaemonError) as e:
        privileged.run("cfs.lock", name="hyperlite-ha-lead")
    assert e.value.kind == "invalid_arguments"


def test_what_writes_the_tree_runs_in_the_daemon():
    daemon = (ROOT / "app" / "daemon" / "__main__.py").read_text()
    for start in ("peer_https.start()", "cluster_v1.start()", "start_lead_loop(follow_setup=True)"):
        assert start in daemon, start
    main = (ROOT / "app" / "main.py").read_text()
    in_process = main.index("if os.environ.get(privileged.INPROCESS_ENV)")
    for start in ("peer_https.start()", "cluster_v1.start()", "start_lead_loop()"):
        assert main.index(start) > in_process, start  # the API's own only when it runs the daemon's operations
    unit = (ROOT / "installer" / "hyperlite-cfs.service").read_text()
    assert "--socket-mode 0660 --socket-group www-data" in unit
