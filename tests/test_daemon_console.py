"""The consoles opened by hyperlite-daemon (app/daemon/ops/console.py, app/core/console_sessions.py): a session's
descriptor crosses the socket and only it, the daemon reads the port, the address, the account and the command
itself, and a session ends when the API lets go of it."""

import os
import select
import signal
import socket
import threading
from pathlib import Path

import pytest

from app.core import cluster, console_sessions, daemon_client, privileged
from app.daemon import SOCKET_ENV, server
from app.daemon.registry import OPERATIONS, Args, DaemonError, Handover, operation


class Domain:
    def __init__(self, xml="<domain/>", active=True):
        self.xml, self.active = xml, active

    def isActive(self):
        return self.active

    def XMLDesc(self, flags=0):
        return self.xml

    def UUIDString(self):
        return "6f1c2d3e-0000-4000-8000-000000000001"

    def interfaceAddresses(self, source):
        return {"vnet0": {"addrs": [{"type": 0, "addr": "192.168.122.40"}]}}


class Conn:
    def __init__(self, domains):
        self.domains = domains

    def lookupByName(self, name):
        if name not in self.domains:
            raise console_sessions_libvirt_error()
        return self.domains[name]


def console_sessions_libvirt_error():
    import libvirt

    return libvirt.libvirtError("no domain")


class PipeArgs(Args):
    text: str
    writer: bool = False


KEPT = []  # the ends of the pipes the test operation keeps


def _pipe(args, ctx):
    """A test operation that hands over the reading end of a pipe holding `text`, or its writing end (`writer`),
    keeping the reading end for the test."""
    r, w = os.pipe()
    if args.writer:
        KEPT.append(r)
        return Handover({"sent": True}, [w])
    os.write(w, args.text.encode())
    os.close(w)
    return Handover({"sent": True}, [r])


if "test.pipe" not in OPERATIONS:
    operation("test.pipe", PipeArgs)(_pipe)


@pytest.fixture()
def daemon(tmp_path, monkeypatch):
    """A daemon on a socket of its own, in a thread."""
    import app.daemon.ops  # noqa: F401

    path = tmp_path / "daemon.sock"
    monkeypatch.setenv(SOCKET_ENV, str(path))
    srv = server.Server(str(path), server.Handler)
    srv.allowed_uids = lambda: {0, os.getuid()}
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def test_a_descriptor_crosses_the_socket_with_the_answer(daemon):
    result, fds = daemon_client.call_fds("test.pipe", {"text": "from root"})
    assert result == {"sent": True} and len(fds) == 1
    with os.fdopen(fds[0], "rb") as f:
        assert f.read() == b"from root"


def test_a_descriptor_nobody_asked_for_is_never_left_open(daemon, monkeypatch):
    received, recv_fds = [], socket.recv_fds

    def recording(sock, bufsize, maxfds):
        answer = recv_fds(sock, bufsize, maxfds)
        received.extend(answer[1])
        return answer

    monkeypatch.setattr(socket, "recv_fds", recording)
    assert daemon_client.call("test.pipe", {"text": "x"}) == {"sent": True}
    assert len(received) == 1
    with pytest.raises(OSError):
        os.fstat(received[0])


def test_the_daemon_keeps_no_copy_of_what_it_handed_over(daemon):
    """Once the API closes the writing end it received, the pipe's reader sees its end: no other copy is left, the
    daemon closed its own. A pseudo-terminal hangs up the same way."""
    _result, fds = daemon_client.call_fds("test.pipe", {"text": "", "writer": True})
    reader = KEPT.pop()
    os.write(fds[0], b"ok")
    daemon_client.close_fds(fds)
    ready, _, _ = select.select([reader], [], [], 5)
    assert ready and os.read(reader, 10) == b"ok"
    ready, _, _ = select.select([reader], [], [], 5)
    assert ready and os.read(reader, 10) == b"", "the end of the pipe: every writing end is closed"
    os.close(reader)


def test_in_process_the_descriptors_are_the_callers():
    result, fds = privileged.run_fds("test.pipe", text="here")
    with os.fdopen(fds[0], "rb") as f:
        assert result == {"sent": True} and f.read() == b"here"


# --- The sessions ---


def _alive_in_group(pgid):
    """The processes of a process group still running (a zombie waiting for its parent is not)."""
    alive = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            stat = Path(f"/proc/{entry}/stat").read_text()
        except OSError:
            continue
        fields = stat.rsplit(")", 1)[1].split()
        if int(fields[2]) == pgid and fields[0] != "Z":
            alive.append(int(entry))
    return alive


