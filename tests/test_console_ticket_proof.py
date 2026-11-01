"""A console's websocket (and a SPICE connection) brings a one-time ticket only, not the session: the ticket keeps the
credential of the request that made it, so hyperlite-daemon gets the account's proof, which it requires from an API
that is not root."""

from app.core import peer_auth, pty_session
from app.daemon.registry import DaemonError
from app.routers import host


def test_a_ticket_hands_over_the_credential_of_the_request_that_made_it(monkeypatch):
    token = peer_auth.CREDENTIAL.set("alice's session")
    try:
        peer_auth.keep_with_ticket("t1", 60)
        peer_auth.keep_with_ticket("spice", 60)
    finally:
        peer_auth.CREDENTIAL.reset(token)
    assert peer_auth.CREDENTIAL.get() is None
    peer_auth.take_ticket_credential("t1")
    assert peer_auth.CREDENTIAL.get() == "alice's session"
    peer_auth.CREDENTIAL.set(None)
    peer_auth.take_ticket_credential("t1")  # spent
    assert peer_auth.CREDENTIAL.get() is None
    for _ in range(2):  # several SPICE connections from one ticket
        peer_auth.CREDENTIAL.set(None)
        peer_auth.take_ticket_credential("spice", keep=True)
        assert peer_auth.CREDENTIAL.get() == "alice's session"
    peer_auth.CREDENTIAL.set(None)


def test_an_expired_ticket_hands_over_nothing(monkeypatch):
    peer_auth.keep_with_ticket("old", 60)
    monkeypatch.setattr(peer_auth.time, "time", lambda: 10**12)
    peer_auth.take_ticket_credential("old")
    assert peer_auth.CREDENTIAL.get() is None


def test_the_host_shell_asks_the_daemon_with_the_administrators_proof(client, auth_headers, monkeypatch):
    seen = []

    async def open_session(op, username, **args):
        seen.append((op, username, peer_auth.CREDENTIAL.get()))
        raise DaemonError("refused", "stop here")

    monkeypatch.setattr(pty_session, "open_session", open_session)
    headers = auth_headers("root")
    ticket = client.post("/host/terminal-ticket", headers=headers).json()["ticket"]
    with client.websocket_connect(f"/api/v1/host/terminal?ticket={ticket}") as ws:
        ws.receive_text()
    proof = headers["Authorization"].removeprefix("Bearer ")
    assert seen == [("console.host_shell", "root", proof)]
    assert ticket not in host.HOST_SHELL_TICKETS


def test_the_api_keeps_the_databases_wal_files_while_it_runs(monkeypatch, tmp_path):
    """Made again by root at every last close, the -wal and -shm are root's alone for an instant, and the API, www-data,
    then cannot open the database: one connection it keeps open keeps them."""
    import sqlite3

    from app.core import database

    path = tmp_path / "hyperlite.db"
    first = sqlite3.connect(path)
    first.execute("PRAGMA journal_mode=WAL")
    first.execute("CREATE TABLE t (x)")
    first.commit()
    first.close()
    monkeypatch.setattr(database, "DB_PATH", path)
    monkeypatch.setattr(database, "_kept", [])
    database.keep_open()
    other = sqlite3.connect(path)  # another service's, opened and closed
    other.execute("INSERT INTO t VALUES (1)")
    other.commit()
    other.close()
    assert (tmp_path / "hyperlite.db-wal").exists() and (tmp_path / "hyperlite.db-shm").exists()
    database.keep_open()
    assert len(database._kept) == 1  # once
    database._kept[0].close()
