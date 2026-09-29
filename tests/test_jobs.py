"""Automation jobs: predefined job bootstrap and the run lifecycle."""

import time

import libvirt
import pytest

from app.core import jobs


def test_predefined_job_is_created_once(database):
    first = jobs.ensure_lb_job_exists()
    assert jobs.ensure_lb_job_exists() == first
    with database.get_conn() as conn:
        rows = conn.execute("SELECT name FROM jobs WHERE predefined_key = ?", (jobs.LB_PREDEFINED_KEY,)).fetchall()
    assert [r["name"] for r in rows] == [jobs.LB_JOB_NAME]


def test_legacy_french_name_is_migrated_in_place(database):
    with database.get_conn() as conn:
        conn.execute(
            "INSERT INTO jobs (name, description, predefined_key, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                "Déployer un load balancing",
                "ancienne description",
                jobs.LB_PREDEFINED_KEY,
                "system",
                "2026-01-01T00:00:00",
            ),
        )
        conn.commit()
    job_id = jobs.ensure_lb_job_exists()
    with database.get_conn() as conn:
        row = conn.execute("SELECT name, description FROM jobs WHERE id = ?", (job_id,)).fetchone()
        count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert row["name"] == jobs.LB_JOB_NAME
    assert row["description"] == jobs.LB_JOB_DESCRIPTION
    assert count == 1  # renamed, not duplicated


# --- Running a job: a refused or failing run is never reported as a success ---


@pytest.fixture()
def no_libvirt(monkeypatch):
    def _refuse(*_a, **_k):
        raise libvirt.libvirtError("no libvirt in the test suite")

    monkeypatch.setattr(jobs, "open_conn", _refuse)


def _wait_run(database, run_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with database.get_conn() as conn:
            run = conn.execute("SELECT * FROM job_runs WHERE id = ?", (run_id,)).fetchone()
        if run and run["statut"] != "en_cours":
            return run
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} still running after {timeout}s")


def _audit(database, action):
    from app.core import audit

    audit._AUDIT_QUEUE.join()
    with database.get_conn() as conn:
        return conn.execute("SELECT result, error_message FROM audit_log WHERE action = ?", (action,)).fetchall()


def _custom_job(client, headers, steps):
    r = client.post("/jobs", json={"name": "custom", "steps": steps}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_predefined_job_without_enough_targets_is_refused_and_audited_as_failed(client, auth_headers, database):
    headers = auth_headers("alice")
    job_id = jobs.ensure_lb_job_exists()
    r = client.post(f"/jobs/{job_id}/run", json={"targets": [], "dry_run": True}, headers=headers)
    assert r.status_code == 422
    assert "At least 2 target VMs" in r.json()["detail"]
    with database.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM job_runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    assert [row["result"] for row in _audit(database, "run_job_requested")] == ["echec"]


def test_duplicate_or_empty_targets_are_refused(client, auth_headers, database):
    headers = auth_headers("alice")
    job_id = jobs.ensure_lb_job_exists()
    for targets in (["web1", "web1"], ["lb", " "]):
        r = client.post(f"/jobs/{job_id}/run", json={"targets": targets, "dry_run": True}, headers=headers)
        assert r.status_code == 422, targets


def test_per_target_steps_without_targets_are_refused(client, auth_headers, database):
    headers = auth_headers("alice")
    job_id = _custom_job(client, headers, [{"cible_type": "chaque_cible", "commande": "true"}])
    r = client.post(f"/jobs/{job_id}/run", json={"targets": [], "dry_run": True}, headers=headers)
    assert r.status_code == 422
    with database.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM job_runs").fetchone()[0] == 0


def test_unknown_job_is_404(client, auth_headers):
    r = client.post("/jobs/9999/run", json={"targets": [], "dry_run": True}, headers=auth_headers("alice"))
    assert r.status_code == 404


def test_predefined_dry_run_records_the_run_before_answering(client, auth_headers, database, no_libvirt):
    headers = auth_headers("alice")
    job_id = jobs.ensure_lb_job_exists()
    r = client.post(f"/jobs/{job_id}/run", json={"targets": ["lb", "web1"], "dry_run": True}, headers=headers)
    assert r.status_code == 202, r.text
    run = _wait_run(database, r.json()["run_id"])
    assert run["statut"] == "succes"
    with database.get_conn() as conn:
        logs = conn.execute("SELECT cible FROM job_run_logs WHERE run_id = ? ORDER BY id", (run["id"],)).fetchall()
        task = conn.execute("SELECT statut FROM tasks WHERE id = ?", (run["task_id"],)).fetchone()
    assert [row["cible"] for row in logs] == ["lb", "web1", "lb", "lb"]
    assert task["statut"] == "termine"
    assert [row["result"] for row in _audit(database, "run_job_requested")] == ["succes"]


def test_an_error_inside_the_run_thread_closes_the_run_as_failed(
    client, auth_headers, database, no_libvirt, monkeypatch
):
    headers = auth_headers("alice")
    job_id = _custom_job(client, headers, [{"cible_type": "host", "commande": "true"}])

    def _boom(*_a, **_k):
        raise OSError("disk on fire")

    monkeypatch.setattr(jobs, "_run_steps", _boom)
    r = client.post(f"/jobs/{job_id}/run", json={"targets": [], "dry_run": True}, headers=headers)
    assert r.status_code == 202, r.text
    run = _wait_run(database, r.json()["run_id"])
    assert run["statut"] == "echec"
    assert run["resultat"] == "ERROR: disk on fire"
    with database.get_conn() as conn:
        task = conn.execute("SELECT statut, erreur FROM tasks WHERE id = ?", (run["task_id"],)).fetchone()
    assert task["statut"] == "echec"
    assert "disk on fire" in task["erreur"]
    assert [row["result"] for row in _audit(database, "run_job")] == ["echec"]


def test_a_failing_step_stops_the_run(database, no_libvirt, monkeypatch):
    calls = []

    def _fake_command(cible_type, cible, commande, dry_run):
        calls.append(commande)
        return "", "nope", 1

    monkeypatch.setattr(jobs, "_run_command", _fake_command)
    with database.get_conn() as conn:
        cur = conn.execute("INSERT INTO jobs (name, created_by, created_at) VALUES ('j', 'x', 'now')")
        for i, cmd in enumerate(("first", "second")):
            conn.execute(
                "INSERT INTO job_steps (job_id, ordre, cible_type, commande, condition_type, condition_valeur) "
                "VALUES (?, ?, 'host', ?, 'exit_code', '0')",
                (cur.lastrowid, i, cmd),
            )
        conn.commit()
    run = _wait_run(database, jobs.start_job_run(cur.lastrowid))
    assert run["statut"] == "echec"
    assert run["resultat"] == "FAILED"
    assert calls == ["first"]
