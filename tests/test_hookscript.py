"""Hook scripts on a VM's lifecycle (app/core/hookscript.py): the library's rules, the script in the VM's metadata,
libvirt's hook at pre-start, post-start and post-stop, Hyperlite's pre-stop, and the API. Checked on libvirt 10 in the
lab (docs/design/hook-scripts.md)."""

import io
import json
import os
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import libvirt
import pytest

from app.core import hookscript

SCRIPT = """#!/bin/sh
echo "$1 $2 $HYPERLITE_VM $HYPERLITE_PHASE $(wc -c)" >> "$(dirname "$0")/../calls"
echo "said $2"
[ -e "$(dirname "$0")/../refuse-$2" ] && { echo "refused at $2"; exit 3; }
exit 0
"""


@pytest.fixture()
def lib(monkeypatch, tmp_path):
    """A library of this test's own (its owner is the user running the tests, as root is on a node)."""
    snippets, runs = tmp_path / "snippets", tmp_path / "runs"
    snippets.mkdir(mode=0o755)
    snippets.chmod(0o755)
    monkeypatch.setenv("HYPERLITE_SNIPPETS_DIR", str(snippets))
    monkeypatch.setenv("HYPERLITE_HOOKSCRIPT_RUNS_DIR", str(runs))
    monkeypatch.setattr(hookscript, "OWNER_UID", os.getuid())

    class Lib:
        dir = snippets

        def add(self, name, text=SCRIPT, mode=0o755):
            path = snippets / name
            path.write_text(text)
            path.chmod(mode)
            return path

        def calls(self):
            path = tmp_path / "calls"
            return path.read_text().splitlines() if path.exists() else []

        def refuse(self, phase):
            (tmp_path / f"refuse-{phase}").touch()

    return Lib()


class FakeDomain:
    """A domain whose metadata is set as libvirt does: under the namespace, in the definition and the live XML."""

    def __init__(self, name="web", active=False):
        self.name_ = name
        self.active = active
        self.metadata = None
        self.set_calls = []

    def name(self):
        return self.name_

    def setMetadata(self, kind, xml, key, uri, flags):
        self.set_calls.append((xml, key, uri, flags))
        self.metadata = None if xml is None else (uri, xml)

    def isActive(self):
        return self.active

    def XMLDesc(self, flags=0):
        meta = ""
        if self.metadata:
            uri, xml = self.metadata
            el = ET.fromstring(xml)
            meta = f"<metadata><hookscript xmlns='{uri}' script='{el.get('script')}'/></metadata>"
        return f"<domain><name>{self.name_}</name>{meta}<devices/></domain>"


def hook(dom, op, sub):
    return hookscript.hook([dom.name(), op, sub, "-"], io.StringIO(dom.XMLDesc()))


# --- The library ---


def test_a_usable_script_is_a_plain_executable_file_only_its_owner_may_change(lib):
    lib.add("ok.sh")
    assert hookscript.check("ok.sh") == lib.dir / "ok.sh"
    lib.add("shared.sh", mode=0o757)
    lib.add("group.sh", mode=0o775)
    lib.add("plain.sh", mode=0o644)
    (lib.dir / "link.sh").symlink_to(lib.dir / "ok.sh")
    (lib.dir / "folder").mkdir()
    for name, reason in [
        ("shared.sh", "writable by others"),
        ("group.sh", "writable by others"),
        ("plain.sh", "not executable"),
        ("link.sh", "not a plain file"),
        ("folder", "not a plain file"),
        ("missing.sh", "No script"),
        ("../ok.sh", "Invalid script name"),
        (".hidden", "Invalid script name"),
    ]:
        with pytest.raises(hookscript.HookError, match=reason):
            hookscript.check(name)
    listed = {s["name"]: s for s in hookscript.list_scripts()}
    assert listed["ok.sh"]["usable"] and listed["ok.sh"]["size"] == len(SCRIPT)
    assert not listed["shared.sh"]["usable"] and "writable" in listed["shared.sh"]["reason"]


