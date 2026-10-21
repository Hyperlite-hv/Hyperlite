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
            return subprocess.CompletedProcess(cmd, 0, "1:1.0.2\n", "")
        if cmd[0] == "dpkg-query":
            return subprocess.CompletedProcess(cmd, 0, "1:1.1.0", "")
        if cmd[:2] == ["apt-cache", "policy"]:
            return subprocess.CompletedProcess(cmd, 0, "hyperlite:\n  Installed: 1:1.1.0\n  Candidate: 1:1.1.1\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def run(cmd, cwd=None, **k):
        calls["run"].append(cmd)
        if cmd[:2] == ["apt-get", "download"]:
            (Path(cwd) / "hyperlite_1.1.0_amd64.deb").write_text("deb")
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
    assert [p.name for p in kept] == ["hyperlite_1.1.0_amd64.deb"]
    assert ["apt-get", "download", "hyperlite=1:1.1.0"] in node["run"]


def test_the_return_reinstalls_it_through_the_watchdog_with_the_service_stopped(
    node, client, auth_headers, monkeypatch
):
    """Not from this process: the previous version's package may move the files back while it runs (seen on a test
    node, 1.1 back to 1.0.3: a second, empty database). The watchdog stops the service, installs the package again,
    removes it (one step back only) and starts the service (tests/test_update_watchdog.py)."""
    spawned = []
    monkeypatch.setattr(update, "_spawn_outside_service", lambda unit, argv: spawned.append((unit, argv)))
    admin = auth_headers("root")
    assert client.post("/update/rollback", headers=admin).status_code == 409  # nothing kept yet
    update.PREVIOUS_DIR.mkdir(parents=True)
    deb = update.PREVIOUS_DIR / "hyperlite_1.0.2_amd64.deb"
    deb.write_text("deb")
    r = client.post("/update/rollback", headers=admin)
    assert r.status_code == 202 and r.json()["version"] == "1.0.2"
    for _ in range(50):
        if spawned:
            break
        import time

        time.sleep(0.05)
    assert not [c for c in node["run"] if c[:2] == ["apt-get", "install"]]  # never from the running service
    [(unit, argv)] = spawned
    assert unit == "hyperlite-update-watchdog"
    assert "HYPERLITE_ROLLBACK_NOW=1" in argv and f"HYPERLITE_PREVIOUS_DEB={deb}" in argv


def test_the_database_schema_only_grows():
    """The previous version runs on the newer schema: no column or table it reads is ever dropped or renamed. The
    one table rebuilt (acl, to widen a CHECK) keeps its name and columns, through a temporary *_pre_* table."""
    source = (ROOT / "app" / "core" / "database.py").read_text()
    assert not re.search(r"DROP\s+COLUMN|RENAME\s+COLUMN", source, re.I)
    for m in re.finditer(r"(?:DROP\s+TABLE|RENAME\s+TO)\s+(\w+)", source, re.I):
        assert "_pre_" in m.group(1), m.group(0)


def test_a_version_that_cannot_be_kept_leaves_no_older_one_offered(node, monkeypatch):
    """The installed version is not on the mirror (a node installed from the offline ISO, updated from a file, or
    without network): the return is not offered, rather than one to a much older version kept earlier."""
    update.PREVIOUS_DIR.mkdir(parents=True)
    (update.PREVIOUS_DIR / "hyperlite_0.9.0_amd64.deb").write_text("an older version kept by an earlier update")

    def run(cmd, cwd=None, **k):
        node["run"].append(cmd)
        return subprocess.CompletedProcess(cmd, 100, "", "E: Version '1:1.1.0' for 'hyperlite' was not found")

    monkeypatch.setattr(update.subprocess, "run", run)
    update._keep_previous("t1")
    assert list(update.PREVIOUS_DIR.glob("hyperlite_*.deb")) == []
    assert update._previous() is None
    assert any("will not be offered" in line for line in node["logs"])


def test_no_return_below_1_0_once_on_it(node, client, auth_headers, monkeypatch):
    """1.0 renamed the database and the cluster's configuration (API v1): a dated version kept by the update to 1.0
    is never offered, nor installed, since it could not read them."""
    monkeypatch.setattr(
        update,
        "_run_c",
        lambda cmd, **k: subprocess.CompletedProcess(
            cmd, 0, "2026.10.19.0616\n" if cmd[:2] == ["dpkg-deb", "-f"] else "1:1.0.0", ""
        ),
    )
    monkeypatch.setattr(update, "_raw_installed_version", lambda: "1:1.0.0")
    update.PREVIOUS_DIR.mkdir(parents=True)
    (update.PREVIOUS_DIR / "hyperlite_2026.10.19.0616_amd64.deb").write_text("deb")
    assert update._previous() is None
    assert client.post("/update/rollback", headers=auth_headers("root")).status_code == 409
    assert not [c for c in node["run"] if c[:2] == ["apt-get", "install"]]


def test_the_update_to_1_0_keeps_no_dated_package(node, monkeypatch):
    monkeypatch.setattr(update, "_raw_installed_version", lambda: "2026.10.19.0616")
    monkeypatch.setattr(update, "_raw_candidate_version", lambda: "1:1.0.0")
    update.PREVIOUS_DIR.mkdir(parents=True)
    (update.PREVIOUS_DIR / "hyperlite_2026.10.18.2047_amd64.deb").write_text("kept by an earlier update")
    update._keep_previous("t1")
    assert list(update.PREVIOUS_DIR.glob("hyperlite_*.deb")) == []
    assert not [c for c in node["run"] if c[:2] == ["apt-get", "download"]]


def test_no_return_to_a_version_that_does_not_know_the_layout_of_1_1(node, client, auth_headers, monkeypatch):
    """From 1.1 the files live where Proxmox VE keeps its own (app/core/paths.py); 1.0.2 is the first version that
    moves them back when installed again. 1.0.1 would start without its database, keys and certificate."""
    monkeypatch.setattr(
        update,
        "_run_c",
        lambda cmd, **k: subprocess.CompletedProcess(
            cmd, 0, "1:1.0.1\n" if cmd[:2] == ["dpkg-deb", "-f"] else "1:1.1.0", ""
        ),
    )
    update.PREVIOUS_DIR.mkdir(parents=True)
    (update.PREVIOUS_DIR / "hyperlite_1.0.1_amd64.deb").write_text("deb")
    assert update._previous() is None
    assert client.post("/update/rollback", headers=auth_headers("root")).status_code == 409


@pytest.mark.parametrize(
    ("value", "knows"),
    [
        ("1:1.0.2", True),
        ("1.0.3~test.20261022.0900", True),
        ("1:1.1.0", True),
        ("2.0.0", True),
        ("1:1.0.1", False),
        ("1.0.2~test.20261020.2039", False),
        ("2026.10.19.0616", False),
        (None, False),
    ],
)
def test_the_versions_that_know_the_layout_of_1_1(value, knows):
    from app.core import version

    assert version.knows_layout(value) is knows


@pytest.mark.parametrize(
    ("value", "dated"),
    [
        ("2026.10.19.0616", True),
        ("1:1.0.0", False),
        ("1.0.1~test.20261020.2039", False),
        ("2.3.4", False),
        (None, True),
    ],
)
def test_dated_versions_are_told_apart(value, dated):
    from app.core import version

    assert version.dated(value) is dated
