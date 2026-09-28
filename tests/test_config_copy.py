"""Copy of the cluster configuration and promotion of a node: the bundle (a consistent snapshot without telemetry,
the two keys, the digest), when a copy is due, the transfer commands, and a full promotion that installs the copy and
renames what pointed at this node, refusing while the old controller still answers."""

import json
import shutil
import sqlite3
import subprocess
import tarfile
from datetime import timedelta
from pathlib import Path

import pytest

from app.core import config_copy


@pytest.fixture()
def controller(database, tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("HYPERLITE_SECRET_KEY=session-key\nHYPERLITE_ENCRYPTION_KEY=fernet-key\nOTHER=kept\n")
    monkeypatch.setattr(config_copy, "_env_path", lambda: env)
    with database.get_conn() as db:
        db.execute("INSERT INTO users (username, hashed_password, role) VALUES ('antho', 'x', 'admin')")
        db.execute(
            "INSERT INTO nodes (name, hostname, statut, added_at) VALUES ('node2', 'node2.example.lan', 'en_ligne', '2026')"
        )
        db.execute(
            "INSERT INTO nodes (name, hostname, statut, added_at) VALUES ('node3', 'node3.example.lan', 'hors_ligne', '2026')"
        )
        db.execute(
            "INSERT INTO ha_protected_vms (vm_name, node, enabled_by, enabled_at) VALUES ('web', 'local', 'a', '2026'), ('db', 'node2', 'a', '2026')"
        )
        db.execute("INSERT INTO metrics_samples (cible, tier, scope, ts) VALUES ('web', 'raw', 'vm', '1')")
        db.commit()
    return tmp_path


def test_the_bundle_holds_a_snapshot_without_telemetry_and_the_two_keys(controller):
    bundle, meta = config_copy.build_bundle(controller)
    assert oct(bundle.stat().st_mode & 0o777) == "0o600"
    with tarfile.open(bundle) as tar:
        assert sorted(tar.getnames()) == ["cles.env", "hyperlite.db", "meta.json"]
        tar.extractall(controller / "x", filter="data")
    copy = sqlite3.connect(controller / "x" / "hyperlite.db")
    assert copy.execute("SELECT username FROM users WHERE username = 'antho'").fetchone()
    assert copy.execute("SELECT COUNT(*) FROM metrics_samples").fetchone()[0] == 0
    copy.close()
    assert (
        controller / "x" / "cles.env"
    ).read_text() == "HYPERLITE_SECRET_KEY=session-key\nHYPERLITE_ENCRYPTION_KEY=fernet-key\n"
    assert meta["empreinte"] == config_copy.config_digest()


def test_only_configuration_changes_change_the_digest(controller, database):
    before = config_copy.config_digest()
    with database.get_conn() as db:
        db.execute("INSERT INTO metrics_samples (cible, tier, scope, ts) VALUES ('web', 'raw', 'vm', '2')")
        db.execute("INSERT INTO audit_log (timestamp, action, result) VALUES ('t', 'login', 'succes')")
        db.commit()
    assert config_copy.config_digest() == before
    with database.get_conn() as db:
        db.execute("UPDATE users SET role = 'observateur' WHERE username = 'antho'")
        db.commit()
    assert config_copy.config_digest() != before


@pytest.fixture()
def remote(controller, monkeypatch):
    """ssh and scp stand-ins: scp really copies into a fake node directory, ssh records its command."""
    calls = []
    node_dir = controller / "node2-copy"
    node_dir.mkdir()

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[0] == "scp":
            shutil.copy(args[-2], node_dir / Path(args[-1].split(":", 1)[1]).name)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(config_copy.subprocess, "run", fake_run)
    monkeypatch.setattr("app.core.cluster.node_ssh_options", lambda extra=None: ["-i", "key", *(extra or [])])
    return calls, node_dir


def test_a_copy_goes_to_online_nodes_only_and_is_not_repeated_until_something_changes(controller, remote, database):
    calls, node_dir = remote
    assert config_copy.copy_now() == [{"node": "node2", "statut": "ok"}]  # node3 is offline
    assert (node_dir / "incoming.tar.gz").is_file()
    commands = [c[-1] for c in calls if c[0] == "ssh"]
    assert commands[0].startswith(f"mkdir -p -m 700 {config_copy.REMOTE_DIR}")
    assert "mv -f incoming.tar.gz latest.tar.gz" in commands[1] and "previous.tar.gz" in commands[1]
    assert config_copy.status()[0]["statut"] == "ok"
    assert not list((database.DB_PATH.parent / "data" / "config-copy").glob("*.tar.gz"))  # staging cleaned

    calls.clear()
    assert config_copy.copy_now() == []  # nothing changed, copy is recent
    with database.get_conn() as db:
        db.execute("INSERT INTO users (username, hashed_password, role) VALUES ('nico', 'x', 'observateur')")
        db.commit()
    assert config_copy.copy_now() == [{"node": "node2", "statut": "ok"}]  # a new user: copied at once


def test_an_old_copy_is_renewed_and_a_failed_one_is_recorded(controller, remote, database, monkeypatch):
    config_copy.copy_now()
    with database.get_conn() as db:
        old = (config_copy._now() - timedelta(seconds=config_copy.COPY_MAX_AGE_S + 1)).isoformat()
        db.execute("UPDATE config_copies SET copie_le = ?", (old,))
        db.commit()

    def failing(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="Connection refused")

    monkeypatch.setattr(config_copy.subprocess, "run", failing)
    assert config_copy.copy_now()[0]["statut"] == "echec"
    row = config_copy.status()[0]
    assert row["statut"] == "echec" and "Connection refused" in row["erreur"]


def test_a_promotion_installs_the_copy_and_makes_this_node_local(controller, tmp_path):
    bundle, meta = config_copy.build_bundle(controller)
    meta, work = config_copy.read_bundle(bundle)
    node_db = tmp_path / "node" / "hyperlite.db"
    node_db.parent.mkdir()
    node_db.write_text("old")
    Path(f"{node_db}-wal").write_text("stale")
    node_env = tmp_path / "node" / ".env"
    node_env.write_text("HYPERLITE_SECRET_KEY=node-own\nHYPERLITE_ENV_LABEL=DEV\n")

    result = config_copy.install(work, meta, {"node2"}, db_path=node_db, env_path=node_env, old_label="hl-old")
    assert result["noeud_local"] == ["node2"] and (node_db.parent / result["sauvegarde"]).read_text() == "old"
    assert not Path(f"{node_db}-wal").exists()
    db = sqlite3.connect(node_db)
    assert [r[0] for r in db.execute("SELECT name FROM nodes ORDER BY name")] == ["node3"]
    assert dict(db.execute("SELECT vm_name, node FROM ha_protected_vms").fetchall()) == {"web": "hl-old", "db": "local"}
    db.close()
    env = node_env.read_text()
    assert "HYPERLITE_SECRET_KEY=session-key" in env and "node-own" not in env
    assert "HYPERLITE_ENCRYPTION_KEY=fernet-key" in env and "HYPERLITE_ENV_LABEL=DEV" in env
    assert oct(node_env.stat().st_mode & 0o777) == "0o600"
    shutil.rmtree(work)


def test_the_promote_command_refuses_while_the_old_controller_answers(controller, monkeypatch, capsys):
    bundle, _ = config_copy.build_bundle(controller)
    monkeypatch.setattr(config_copy, "controller_answers", lambda host, timeout=5: True)
    calls = []
    monkeypatch.setattr(config_copy, "_systemctl", lambda *a: calls.append(a))
    assert config_copy.promote_cli(["--copy", str(bundle), "--yes"]) == 3
    assert "still answers" in capsys.readouterr().err and calls == []


def test_the_promote_command_needs_the_typed_confirmation(controller, monkeypatch):
    bundle, meta = config_copy.build_bundle(controller)
    monkeypatch.setattr(config_copy, "controller_answers", lambda host, timeout=5: False)
    monkeypatch.setattr("builtins.input", lambda prompt: "wrong")
    installed = []
    monkeypatch.setattr(config_copy, "install", lambda *a, **k: installed.append(a))
    assert config_copy.promote_cli(["--copy", str(bundle)]) == 4 and installed == []
    assert config_copy.promote_cli(["--copy", str(controller / "missing.tar.gz"), "--yes"]) == 2
    assert json.loads(json.dumps(meta))["version"] == 1