def test_a_script_of_another_owner_or_in_an_open_library_is_refused(lib, monkeypatch):
    lib.add("ok.sh")
    monkeypatch.setattr(hookscript, "OWNER_UID", os.getuid() + 1)
    with pytest.raises(hookscript.HookError, match="not owned by root"):
        hookscript.check("ok.sh")
    monkeypatch.setattr(hookscript, "OWNER_UID", os.getuid())
    lib.dir.chmod(0o777)
    with pytest.raises(hookscript.HookError, match=r"library .* is writable by others"):
        hookscript.check("ok.sh")


def test_no_library_lists_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("HYPERLITE_SNIPPETS_DIR", str(tmp_path / "none"))
    assert hookscript.list_scripts() == []
    with pytest.raises(hookscript.HookError, match="No script library"):
        hookscript.check("ok.sh")


# --- The VM's script ---


def test_the_script_lives_in_the_vms_metadata_live_and_in_its_definition():
    dom = FakeDomain(active=True)
    hookscript.set_script(dom, "ok.sh")
    assert hookscript.get_script(dom) == "ok.sh"
    assert dom.set_calls[-1][3] == libvirt.VIR_DOMAIN_AFFECT_CONFIG | libvirt.VIR_DOMAIN_AFFECT_LIVE
    hookscript.set_script(dom, None)
    assert dom.metadata is None and hookscript.get_script(dom) is None
    with pytest.raises(hookscript.HookError):
        hookscript.set_script(dom, "a'b")


# --- libvirt's hook ---


def test_the_hook_runs_pre_start_post_start_and_post_stop_with_the_vm_and_phase(lib):
    lib.add("ok.sh")
    dom = FakeDomain()
    hookscript.set_script(dom, "ok.sh")
    for op, sub in [("prepare", "begin"), ("start", "begin"), ("started", "begin"), ("stopped", "end")]:
        assert hook(dom, op, sub) == 0
    assert hook(dom, "release", "end") == 0
    calls = [c.split()[:4] for c in lib.calls()]
    assert calls == [["web", p, "web", p] for p in ("pre-start", "post-start", "post-stop")]
    assert all(int(c.split()[4]) > 0 for c in lib.calls()), "the domain XML on stdin"
    runs = hookscript.runs("web")
    assert [(r["phase"], r["exit_code"], r["output"]) for r in runs] == [
        ("pre-start", 0, "said pre-start\n"),
        ("post-start", 0, "said post-start\n"),
        ("post-stop", 0, "said post-stop\n"),
    ]


def test_a_failing_pre_start_refuses_the_start_and_gets_no_post_stop(lib, capsys):
    lib.add("ok.sh")
    lib.refuse("pre-start")
    dom = FakeDomain()
    hookscript.set_script(dom, "ok.sh")
    assert hook(dom, "prepare", "begin") == 1
    assert "pre-start hook script failed (exit code 3)" in capsys.readouterr().err
    # libvirt then releases the VM it did not start.
    assert hook(dom, "release", "end") == 0
    assert [c.split()[1] for c in lib.calls()] == ["pre-start"]
    assert not (Path(os.environ["HYPERLITE_HOOKSCRIPT_RUNS_DIR"]) / "web.refused").exists()


def test_post_start_and_post_stop_never_fail_the_vm(lib):
    lib.add("ok.sh")
    lib.refuse("post-start")
    lib.refuse("post-stop")
    dom = FakeDomain()
    hookscript.set_script(dom, "ok.sh")
    assert hook(dom, "started", "begin") == 0
    assert hook(dom, "release", "end") == 0
    assert [r["exit_code"] for r in hookscript.runs("web")] == [3, 3]


def test_a_script_changed_to_be_writable_by_others_is_not_run(lib, capsys):
    path = lib.add("ok.sh")
    dom = FakeDomain()
    hookscript.set_script(dom, "ok.sh")
    path.chmod(0o777)
    assert hook(dom, "prepare", "begin") == 1
    assert "writable by others" in capsys.readouterr().err
    assert lib.calls() == []
    assert hookscript.runs("web")[-1]["exit_code"] is None


def test_a_vm_without_a_script_or_with_an_odd_name_is_left_alone(lib):
    lib.add("ok.sh")
    assert hook(FakeDomain(), "prepare", "begin") == 0
    odd = FakeDomain(name="../x")
    hookscript.set_script(odd, "ok.sh")
    assert hook(odd, "prepare", "begin") == 0
    assert lib.calls() == []