@pytest.fixture()
def spawned(monkeypatch):
    """The programs the daemon would run in a terminal: recorded, and replaced by one that waits."""
    seen, real = [], console_sessions.open_pty

    def open_pty(argv, env, cols=80, rows=24):
        seen.append((argv, env))
        return real(["sleep", "30"], env, cols, rows)

    monkeypatch.setattr(console_sessions, "open_pty", open_pty)
    yield seen
    for sid in list(console_sessions._sessions):
        console_sessions.close(sid)


def test_the_host_shell_is_roots_login_shell_in_a_clean_environment(spawned, monkeypatch):
    monkeypatch.setenv("HYPERLITE_SECRET_KEY", "never in a shell")
    result, fds = privileged.run_fds("console.host_shell", user="alice")
    daemon_client.close_fds(fds)
    argv, env = spawned[0]
    assert argv[1:] == ["-l"] and argv[0] == env["SHELL"]
    assert "HYPERLITE_SECRET_KEY" not in env and env["TERM"] == "xterm-256color" and env["USER"] == "root"
    assert privileged.run("console.close", session=result["session"])["exit"] is not None
    with pytest.raises(DaemonError) as e:
        privileged.run("console.host_shell", command="rm -rf /")
    assert e.value.kind == "invalid_arguments"  # a shell, never a command


def test_a_terminal_hangs_up_when_the_api_lets_go_of_it():
    """The daemon closed its copy: once the API closes the terminal (or stops), the session's program gets SIGHUP."""
    fd, sid = console_sessions.open_pty(["sleep", "30"], console_sessions.root_environment())
    proc = console_sessions._sessions[sid]
    os.close(fd)
    proc.wait(5)
    assert console_sessions.close(sid) == -signal.SIGHUP


def test_close_ends_the_whole_session_and_knows_only_its_own():
    fd, sid = console_sessions.open_pty(["sh", "-c", "sleep 30 & sleep 30"], console_sessions.root_environment())
    proc = console_sessions._sessions[sid]
    assert privileged.run("console.close", session=sid)["exit"] is not None
    assert proc.poll() is not None
    assert _alive_in_group(proc.pid) == [], "nothing of its process group is left running"
    os.close(fd)
    assert privileged.run("console.close", session=sid) == {"exit": None}
    with pytest.raises(DaemonError) as e:
        privileged.run("console.close", session="../../1")
    assert e.value.kind == "invalid_arguments"


def test_the_serial_console_and_the_terminal_name_a_running_vm(daemon_libvirt, spawned):
    daemon_libvirt(Conn({"web": Domain(active=False)}))
    for op in ("console.serial", "console.terminal"):
        with pytest.raises(DaemonError) as e:
            privileged.run(op, vm="web")
        assert e.value.kind == "refused", op
        with pytest.raises(DaemonError) as e:
            privileged.run(op, vm="ghost")
        assert e.value.kind == "not_found", op
    assert spawned == []


def test_the_terminal_reads_the_address_and_the_account_itself(database, daemon_libvirt, spawned, monkeypatch):
    from app.core import guest_agent
    from app.core.vm_meta import set_vm_ssh_user

    daemon_libvirt(Conn({"web": Domain()}))
    monkeypatch.setattr(guest_agent, "ipv4", lambda domain: None)
    for extra in ({"address": "10.9.9.9"}, {"ssh_user": "root"}, {"command": "id"}):
        with pytest.raises(DaemonError) as e:
            privileged.run("console.terminal", vm="web", **extra)
        assert e.value.kind == "invalid_arguments"
    with pytest.raises(DaemonError) as e:
        privileged.run("console.terminal", vm="web")  # no account recorded for it
    assert e.value.kind == "refused"
    set_vm_ssh_user("web", "-oProxyCommand=sh")
    with pytest.raises(DaemonError):
        privileged.run("console.terminal", vm="web")
    set_vm_ssh_user("web", "ubuntu")
    result, fds = privileged.run_fds("console.terminal", vm="web")
    daemon_client.close_fds(fds)
    argv, _env = spawned[-1]
    assert result["address"] == "192.168.122.40" and result["user"] == "ubuntu"
    assert argv[-2:] == ["--", "ubuntu@192.168.122.40"] and "ProxyCommand" not in " ".join(argv)


