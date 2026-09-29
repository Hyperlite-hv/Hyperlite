"""Which backups of a VM a retention policy keeps.

The policy is grandfather-father-son (GFS), as most backup tools do it: the `last` most recent backups, plus the
newest backup of each of the `daily` most recent days that have one, of the `weekly` most recent ISO weeks and of
the `monthly` most recent months. A backup kept by any rule is kept. With no daily, weekly or monthly count the
policy is the plain count it always was, so existing schedules behave the same.

Periods are counted among those that have a backup (a week without one does not use up a weekly slot), and each
period keeps its newest backup: the state at the end of the day, week or month.
"""

from datetime import datetime


def _parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def kept(backups, last, daily=None, weekly=None, monthly=None):
    """`backups`: (id, created ISO timestamp) pairs. Returns the set of ids the policy keeps."""
    ordered = sorted(backups, key=lambda b: _parse(b[1]), reverse=True)
    keep = {b[0] for b in ordered[: max(last or 0, 0)]}
    periods = (
        (daily, lambda d: d.date()),
        (weekly, lambda d: tuple(d.isocalendar())[:2]),
        (monthly, lambda d: (d.year, d.month)),
    )
    for count, period_of in periods:
        if not count:
            continue
        seen = set()
        for backup_id, ts in ordered:
            period = period_of(_parse(ts))
            if period in seen:
                continue
            seen.add(period)
            keep.add(backup_id)
            if len(seen) >= count:
                break
    return keep


def policy_of(row):
    """The policy stored on a backup_jobs or backup_group_jobs row."""
    return {
        "last": row["retention_count"],
        "daily": row["garder_jours"],
        "weekly": row["garder_semaines"],
        "monthly": row["garder_mois"],
    }


def describe(policy):
    parts = [f"{policy['last']} last"]
    for key, label in (("daily", "daily"), ("weekly", "weekly"), ("monthly", "monthly")):
        if policy.get(key):
            parts.append(f"{policy[key]} {label}")
    return ", ".join(parts)
