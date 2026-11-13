"""Inside a VM through its guest agent (app/core/guest_agent.py, app/routers/vms/guest.py, #348): the guest's own
description, and a command run there, only with vm.exec, its output decoded and bounded, a timeout said."""

import base64
import json

import libvirt
import pytest

from app.core import guest_agent, privileged
from app.daemon.registry import DaemonError


class _Dom:
    def __init__(self, name, agent=True, running=True, finish_after=1, out=b"hello\n", code=0):
        self._name, self.agent, self.running = name, agent, running
        self.finish_after, self.out, self.code = finish_after, out, code
        self.asked = []

    def name(self):
        return self._name

    def UUIDString(self):
        return f"uuid-{self._name}"

    def isActive(self):
        return self.running

    def XMLDesc(self, flags=0):
        state = "connected" if self.agent else "disconnected"
        return f"<domain><devices><channel type='unix'><target type='virtio' name='org.qemu.guest_agent.0' state='{state}'/></channel></devices></domain>"

    def agent_reply(self, command):
        c = json.loads(command)
        self.asked.append(c)
        what = c["execute"]
        if what == "guest-get-osinfo":
            return json.dumps(
                {"return": {"id": "debian", "pretty-name": "Debian GNU/Linux 12", "kernel-release": "6.1.0"}}
            )
        if what == "guest-get-host-name":
            return json.dumps({"return": {"host-name": "db-01"}})
        if what == "guest-get-fsinfo":
            return json.dumps(
                {
                    "return": [
                        {"mountpoint": "/", "type": "ext4", "used-bytes": 5, "total-bytes": 10},
                        {"mountpoint": "/run", "type": "tmpfs"},
                    ]
                }
            )
        if what == "guest-exec":
            return json.dumps({"return": {"pid": 42}})
        if what == "guest-exec-status":
            self.finish_after -= 1
            if self.finish_after > 0:
                return json.dumps({"return": {"exited": False}})
            return json.dumps(
                {"return": {"exited": True, "exitcode": self.code, "out-data": base64.b64encode(self.out).decode()}}
            )
        raise libvirt.libvirtError("unknown command")  # an older agent: guest-get-timezone, guest-get-users, guest-info


class _Conn:
    def __init__(self, *domains):
        self.domains = {d.name(): d for d in domains}

    def lookupByName(self, name):
        if name not in self.domains:
            raise libvirt.libvirtError("no domain")
        return self.domains[name]


@pytest.fixture()
def vms(daemon_libvirt, fake_agent, monkeypatch):
    monkeypatch.setattr(guest_agent, "EXEC_POLL_S", 0)
    conn = _Conn(
        _Dom("db"),
        _Dom("bare", agent=False),
        _Dom("slow", finish_after=10**9),
        _Dom("big", out=b"x" * (guest_agent.MAX_OUTPUT + 10), code=3),
    )
    daemon_libvirt(conn)
    return conn


def test_the_guest_describes_itself_and_a_missing_command_is_no_failure(vms):
    info = privileged.run("vm.agent_info", vm="db")
    assert info["os"]["pretty-name"] == "Debian GNU/Linux 12" and info["hostname"] == "db-01"
    assert info["timezone"] is None and info["users"] == []
    assert [f["mountpoint"] for f in info["filesystems"]] == ["/"]  # tmpfs left out
    with pytest.raises(DaemonError) as e:
        privileged.run("vm.agent_info", vm="bare")
    assert e.value.kind == "refused"


def test_a_command_runs_in_the_chosen_shell_and_its_output_comes_back(vms):
    out = privileged.run("vm.agent_exec", vm="db", shell="sh", script="echo hello", timeout_s=5)  # noqa: S604 -- the guest's shell
    assert out["exit_code"] == 0 and out["stdout"] == "hello\n" and not out["timed_out"] and not out["truncated"]
    exec_call = next(c for c in vms.domains["db"].asked if c["execute"] == "guest-exec")
    assert exec_call["arguments"] == {"path": "/bin/sh", "arg": ["-c", "echo hello"], "capture-output": True}
    big = privileged.run("vm.agent_exec", vm="big", shell="powershell", script="Get-Process", timeout_s=5)  # noqa: S604
    assert big["exit_code"] == 3 and big["truncated"] and len(big["stdout"]) == guest_agent.MAX_OUTPUT


def test_a_command_past_its_deadline_is_said_timed_out(vms, monkeypatch):
    clock = iter(range(0, 1000, 10))
    monkeypatch.setattr(guest_agent.time, "monotonic", lambda: next(clock))
    out = privileged.run("vm.agent_exec", vm="slow", shell="bash", script="sleep 999", timeout_s=5)  # noqa: S604
    assert out["timed_out"] and out["exit_code"] is None


def test_the_shell_and_the_limits_are_checked(vms):
    for args in ({"shell": "zsh"}, {"timeout_s": 500}, {"script": ""}):
        with pytest.raises(DaemonError) as e:
            privileged.run("vm.agent_exec", **{"vm": "db", "shell": "sh", "script": "id", "timeout_s": 5, **args})
        assert e.value.kind == "invalid_arguments"


def test_running_a_command_needs_vm_exec_and_is_written_to_the_journal(client, auth_headers, vms, monkeypatch):
    logged = []
    monkeypatch.setattr("app.routers.vms.guest.log_action", lambda *a, **k: logged.append(a))
    admin, viewer = auth_headers("root"), auth_headers("eve", role="viewer")
    assert client.post("/vms/db/guest/run", json={"script": "id"}, headers=viewer).status_code == 403
    r = client.post("/vms/db/guest/run", json={"script": "cat /etc/secret-token"}, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["stdout"] == "hello\n"
    assert logged[-1][1:4] == ("guest_run", "db", "success") and "secret-token" not in logged[-1][4]
    assert client.get("/vms/db/guest", headers=viewer).json()["hostname"] == "db-01"
    assert client.get("/vms/bare/guest", headers=admin).status_code == 422
