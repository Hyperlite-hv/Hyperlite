"""The timer that undoes a host change unless it is confirmed in time (the host's network, app/core/host_network.py,
and its firewall, app/core/host_firewall.py): a transient systemd timer, armed before anything changes, that runs
`python -m <module> rollback <id>`. It needs neither the API nor hyperlite-daemon, so a change that cuts the
administrator off is undone even if they are gone."""

import contextlib
import subprocess
import sys

from app.core import paths


class TimerError(RuntimeError):
    pass


def start(unit, module, change_id, timeout_s):
    stop(unit)
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(["systemctl", "reset-failed", f"{unit}.service"], capture_output=True, timeout=30)
    argv = [
        "systemd-run",
        f"--unit={unit}",
        f"--on-active={int(timeout_s)}s",
        "--timer-property=AccuracySec=1s",
        f"--working-directory={paths.CODE}",
        f"--setenv=PYTHONPATH={paths.CODE}",
        sys.executable,
        "-m",
        module,
        "rollback",
        change_id,
    ]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise TimerError(f"The rollback timer could not be started: {(r.stderr or r.stdout).strip()[-300:]}")


def stop(unit):
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(["systemctl", "stop", f"{unit}.timer"], capture_output=True, timeout=30)