def test_a_script_that_hangs_is_stopped_and_fails(lib, monkeypatch):
    lib.add("slow.sh", "#!/bin/sh\necho begun\nsleep 5\n")
    monkeypatch.setattr(hookscript, "RUN_TIMEOUT_S", 0.5)
    run = hookscript.run("web", "pre-stop", "slow.sh")
    assert not run["ok"] and run["exit_code"] is None and "Stopped after 0.5 s" in run["output"]


def test_a_script_leaving_a_process_in_the_background_returns_at_once(lib):
    # A post-start that starts a daemon: its child keeps the output open, and waiting for that would hold the VM's
    # start in libvirt for as long as the daemon lives.
    lib.add("daemon.sh", "#!/bin/sh\necho started\nsleep 20 &\nexit 0\n")
    begun = time.monotonic()
    run = hookscript.run("web", "post-start", "daemon.sh")
    assert time.monotonic() - begun < 5
    assert run["ok"] and run["output"] == "started\n"


def test_a_script_that_hangs_is_stopped_with_its_children(lib, monkeypatch, tmp_path):
    lib.add("stuck.sh", f"#!/bin/sh\nsleep 30 &\necho $! > {tmp_path}/child\nwait\n")
    monkeypatch.setattr(hookscript, "RUN_TIMEOUT_S", 1)
    begun = time.monotonic()
    run = hookscript.run("web", "pre-start", "stuck.sh")
    assert time.monotonic() - begun < 5 and not run["ok"] and run["exit_code"] is None
    child = int((tmp_path / "child").read_text())
    time.sleep(0.2)
    assert not Path(f"/proc/{child}").exists() or "Z" in Path(f"/proc/{child}/stat").read_text().split()[2]


def test_only_the_last_runs_are_kept(lib, monkeypatch):
    lib.add("ok.sh")
    monkeypatch.setattr(hookscript, "RUNS_KEPT", 3)
    for _ in range(5):
        hookscript.run("web", "post-start", "ok.sh")
    assert len(hookscript.runs("web")) == 3


def test_the_hook_script_reaches_python_only_for_a_vm_with_a_script(tmp_path):
    fake = tmp_path / "code"
    (fake / "venv/bin").mkdir(parents=True)
    marker = tmp_path / "python-ran"
    (fake / "venv/bin/python3").write_text(f'#!/bin/sh\ncat > /dev/null\necho "$@" > {marker}\n')
    (fake / "venv/bin/python3").chmod(0o755)
    env = {**os.environ, "HYPERLITE_CODE": str(fake)}
    script = str(Path("scripts/libvirt-hook-hookscript").resolve())
    with_script = FakeDomain()
    hookscript.set_script(with_script, "ok.sh")
    cases = [
        (["web", "start", "begin", "-"], with_script.XMLDesc(), False),
        (["web", "prepare", "begin", "-"], FakeDomain().XMLDesc(), False),
        (["web", "prepare", "begin", "-"], with_script.XMLDesc(), True),
        (["web", "release", "end", "-"], with_script.XMLDesc(), True),
    ]
    for args, xml, reaches in cases:
        marker.unlink(missing_ok=True)
        r = subprocess.run([script, *args], input=xml, text=True, env=env, capture_output=True)
        assert r.returncode == 0, r.stderr
        assert marker.exists() == reaches, args
    assert marker.read_text().split()[:3] == ["-m", "app.core.hookscript", "hook"]


def test_libvirts_error_for_a_refused_start_keeps_the_scripts_words():
    libvirt_msg = (
        "Hook script execution failed: internal error: Child process (LC_ALL=C PATH=/usr/bin HOME=/root "
        "/etc/libvirt/hooks/qemu.d/hyperlite-hookscript web prepare begin -) unexpected exit status 1: "
        "pre-start hook script failed (exit code 3): maintenance\n"
    )
    assert hookscript.start_error(libvirt_msg) == "pre-start hook script failed (exit code 3): maintenance"
    assert hookscript.start_error("no network") == "no network"


# --- Another node ---


