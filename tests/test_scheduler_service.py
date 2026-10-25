"""hyperlite-scheduler (app/scheduler; docs/design/privilege-separation.md, section 14, 4c): the schedulers out of the
API on an installed node, in it otherwise; the schema made by one service at a time."""

import inspect
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

from app import scheduler
from app.core import paths

UNITS = Path(__file__).resolve().parents[1] / "installer"


def test_the_scheduler_starts_every_scheduler(monkeypatch):
    started = []
    for module, name in (
        ("app.core.backups", "start_backup_scheduler"),
        ("app.core.replication", "start_replication_scheduler"),
        ("app.core.vm_cleanup", "start_auto_cleanup_scheduler"),
        ("app.core.update_check", "start_update_check_scheduler"),
        ("app.core.vm_boot", "start_boot_sequence"),
        ("app.core.cluster", "start_node_poller"),
        ("app.core.config_copy", "start_config_copy"),
        ("app.core.network_firewall", "reapply_all"),
    ):
        monkeypatch.setattr(f"{module}.{name}", lambda name=name: started.append(name))
    vlans = []
    monkeypatch.setattr("app.core.privileged.run", lambda op, **k: vlans.append(op))
    scheduler.start()
    assert len(started) == 8 and vlans == ["network.vlan_boot"]


def test_the_network_at_start_survives_a_daemon_that_refuses(monkeypatch, caplog):
    from app.daemon.registry import DaemonError

    monkeypatch.setattr("app.core.network_firewall.reapply_all", lambda: None)

    def refuse(op, **k):
        raise DaemonError("failed", "no bridge")

    monkeypatch.setattr("app.core.privileged.run", refuse)
    scheduler._at_start()
    assert "The VLANs of the running VMs were not set: no bridge" in caplog.text


def test_the_api_leaves_the_nodes_poll_the_copy_and_the_network_to_the_scheduler():
    import inspect

    from app import main

    source = inspect.getsource(main.on_startup)
    for name in ("start_node_poller", "start_config_copy", "reapply", "vlan_boot", "seed_admin", "encrypt_stored"):
        assert name not in source, name


def test_the_api_starts_them_only_without_the_service(monkeypatch):
    from app import main

    source = inspect.getsource(main.on_startup)
    assert "if not scheduler.separate():" in source and "scheduler.start()" in source
    for name in ("start_backup_scheduler", "start_replication_scheduler", "start_boot_sequence"):
        assert name not in source
    monkeypatch.delenv(scheduler.SEPARATE_ENV, raising=False)
    assert not scheduler.separate()
    monkeypatch.setenv(scheduler.SEPARATE_ENV, "1")
    assert scheduler.separate()


def test_the_service_runs_until_systemd_stops_it(monkeypatch):
    from app.scheduler import __main__ as entry

    ran = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr("app.core.database.init_db", lambda: ran.append("schema"))
    monkeypatch.setattr("app.core.tasks.close_interrupted_tasks", lambda: ran.append("tasks"))
    monkeypatch.setattr(
        "app.core.cluster_lead.start_lead_loop", lambda follow_setup: ran.append(("lead", follow_setup))
    )
    monkeypatch.setattr(scheduler, "start", lambda: ran.append("schedulers"))
    monkeypatch.delenv("HYPERLITE_SERVICE", raising=False)
    previous = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    timer = threading.Timer(0.3, os.kill, (os.getpid(), signal.SIGTERM))
    timer.start()
    try:
        entry.main()  # returns once SIGTERM came
    finally:
        timer.cancel()
        signal.signal(signal.SIGTERM, previous[0])
        signal.signal(signal.SIGINT, previous[1])
    assert ran == ["schema", "tasks", ("lead", False), "schedulers"]
    assert os.environ["HYPERLITE_SERVICE"] == "scheduler"  # its tasks' runner
    monkeypatch.delenv("HYPERLITE_SERVICE")


def test_two_services_make_the_schema_one_after_the_other(tmp_path):
    """The API and the scheduler start together: both bring a fresh database up to date, without failing."""
    env = {
        **os.environ,
        "HYPERLITE_DB_PATH": str(tmp_path / "fresh.db"),
        "HYPERLITE_LOCK_DIR": str(paths.LOCKS),
        "PYTHONPATH": str(paths.CODE),
    }
    code = "from app.core.database import init_db; init_db()"
    procs = [
        subprocess.Popen([sys.executable, "-c", code], cwd=paths.CODE, env=env, stderr=subprocess.PIPE)
        for _ in range(3)
    ]
    for proc in procs:
        _out, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, err.decode()[-2000:]


def test_the_units_tie_the_scheduler_to_the_api():
    unit = (UNITS / "hyperlite-scheduler.service").read_text()
    assert "ExecStart=/usr/lib/hyperlite/venv/bin/python -m app.scheduler" in unit
    assert "PartOf=hyperlite.service" in unit and "After=hyperlite-daemon.service hyperlite.service" in unit
    assert "Environment=HYPERLITE_SCHEDULER_SERVICE=1" in unit and "Environment=HYPERLITE_SERVICE=scheduler" in unit
    api = (UNITS / "hyperlite.service").read_text()
    assert "Environment=HYPERLITE_SCHEDULER_SERVICE=1" in api and "hyperlite-scheduler.service" in api
    postinst = (UNITS / "deb" / "postinst").read_text()
    assert "installer/hyperlite-scheduler.service" in postinst and "enable hyperlite-scheduler.service" in postinst
    assert "hyperlite-scheduler.service" in (UNITS / "deb" / "prerm").read_text()
    assert "hyperlite-scheduler.service" in (UNITS / "deb" / "postrm").read_text()