def test_a_vm_of_another_node_is_reached_through_it_with_its_recorded_host_key(
    database, daemon_libvirt, spawned, monkeypatch
):
    from app.core import guest_agent
    from app.core.vm_meta import set_vm_ssh_user

    daemon_libvirt(None, nodes={"n2": Conn({"web": Domain()})})
    monkeypatch.setattr(guest_agent, "ipv4", lambda domain: None)
    monkeypatch.setattr(
        cluster, "get_node", lambda name: {"name": "n2", "hostname": "10.0.0.2", "ssh_port": 22, "ssh_user": "root"}
    )
    set_vm_ssh_user("web", "ubuntu")
    _result, fds = privileged.run_fds("console.terminal", vm="web", node="n2")
    daemon_client.close_fds(fds)
    argv, _env = spawned[-1]
    proxy = next(a for a in argv if a.startswith("ProxyCommand="))
    assert "StrictHostKeyChecking=yes" in proxy and "-W %h:%p -- root@10.0.0.2" in proxy
    assert proxy.index("StrictHostKeyChecking=yes") < proxy.index("StrictHostKeyChecking=accept-new"), (
        "ssh keeps the first value: a node's key is never learned here"
    )


def test_a_display_is_the_port_the_definition_names_never_one_the_api_names(daemon_libvirt):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    daemon_libvirt(Conn({"web": Domain(f"<domain><devices><graphics type='vnc' port='{port}'/></devices></domain>")}))
    with pytest.raises(DaemonError) as e:
        privileged.run_fds("console.display", vm="web", protocol="vnc", port=22)
    assert e.value.kind == "invalid_arguments"
    result, fds = privileged.run_fds("console.display", vm="web", protocol="vnc")
    accepted, _ = listener.accept()
    with socket.socket(fileno=fds[0]) as sock:
        sock.sendall(b"RFB")
        assert accepted.recv(3) == b"RFB" and result == {"session": None}
    accepted.close()
    listener.close()
    with pytest.raises(DaemonError) as e:
        privileged.run_fds("console.display", vm="web", protocol="spice")
    assert e.value.kind == "refused"  # no SPICE display: nothing is connected


def test_another_nodes_display_is_ssh_w_to_its_loopback(daemon_libvirt, monkeypatch):
    seen = []
    daemon_libvirt(
        None,
        nodes={"n2": Conn({"web": Domain("<domain><devices><graphics type='vnc' port='5907'/></devices></domain>")})},
    )
    monkeypatch.setattr(
        cluster,
        "get_node",
        lambda name: (
            {"name": "n2", "hostname": "10.0.0.2", "ssh_port": 2222, "ssh_user": "root"} if name == "n2" else None
        ),
    )
    monkeypatch.setattr(console_sessions, "open_relay", lambda argv: seen.append(argv) or (os.dup(0), "f" * 32))
    _result, fds = privileged.run_fds("console.display", vm="web", node="n2", protocol="vnc")
    daemon_client.close_fds(fds)
    argv = seen[0]
    assert argv[0] == "ssh" and argv[-4:] == ["-W", "127.0.0.1:5907", "--", "root@10.0.0.2"]
    assert argv[argv.index("-p") + 1] == "2222" and argv.index("StrictHostKeyChecking=yes") < argv.index("-i")
    with pytest.raises(DaemonError) as e:
        privileged.run_fds("console.display", vm="web", node="ghost", protocol="vnc")
    assert e.value.kind == "not_found"


def test_a_relays_failure_is_its_exit_status():
    """ssh that cannot connect exits with 255: the API says so (an error for noVNC, a line in the terminal)."""
    fd, sid = console_sessions.open_relay(["sh", "-c", "echo 'Host key verification failed.' >&2; exit 255"])
    with socket.socket(fileno=fd) as sock:
        assert sock.recv(10) == b""
    assert console_sessions.close(sid) == 255


def test_the_container_terminal_and_shell_name_a_running_container(database, daemon_lxc, spawned, monkeypatch):
    from app.core import container_shell

    daemon_lxc(Conn({"ct": Domain(active=False)}))
    for op in ("console.container_shell", "console.container_terminal"):
        with pytest.raises(DaemonError) as e:
            privileged.run(op, container="ct")
        assert e.value.kind == "refused", op
        with pytest.raises(DaemonError) as e:
            privileged.run(op, container="ct", pid=1)
        assert e.value.kind == "invalid_arguments", op  # the daemon finds the container's process itself
    daemon_lxc(Conn({"ct": type("Running", (Domain,), {"ID": lambda self: 4242})()}))
    monkeypatch.setattr(container_shell, "init_pid", lambda controller: 4243 if controller == 4242 else None)
    _result, fds = privileged.run_fds("console.container_shell", container="ct")
    daemon_client.close_fds(fds)
    argv, env = spawned[-1]
    assert argv[:2] == ["nsenter", "--target=4243"] and env["HOME"] == "/root"
