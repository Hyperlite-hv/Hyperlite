"""Predefined automation job bootstrap."""

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