@pytest.fixture()
def remote(monkeypatch):
    """A cluster member reached over SSH: the commands sent, and what the module there answers."""
    monkeypatch.setattr(
        "app.core.cluster.get_node",
        lambda name: {"hostname": "node2.example", "ssh_port": 22, "ssh_user": "root"} if name == "n2" else None,
    )
    monkeypatch.setattr("app.core.cluster.node_ssh_options", lambda extra=None: ["-o", "BatchMode=yes"])
    calls = []

    def run(cmd, input=None, **kw):
        calls.append((cmd, input))
        args = json.loads(input)["args"]
        if args[0] == "list":
            out = [{"name": "ok.sh", "usable": True}]
        elif args[0] == "run":
            out = {"phase": args[2], "ok": False, "exit_code": 2, "seconds": 0.1, "output": "busy\n", "at": 1}
        else:
            return subprocess.CompletedProcess(cmd, 255, "", "ssh: connect to host node2.example: refused")
        return subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")

    monkeypatch.setattr(hookscript.subprocess, "run", run)
    return calls


def test_another_nodes_library_and_pre_stop_go_through_the_module_there(remote):
    assert hookscript.list_scripts_on("n2") == [{"name": "ok.sh", "usable": True}]
    cmd, request = remote[-1]
    assert cmd[0] == "ssh" and "root@node2.example" in cmd
    assert cmd[-1] == "cd /usr/lib/hyperlite && /usr/lib/hyperlite/venv/bin/python3 -m app.core.hookscript remote"
    assert json.loads(request)["args"] == ["list"]
    dom = FakeDomain(active=True)
    hookscript.set_script(dom, "ok.sh")
    run = hookscript.pre_stop(dom, node="n2")
    assert run["exit_code"] == 2 and run["phase"] == "pre-stop"
    cmd, request = remote[-1]
    assert cmd[-1].endswith("-m app.core.hookscript remote"), "the command line never carries a name"
    assert json.loads(request)["args"] == ["run", "web", "pre-stop", "ok.sh"]
    assert "<domain>" in json.loads(request)["stdin"]
    with pytest.raises(hookscript.HookError, match=r"node2\.example: refused"):
        hookscript.runs_on("n2", "web")
    with pytest.raises(hookscript.HookError, match="not found"):
        hookscript.list_scripts_on("gone")


def test_only_the_modules_commands_with_plain_names_reach_another_node(remote):
    for args in (["run", "web;reboot", "pre-stop", "ok.sh"], ["runs", "$(id)"], ["run", "web", "x", "ok.sh"], ["rm"]):
        with pytest.raises(hookscript.HookError):
            hookscript._on_node("n2", args)
    with pytest.raises(hookscript.HookError, match="Invalid name"):
        hookscript.runs_on("n2", "a b")
    assert remote == [], "nothing was sent"


