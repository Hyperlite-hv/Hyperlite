"""Automation jobs as the API manages them: list, detail, creation with step checks, deletion (predefined jobs are
kept), runs. Execution itself is app/core/jobs.py."""

from datetime import UTC, datetime

from app.domain.common import AlreadyExists
from app.repositories import registry

STEP_TYPES = ("vm", "host", "chaque_cible")


class JobNotFound(LookupError):
    pass


class RunNotFound(LookupError):
    pass


class JobInvalid(ValueError):
    pass


class JobExists(ValueError):
    pass


class JobProtected(PermissionError):
    pass


async def list_jobs():
    return await registry.automation().list_jobs()


async def get_job(job_id):
    job = await registry.automation().get_job(job_id)
    if not job:
        raise JobNotFound(job_id)
    return job


async def create_job(name, description, steps, username):
    """steps: dicts (cible_type, cible, commande, condition_type, condition_valeur)."""
    if not steps:
        raise JobInvalid("A job needs at least one step")
    for s in steps:
        if s["cible_type"] not in STEP_TYPES:
            raise JobInvalid("cible_type invalide")
        if s["cible_type"] == "vm" and not s["cible"]:
            raise JobInvalid("cible requise quand cible_type='vm'")
    try:
        job_id = await registry.automation().create_job(
            name, description, username, datetime.now(UTC).isoformat(), steps
        )
    except AlreadyExists as e:
        raise JobExists(str(e)) from None
    return await get_job(job_id)


async def delete_job(job_id):
    """The deleted job's name."""
    job = await get_job(job_id)
    if job["predefined_key"]:
        raise JobProtected("This predefined job cannot be deleted")
    await registry.automation().delete_job(job_id)
    return job["name"]


async def list_runs(job_id):
    return await registry.automation().list_runs(job_id)


async def get_run(run_id):
    run = await registry.automation().get_run(run_id)
    if not run:
        raise RunNotFound(run_id)
    return run
