"""The return to the version before the last update (docs/design/updates-1.0.md): its package is kept on the node,
reinstalled under the same safety net as an update, one step back only; the database schema only grows, so the
previous version runs on it."""

import re
import subprocess
from pathlib import Path

import pytest

from app.routers import update

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def node(database, tmp_path, monkeypatch):
    monkeypatch.setattr(update, "PREVIOUS_DIR", tmp_path / "previous")
    monkeypatch.setattr(update, "_install_method", lambda: "apt")
    calls = {"run": [], "spawned": [], "logs": []}

    def run_c(cmd, **k):
        if cmd[:2] == ["dpkg-deb", "-f"]:
            return subprocess.CompletedProcess(cmd, 0, "1:1.0.0\n", "")
        if cmd[0] == "dpkg-query":
            return subprocess.CompletedProcess(cmd, 0, "1:1.0.1", "")
        if cmd[:2] == ["apt-cache", "policy"]:
            return subprocess.CompletedProcess(cmd, 0, "hyperlite:\n  Installed: 1:1.0.1\n  Candidate: 1:1.1.0\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def run(cmd, cwd=None, **k):
        calls["run"].append(cmd)
        if cmd[:2] == ["apt-get", "download"]:
            (Path(cwd) / "hyperlite_1.0.1_amd64.deb").write_text("deb")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(update, "_run_c", run_c)
    monkeypatch.setattr(update.subprocess, "run", run)
    monkeypatch.setattr(update, "_backup", lambda task_id: tmp_path / "backup.tar.gz")
    monkeypatch.setattr(update, "_spawn_outside_service", lambda unit, argv: calls["spawned"].append(unit))
    monkeypatch.setattr(update, "task_log", lambda task_id, line: calls["logs"].append(line))
    return calls


def test_the_installed_package_is_kept_before_hyperlite_changes(node):
    update._keep_previous("t1")
    kept = list(update.PREVIOUS_DIR.glob("hyperlite_*.deb"))
    assert [p.name for p in kept] == ["hyperlite_1.0.1_amd64.deb"]
    assert ["apt-get", "download", "hyperlite=1:1.0.1"] in node["run"]


def test_the_return_reinstalls_it_under_the_watchdog_and_only_once(node, client, auth_headers):
    admin = auth_headers("root")
    assert client.post("/update/rollback", headers=admin).status_code == 409  # nothing kept yet
    update.PREVIOUS_DIR.mkdir(parents=True)
    deb = update.PREVIOUS_DIR / "hyperlite_1.0.0_amd64.deb"
    deb.write_text("deb")
    r = client.post("/update/rollback", headers=admin)
    assert r.status_code == 202 and r.json()["version"] == "1.0.0"
    for _ in range(50):
        if node["spawned"]:
            break
        import time

        time.sleep(0.05)
    install = next(c for c in node["run"] if c[:2] == ["apt-get", "install"])
    assert "--allow-downgrades" in install and install[-1] == str(deb)
    assert node["spawned"] == ["hyperlite-update-watchdog", "hyperlite-update-restart"]
    assert not deb.exists()  # one step back only


def test_the_database_schema_only_grows():
    """The previous version runs on the newer schema: no column or table it reads is ever dropped or renamed. The
    one table rebuilt (acl, to widen a CHECK) keeps its name and columns, through a temporary *_pre_* table."""
    source = (ROOT / "app" / "core" / "database.py").read_text()
    assert not re.search(r"DROP\s+COLUMN|RENAME\s+COLUMN", source, re.I)
    for m in re.finditer(r"(?:DROP\s+TABLE|RENAME\s+TO)\s+(\w+)", source, re.I):
        assert "_pre_" in m.group(1), m.group(0)