def test_the_module_answers_the_other_nodes_commands(lib, capsys):
    lib.add("ok.sh")
    assert hookscript._main(["list"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["name"] == "ok.sh"
    assert hookscript._main(["check", "nope.sh"]) == 0
    assert "No script" in json.loads(capsys.readouterr().out)["error"]
    assert hookscript._main(["run", "web", "reboot", "ok.sh"]) == 2, "an unknown phase is refused"


def test_the_other_node_checks_a_request_again_before_acting(lib, capsys):
    lib.add("ok.sh")
    request = json.dumps({"args": ["run", "web", "pre-stop", "ok.sh"], "stdin": "<domain/>"})
    assert hookscript._main(["remote"], io.StringIO(request)) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["ok"] and answer["output"] == "said pre-stop\n"
    assert lib.calls()[-1].split()[:2] == ["web", "pre-stop"]
    for bad in ('{"args": ["run", "web;id", "pre-stop", "ok.sh"]}', '{"args": ["rm", "-rf"]}', "not json", "{}"):
        assert hookscript._main(["remote"], io.StringIO(bad)) == 2, bad
    assert len(lib.calls()) == 1


# --- The API ---


class ApiDomain(FakeDomain):
    def __init__(self):
        super().__init__(active=True)
        self.destroyed = 0
        self.shutdowns = 0

    def destroy(self):
        self.destroyed += 1
        self.active = False

    def shutdown(self):
        self.shutdowns += 1


@pytest.fixture()
def api(monkeypatch, lib, daemon_libvirt):
    from app.core import guest_agent
    from app.routers.vms import hookscript as routes
    from app.routers.vms import lifecycle

    dom = ApiDomain()

    class Conn:
        def lookupByName(self, name):
            if name != "web":
                raise libvirt.libvirtError("no domain")
            return dom

        def close(self):
            pass

    for module in (routes, lifecycle):
        monkeypatch.setattr(module, "open_conn", lambda *a, **k: Conn())
    daemon_libvirt(Conn())  # hyperlite-daemon runs the pre-stop and stops the VM, in this process in the tests
    monkeypatch.setattr(lifecycle, "_domain_summary", lambda d: {"name": d.name()})
    # The shutdown's watcher keeps asking the guest: here it has nothing to watch.
    monkeypatch.setattr(lifecycle, "_watch_stop", lambda *a, **k: None)
    monkeypatch.setattr(guest_agent, "shutdown", lambda d: d.shutdown() or "acpi")
    lib.add("ok.sh")
    return dom


def test_an_administrator_attaches_a_script_of_the_library(client, auth_headers, api):
    admin = auth_headers("admin")
    r = client.get("/vms/web/hookscript", headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["script"] is None and [s["name"] for s in r.json()["library"]] == ["ok.sh"]
    r = client.put("/vms/web/hookscript", json={"script": "ok.sh"}, headers=admin)
    assert r.status_code == 200, r.text
    assert hookscript.get_script(api) == "ok.sh"
    for bad, status in [("nope.sh", 422), ("../ok.sh", 422)]:
        r = client.put("/vms/web/hookscript", json={"script": bad}, headers=admin)
        assert r.status_code == status, r.text
    assert hookscript.get_script(api) == "ok.sh"
    assert client.put("/vms/web/hookscript", json={"script": None}, headers=admin).status_code == 200
    assert hookscript.get_script(api) is None


def test_only_an_administrator_attaches_one_and_others_see_no_library(client, auth_headers, api):
    viewer = auth_headers("viewer", "viewer")
    assert client.put("/vms/web/hookscript", json={"script": "ok.sh"}, headers=viewer).status_code == 403
    r = client.get("/vms/web/hookscript", headers=viewer)
    assert r.status_code == 200 and r.json()["library"] is None


def test_a_failing_pre_stop_refuses_the_shutdown_but_not_a_forced_stop(client, auth_headers, api, lib):
    admin = auth_headers("admin")
    hookscript.set_script(api, "ok.sh")
    lib.refuse("pre-stop")
    r = client.post("/vms/web/stop", headers=admin)
    assert r.status_code == 409 and "pre-stop hook script failed (exit code 3)" in r.json()["detail"]
    assert api.shutdowns == 0 and api.active
    r = client.post("/vms/web/stop?force=true", headers=admin)
    assert r.status_code == 200, r.text
    assert api.destroyed == 1
    assert [c.split()[1] for c in lib.calls()] == ["pre-stop", "pre-stop"]


def test_a_shutdown_runs_pre_stop_into_the_task_log(client, auth_headers, api, lib):
    from app.core.tasks import get_task_log

    admin = auth_headers("admin")
    hookscript.set_script(api, "ok.sh")
    r = client.post("/vms/web/stop", headers=admin)
    assert r.status_code == 200, r.text
    assert api.shutdowns == 1
    tasks = client.get("/tasks", headers=admin).json()
    items = tasks.get("items", tasks) if isinstance(tasks, dict) else tasks
    log = [e["message"] for e in get_task_log(items[0]["id"])]
    assert "Hook script, pre-stop: ok" in " ".join(log) and "said pre-stop" in log


def test_a_vm_without_a_script_stops_as_before(client, auth_headers, api, lib):
    r = client.post("/vms/web/stop", headers=auth_headers("admin"))
    assert r.status_code == 200 and api.shutdowns == 1 and lib.calls() == []


def test_the_runs_are_shown_with_the_script(client, auth_headers, api, lib):
    hookscript.set_script(api, "ok.sh")
    hookscript.run("web", "post-start", "ok.sh")
    r = client.get("/vms/web/hookscript", headers=auth_headers("admin"))
    assert r.json()["script"] == "ok.sh"
    assert [(x["phase"], x["ok"]) for x in r.json()["runs"]] == [("post-start", True)]
    assert json.dumps(r.json())
