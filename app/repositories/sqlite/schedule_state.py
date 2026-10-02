"""This node's own next run of the jobs every node of a cluster runs for the VMs it hosts (grouped backups, replication).

The job rows are shared (app/repositories/cfs/tables.py) and each node runs them for its own VMs, at its own pace: a
run on one node must not move the next run of the others. So in a cluster a run records the next one here, in a table
each node keeps for itself, together with the shared value it was computed from. Editing the job writes a new shared
value, which then wins again: the new schedule starts from the edit, on every node.
"""


def overlay(db, kind, rows, keep_shared=False):
    """Give each job row this node's next run. keep_shared: also keep the shared value under "prochaine_partagee",
    which record() needs."""
    states = {
        r["job_id"]: r
        for r in db.execute(
            "SELECT job_id, base, prochaine_execution FROM schedule_state WHERE kind = ?", (kind,)
        ).fetchall()
    }
    for row in rows:
        shared = row["prochaine_execution"]
        if keep_shared:
            row["prochaine_partagee"] = shared
        state = states.get(row["id"])
        if state and state["base"] == shared:
            row["prochaine_execution"] = state["prochaine_execution"]
    return rows


def due(db, kind, rows, now):
    return [r for r in overlay(db, kind, rows, keep_shared=True) if r["prochaine_execution"] <= now]


def record(db, kind, job_id, base, next_run):
    db.execute(
        "INSERT INTO schedule_state (kind, job_id, base, prochaine_execution) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(kind, job_id) DO UPDATE SET base = excluded.base, prochaine_execution = excluded.prochaine_execution",
        (kind, job_id, base, next_run),
    )


def forget(db, kind, job_id):
    db.execute("DELETE FROM schedule_state WHERE kind = ? AND job_id = ?", (kind, job_id))
