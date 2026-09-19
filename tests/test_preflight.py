"""Installation preflight checks."""

import json
import subprocess
import sys

import pytest

from app.core import preflight


def statuses(checks):
    return {check["id"]: check["statut"] for check in checks}


def test_current_interpreter_has_every_required_python_module():
    checks = preflight.check_python(requirements="requirements.txt")
    assert [c for c in checks if c["statut"] == "blocking"] == []
    assert statuses(checks)["python:websocket"] == "ok"


def test_a_bare_interpreter_reports_blocking_missing_modules(tmp_path):
    bare = tmp_path / "python"
    bare.write_text('#!/bin/sh\nexec /usr/bin/env python3 -S -c "$2" "$3"\n')
    bare.chmod(0o755)
    checks = preflight.check_python(str(bare))
    assert any(c["statut"] == "blocking" for c in checks)


def test_missing_interpreter_is_blocking(tmp_path):
    checks = preflight.check_python(str(tmp_path / "does-not-exist"))
    assert checks[0]["statut"] == "blocking"


def test_websocket_library_absence_only_disables_the_related_features(monkeypatch):
    monkeypatch.setattr(
        preflight,
        "probe_python",
        lambda python=None: {
            "modules": dict.fromkeys(m[0] for m in preflight.PYTHON_MODULES),
            "versions": {},
            "websocket": None,
        },
    )
    checks = {c["id"]: c for c in preflight.check_python()}
    assert checks["python:websocket"]["statut"] == "disabled"
    assert not any(c["statut"] == "blocking" for c in checks.values())


def test_a_broken_optional_module_disables_its_feature_only(monkeypatch):
    modules = dict.fromkeys(m[0] for m in preflight.PYTHON_MODULES)
    modules["pyotp"] = "ImportError: broken"
    monkeypatch.setattr(
        preflight, "probe_python", lambda python=None: {"modules": modules, "versions": {}, "websocket": "websockets"}
    )
    checks = {c["id"]: c for c in preflight.check_python()}
    assert checks["python:pyotp"]["statut"] == "disabled"


def test_a_broken_required_module_blocks(monkeypatch):
    modules = dict.fromkeys(m[0] for m in preflight.PYTHON_MODULES)
    modules["libvirt"] = "ImportError: libvirt.so missing"
    monkeypatch.setattr(
        preflight, "probe_python", lambda python=None: {"modules": modules, "versions": {}, "websocket": "websockets"}
    )
    assert statuses(preflight.check_python())["python:libvirt"] == "blocking"


def test_version_drift_from_requirements_is_a_warning(tmp_path, monkeypatch):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("fastapi==1.0.0\n")
    modules = dict.fromkeys(m[0] for m in preflight.PYTHON_MODULES)
    monkeypatch.setattr(
        preflight,
        "probe_python",
        lambda python=None: {"modules": modules, "versions": {"fastapi": "2.0.0"}, "websocket": "websockets"},
    )
    assert statuses(preflight.check_python(requirements=str(requirements)))["python:fastapi"] == "warning"


def test_non_root_execution_is_blocking(monkeypatch):
    monkeypatch.setattr(preflight.os, "geteuid", lambda: 1000)
    assert statuses(preflight.check_system(offline=True))["root"] == "blocking"


@pytest.mark.parametrize("ram_mb,expected", [(512, "blocking"), (2048, "warning"), (8192, "ok")])
def test_memory_thresholds(monkeypatch, ram_mb, expected):
    monkeypatch.setattr(preflight, "_mem_total_mb", lambda: ram_mb)
    assert statuses(preflight.check_system(offline=True))["memoire"] == expected


def test_secure_boot_disables_zfs_instead_of_blocking(monkeypatch):
    monkeypatch.setattr(preflight, "_secure_boot", lambda: "active")
    monkeypatch.setattr(preflight, "_zfs_module_loaded", lambda: False)
    checks = {c["id"]: c for c in preflight.check_system(offline=True)}
    assert checks["zfs"]["statut"] == "disabled"
    assert "ZFS" in checks["zfs"]["fonctionnalite"]


def test_missing_kvm_disables_vms_but_does_not_block(monkeypatch):
    monkeypatch.setattr(preflight, "_kvm_device_present", lambda: False)
    checks = {c["id"]: c for c in preflight.check_system(offline=True)}
    assert checks["virtualisation_materielle"]["statut"] == "disabled"
    assert "KVM" in checks["virtualisation_materielle"]["fonctionnalite"]


def test_kvm_present_with_hardware_virtualisation_is_ok(monkeypatch):
    monkeypatch.setattr(preflight, "_kvm_device_present", lambda: True)
    monkeypatch.setattr(preflight, "_read", lambda path: "flags : fpu vmx sse2" if str(path) == "/proc/cpuinfo" else "")
    assert statuses(preflight.check_system(offline=True))["virtualisation_materielle"] == "ok"


def test_summary_counts_and_blocking_flag():
    checks = [{"statut": "ok"}, {"statut": "disabled", "fonctionnalite": "ZFS"}, {"statut": "blocking"}]
    summary = preflight.summarize(checks)
    assert summary["bloquant"] is True
    assert summary["compte"]["disabled"] == 1
    assert summary["fonctionnalites_desactivees"] == ["ZFS"]


def test_command_line_exit_code_reflects_blocking_checks(tmp_path):
    result = subprocess.run(
        [sys.executable, "app/core/preflight.py", "--only", "python", "--python", str(tmp_path / "missing"), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["resume"]["bloquant"] is True


def test_script_runs_with_only_the_standard_library():
    """The script is executed by the package post-install script before the venv exists."""
    result = subprocess.run(
        [sys.executable, "-S", str(preflight.__file__), "--only", "system", "--offline", "--json"],
        capture_output=True,
        text=True,
        cwd="/",
        env={"PYTHONPATH": ""},
        check=False,
    )
    assert result.returncode in (0, 1), result.stderr
    assert "controles" in json.loads(result.stdout)
