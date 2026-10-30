"""The automation jobs' steps run by hyperlite-daemon (app/daemon/ops/job.py): a command as root on this host, or over
SSH with the automation key on a VM whose address and account the daemon reads itself."""

import threading
import time

import pytest

from app.core import jobs, privileged
from app.daemon.ops import job
from app.daemon.registry import DaemonError


class Domain:
    def __init__(self, active=True):
        self.active = active

    def isActive(self):
        return self.active

    def interfaceAddresses(self, source):
        return {"vnet0": {"addrs": [{"type": 0, "addr": "192.168.122.41"}]}}


class Conn:
    def __init__(self, domains):
        self.domains = domains

    def lookupByName(self, name):
        import libvirt

        if name not in self.domains:
            raise libvirt.libvirtError("no domain")
        return self.domains[name]


def test_a_host_step_is_a_shell_command_with_its_output_and_status():
    result = privileged.run("job.step", task_id="t1", target_type="host", command="echo out; echo err >&2; exit 3")
    assert result == {"stdout": "out\n", "stderr": "err\n", "exit": 3}


def test_a_vm_step_reaches_the_address_and_account_the_daemon_reads(database, daemon_libvirt, monkeypatch, tmp_path):
    from app.core import guest_agent, vm_builder
    from app.core.vm_meta import set_vm_ssh_user

    monkeypatch.setattr(vm_builder, "get_automation_private_key_path", lambda: tmp_path / "automation")
    daemon_libvirt(Conn({"web": Domain(), "off": Domain(active=False)}))
    monkeypatch.setattr(guest_agent, "ipv4", lambda domain: None)
    set_vm_ssh_user("web", "ubuntu")
    ran = []

    class Proc:
        returncode = 0

        def __init__(self, argv, **kw):
            ran.append(argv)

        def communicate(self, timeout=None):
            return "hello\n", ""

        def poll(self):
            return 0

    monkeypatch.setattr(job.subprocess, "Popen", Proc)
    assert privileged.run("job.step", task_id="t1", target_type="vm", target="web", command="uptime")["exit"] == 0
    argv = ran[0]
    assert argv[0] == "ssh" and "-tt" not in argv and argv[-3:] == ["--", "ubuntu@192.168.122.41", "uptime"]
    for extra in ({"address": "10.9.9.9"}, {"ssh_user": "root"}):
        with pytest.raises(DaemonError) as e:
            privileged.run("job.step", task_id="t1", target_type="vm", target="web", command="id", **extra)
        assert e.value.kind == "invalid_arguments"
    # The run's log says why a step could not run, as before.
    jobs._current.task_id = "t1"
    with pytest.raises(RuntimeError, match="not running"):
        jobs._run_command("vm", "off", "uptime", dry_run=False)
    with pytest.raises(RuntimeError, match="not found"):
        jobs._run_command("vm", "ghost", "uptime", dry_run=False)
    assert len(ran) == 1


def test_cancelling_a_run_stops_the_step_in_progress():
    answer = {}

    def run():
        answer.update(privileged.run("job.step", task_id="t2", target_type="host", command="sleep 30"))

    worker = threading.Thread(target=run)
    worker.start()
    deadline = time.monotonic() + 5
    while "t2" not in job._running and time.monotonic() < deadline:
        time.sleep(0.02)
    assert jobs._cancel_step("t2") is None
    worker.join(5)
    assert answer["exit"] != 0 and not worker.is_alive()
    assert privileged.run("job.cancel", task_id="t2") == {"cancelled": False}


def test_a_step_prints_at_most_the_limit_and_ends_at_its_timeout(monkeypatch):
    monkeypatch.setattr(job, "OUTPUT_LIMIT", 10)
    out = privileged.run("job.step", task_id="t3", target_type="host", command="printf '%050d' 0")["stdout"]
    assert out == "0" * 10 + job.TRUNCATED
    monkeypatch.setattr(job, "STEP_TIMEOUT_S", 0.2)
    result = privileged.run("job.step", task_id="t3", target_type="host", command="sleep 5")
    assert result["exit"] == 124 and "Timed out" in result["stderr"]
