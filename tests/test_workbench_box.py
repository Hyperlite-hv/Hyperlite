"""The Terminal's box (app/core/workbench_box.py): every protection of docs/design/workbench.md, section 6, is there."""

import pytest

from app.core import workbench_box as box

SID = "0123456789abcdef"
UID = 700001


def props():
    return box.box_properties(SID, UID, {"memory_mb": 256, "cpu_percent": 25, "tasks": 64, "max_hours": 2})


@pytest.mark.parametrize(
    "wanted",
    [
        "User=700001", "PrivateUsers=yes", "NoNewPrivileges=yes", "CapabilityBoundingSet=", "ProtectSystem=strict",
        "ProtectHome=tmpfs", "PrivateTmp=yes", "PrivateDevices=yes", "DevicePolicy=closed", "PrivateNetwork=yes",
        "RestrictNamespaces=yes", "ProtectProc=invisible", "ProcSubset=pid", "ProtectKernelModules=yes",
        "ProtectKernelTunables=yes", "RestrictSUIDSGID=yes", "SystemCallFilter=@system-service",
        "SystemCallArchitectures=native", "SystemCallErrorNumber=EPERM", "MemoryMax=256M", "MemorySwapMax=0", "OOMPolicy=continue", "CPUQuota=25%", "TasksMax=64",
        "RuntimeMaxSec=7200", "UMask=0077",
    ],
)  # fmt: skip
def test_every_protection_is_set(wanted):
    assert wanted in props()


def test_the_node_secrets_are_hidden():
    hidden = next(p for p in props() if p.startswith("InaccessiblePaths=")).split("=", 1)[1].split()
    for path in ("/etc/hyperlite", "/var/lib/hyperlite", "/var/lib/libvirt", "/run/libvirt", "/etc/shadow"):
        assert f"-{path}" in hidden
    denied = next(p for p in props() if p.startswith("SystemCallFilter=~"))
    assert "@mount" in denied and "@privileged" in denied and "@module" in denied


def test_only_the_home_and_the_session_dir_are_bound():
    binds = [p for p in props() if p.startswith(("BindPaths=", "ReadWritePaths=", "BindReadOnlyPaths="))]
    assert binds == [f"BindPaths={box.MOUNTS / str(UID)}:/home/workbench {box.RUN / SID}:/run/workbench"]


def test_ids_are_checked():
    with pytest.raises(ValueError):
        box.box_properties("../../etc", UID, None)
    with pytest.raises(ValueError):
        box.box_properties(SID, 0, None)  # never root
    with pytest.raises(ValueError):
        box.attach_argv(SID, 1000)


def test_commands():
    argv = box.box_argv(SID, UID, None)
    assert argv[:3] == ["systemd-run", "--quiet", "--collect"] and f"--unit=hyperlite-workbench-{SID}.service" in argv
    assert argv[-1].endswith("scripts/workbench/box.sh")
    relay = box.relay_argv(SID, UID)
    assert "--property=IPAddressAllow=127.0.0.1" in relay and "--property=IPAddressDeny=any" in relay
    assert relay[-3:] == ["unix-to-tcp", str(box.RUN / SID / "api.sock"), "127.0.0.1"] or relay[-4] == "unix-to-tcp"
    attach = box.attach_argv(SID, UID)
    assert attach[:2] == ["setpriv", f"--reuid={UID}"] and "--no-new-privs" in attach
    # Attach only: never a command that would start a tmux server (and shells) outside the box.
    assert attach[-3:] == ["attach-session", "-t", "main"] and "new-session" not in attach
