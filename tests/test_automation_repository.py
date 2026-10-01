"""The automation repository (control plane v2, lot 4): jobs with their steps, runs with their logs, and the
predefined job, run against every backend (SQLite today)."""

import asyncio

import pytest

from app.domain.common import AlreadyExists

STEP = {
    "cible_type": "host",
    "cible": None,
    "commande": "uptime",
    "condition_type": "exit_code",
    "condition_valeur": "0",
}


@pytest.fixture(params=["sqlite"])
def repo(request, database):
    from app.repositories.sqlite.automation import SqliteAutomationRepository

    return SqliteAutomationRepository()


def run(coro):
    return asyncio.run(coro)


def test_jobs_with_steps(repo):
    job_id = run(repo.create_job("nightly", "check", "alice", "t0", [STEP, {**STEP, "commande": "df -h"}]))
    with pytest.raises(AlreadyExists):
        run(repo.create_job("nightly", None, "bob", "t1", [STEP]))
    job = run(repo.get_job(job_id))
    assert [s["commande"] for s in job["steps"]] == ["uptime", "df -h"] and job["created_by"] == "alice"
    assert [j["name"] for j in run(repo.list_jobs())] == ["nightly"]
    run(repo.delete_job(job_id))
    assert run(repo.get_job(job_id)) is None


def test_runs_and_their_logs(repo):
    job_id = run(repo.create_job("nightly", None, "alice", "t0", [STEP]))
    repo.sync.create_run("r1", job_id, "task-1", True, '["web"]', "t1")
    repo.sync.log_step("r1", 0, "host", "uptime", "up 3 days", "", 0, True, "t2")
    repo.sync.close_run("r1", "succes", "t3", "1/1 steps")
    detail = run(repo.get_run("r1"))
    assert (detail["statut"], detail["dry_run"], detail["resultat"]) == ("succes", 1, "1/1 steps")
    assert [(log["stdout"], log["reussi"]) for log in detail["logs"]] == [("up 3 days", 1)]
    assert [r["id"] for r in run(repo.list_runs(job_id))] == ["r1"] and run(repo.get_run("nope")) is None


def test_the_predefined_job_is_created_once_and_kept_current(repo):
    first = repo.sync.ensure_predefined("lb", "Old name", "old", "t0")
    assert repo.sync.ensure_predefined("lb", "Deploy a load balancer", "new", "t1") == first
    job = run(repo.get_job(first))
    assert (job["name"], job["description"], job["predefined_key"]) == ("Deploy a load balancer", "new", "lb")
