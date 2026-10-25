"""hyperlite-daemon (app/daemon): who may connect, what a request may hold, and the client's view of it."""

import json
import os
import socket
import threading

import pytest

from app.core import daemon_client
from app.daemon import SOCKET_ENV, server
from app.daemon.registry import OPERATIONS, Args, DaemonError, operation


class EchoArgs(Args):
    text: str
    times: int = 1


@pytest.fixture()
def daemon(tmp_path, monkeypatch):
    """A daemon on a socket of its own, in a thread, with a test operation next to the real ones."""
    import app.daemon.ops  # noqa: F401

    if "test.echo" not in OPERATIONS:

        @operation("test.echo", EchoArgs)
        def echo(args, ctx):
            if args.text == "refuse":
                raise DaemonError("forbidden", "not this one")
            if args.text == "crash":
                raise RuntimeError("an internal detail that must not leave the daemon")
            return {"text": args.text * args.times, "user": ctx.user}

    path = tmp_path / "daemon.sock"
    monkeypatch.setenv(SOCKET_ENV, str(path))
    srv = server.Server(str(path), server.Handler)
    # The account running the tests stands for the API (root in production until step 4, then www-data).
    srv.allowed_uids = lambda: {0, os.getuid()}
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def raw(path, payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(str(path))
        s.sendall(payload)
        return json.loads(s.makefile("rb").readline())


def test_ping_answers_with_the_version_and_the_user_it_runs_as(daemon):
    result = daemon_client.call("system.ping", user="alice")
    assert set(result) == {"version", "uid"}


def test_an_operation_gets_checked_arguments_and_the_user_named(daemon):
    assert daemon_client.call("test.echo", {"text": "ab", "times": 2}, user="alice") == {
        "text": "abab",
        "user": "alice",
    }


@pytest.mark.parametrize(
    ("op", "args", "kind"),
    [
        ("nothing.here", {}, "unknown_operation"),
        ("test.echo", {}, "invalid_arguments"),  # text missing
        ("test.echo", {"text": "a", "path": "/etc/shadow"}, "invalid_arguments"),  # an unknown field is refused
        ("test.echo", {"text": "a", "times": "many"}, "invalid_arguments"),
        ("test.echo", {"text": "refuse"}, "forbidden"),
        ("test.echo", {"text": "crash"}, "failed"),
    ],
)
def test_what_is_refused_and_how(daemon, op, args, kind):
    with pytest.raises(DaemonError) as e:
        daemon_client.call(op, args)
    assert e.value.kind == kind
    assert "internal detail" not in e.value.detail  # a crash's own text stays in the daemon's log


def test_a_request_must_be_one_json_object_per_line(daemon, tmp_path):
    path = tmp_path / "daemon.sock"
    assert raw(path, b"not json\n")["error"] == "bad_request"
    assert raw(path, b"[1, 2]\n")["error"] == "bad_request"
    assert raw(path, b'{"op": "system.ping", "args": [1]}\n')["error"] == "invalid_arguments"


def test_a_request_too_large_is_refused(daemon, tmp_path):
    from app.daemon import MAX_REQUEST_BYTES

    big = json.dumps({"op": "test.echo", "args": {"text": "x" * (MAX_REQUEST_BYTES + 10)}}).encode() + b"\n"
    assert raw(tmp_path / "daemon.sock", big)["error"] == "bad_request"


def test_a_process_of_another_user_is_refused(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "allowed_uids", lambda: set())  # as if the caller were neither root nor the API
    with pytest.raises(DaemonError) as e:
        daemon_client.call("system.ping")
    assert e.value.kind == "forbidden"


def test_the_client_says_when_the_daemon_is_not_running(tmp_path, monkeypatch):
    monkeypatch.setenv(SOCKET_ENV, str(tmp_path / "nobody-listens.sock"))
    with pytest.raises(DaemonError) as e:
        daemon_client.call("system.ping")
    assert e.value.kind == "unavailable"


def test_an_operation_name_is_registered_once():
    with pytest.raises(ValueError):
        operation("system.ping")(lambda args, ctx: None)


def test_the_daemon_tells_systemd_once_its_socket_answers(tmp_path, monkeypatch):
    """Type=notify: hyperlite.service, ordered after it, writes the accounts through it from its first start."""
    import socket

    from app.daemon import server

    address = str(tmp_path / "notify")
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as systemd:
        systemd.bind(address)
        monkeypatch.setenv("NOTIFY_SOCKET", address)
        server.notify_ready()
        assert systemd.recv(64) == b"READY=1"
    monkeypatch.delenv("NOTIFY_SOCKET")
    server.notify_ready()  # run by hand: nothing to tell
