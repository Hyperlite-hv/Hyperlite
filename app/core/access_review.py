"""The access review (docs/design/access-2.md, section 8): what an administrator goes through now and then to keep
the rights given to what is still needed.

Each assignment comes with what helps decide on it: its account's last sign-in, its end, its reason, whether it gives
access to others or the whole cluster, whether its account or group still exists, and when someone last kept it. The
accounts and API tokens that look forgotten come with it.
"""

from datetime import UTC, datetime, timedelta

from app.core import permissions as perm
from app.core.database import get_conn

STALE_DAYS = 90
ENDING_DAYS = 7
REVIEW_VALID_DAYS = 90


def _age_days(iso, now):
    if not iso:
        return None
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    return (now - then).days


def _reviews():
    with get_conn() as db:
        return {r["acl_id"]: dict(r) for r in db.execute("SELECT * FROM access_reviews")}


def keep(acl_id, note, username):
    with get_conn() as db:
        db.execute(
            "INSERT INTO access_reviews (acl_id, note, reviewed_by, reviewed_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(acl_id) DO UPDATE SET note = excluded.note, reviewed_by = excluded.reviewed_by, "
            "reviewed_at = excluded.reviewed_at",
            (str(acl_id), note, username, datetime.now(UTC).isoformat()),
        )
        db.commit()


def review():
    from app.core.api_tokens import list_tokens
    from app.services import account_service

    now = datetime.now(UTC)
    accounts = {u["username"]: u for u in account_service._store().list_summary()}
    groups = {str(g["id"]): g for g in perm.list_groups()}
    reviews = _reviews()
    rows = []
    for a in perm.list_acl():
        flags = []
        account = accounts.get(a["subject_id"]) if a["subject_type"] == "user" else None
        if (a["subject_type"] == "user" and account is None) or (
            a["subject_type"] == "group" and a["subject_id"] not in groups
        ):
            flags.append("orphan")
        last_login = account["last_login_at"] if account else None
        idle = _age_days(last_login, now)
        if account is not None and (idle is None or idle >= STALE_DAYS):
            flags.append("stale_account")
        privileges = perm.get_role_privileges(a["role"])
        if not privileges:
            flags.append("unknown_role")
        if "access.manage" in privileges:
            flags.append("gives_access")
        if a["resource_type"] == "cluster":
            flags.append("whole_cluster")
        if a.get("expires_at"):
            left = -(_age_days(a["expires_at"], now) or 0)
            if left <= ENDING_DAYS:
                flags.append("ending_soon")
        reviewed = reviews.get(str(a["id"]))
        if not reviewed or (_age_days(reviewed["reviewed_at"], now) or 0) >= REVIEW_VALID_DAYS:
            flags.append("to_review")
        rows.append({**a, "last_login_at": last_login, "flags": flags, "review": reviewed})
    stale_accounts = [
        {
            "username": u["username"],
            "role": u["role"],
            "last_login_at": u["last_login_at"],
            "totp_enabled": u["totp_enabled"],
        }
        for u in accounts.values()
        if (_age_days(u["last_login_at"], now) is None or _age_days(u["last_login_at"], now) >= STALE_DAYS)
    ]
    admins_without_2fa = [u["username"] for u in accounts.values() if u["role"] == "admin" and not u["totp_enabled"]]
    tokens = []
    for name in accounts:
        for tok in list_tokens(name):
            unused = _age_days(tok["last_used_at"] or tok["created_at"], now)
            if tok["expires_at"] is None or (unused is not None and unused >= STALE_DAYS):
                tokens.append({**tok, "username": name, "unused_days": unused})
    soon = (now + timedelta(days=ENDING_DAYS)).isoformat()
    return {
        "assignments": rows,
        "stale_accounts": stale_accounts,
        "admins_without_2fa": admins_without_2fa,
        "tokens": tokens,
        "summary": {
            "assignments": len(rows),
            "to_review": sum("to_review" in r["flags"] for r in rows),
            "orphans": sum("orphan" in r["flags"] for r in rows),
            "ending_soon": sum(1 for r in rows if r.get("expires_at") and r["expires_at"] <= soon),
            "stale_accounts": len(stale_accounts),
            "tokens": len(tokens),
        },
        "stale_days": STALE_DAYS,
    }


# ---- Scheduled reviews (docs/design/governance.md, section 5) ----


def campaigns():
    import json

    with get_conn() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM review_campaigns ORDER BY name")]
    for c in rows:
        c["reviewers"] = json.loads(c["reviewers"] or "[]")
        c["remove_undecided"] = bool(c["remove_undecided"])
    return rows


def save_campaign(fields, username, campaign_id=None):
    import json
    import uuid

    now = datetime.now(UTC)
    values = (fields["name"], fields["every_days"], fields["deadline_days"], json.dumps(fields["reviewers"]),
              1 if fields["remove_undecided"] else 0)  # fmt: skip
    with get_conn() as db:
        if campaign_id:
            cur = db.execute(
                "UPDATE review_campaigns SET name = ?, every_days = ?, deadline_days = ?, reviewers = ?, "
                "remove_undecided = ? WHERE id = ?",
                (*values, campaign_id),
            )
            if cur.rowcount == 0:
                return None
        else:
            campaign_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO review_campaigns (name, every_days, deadline_days, reviewers, remove_undecided, id, "
                "next_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (*values, campaign_id, now.isoformat(), username),
            )
        db.commit()
    return next(c for c in campaigns() if c["id"] == campaign_id)


def delete_campaign(campaign_id):
    with get_conn() as db:
        cur = db.execute("DELETE FROM review_campaigns WHERE id = ?", (campaign_id,))
        db.execute("DELETE FROM review_items WHERE campaign_id = ?", (campaign_id,))
        db.commit()
    return cur.rowcount > 0


def items(campaign_id):
    with get_conn() as db:
        c = db.execute("SELECT round_started_at FROM review_campaigns WHERE id = ?", (campaign_id,)).fetchone()
        if c is None or not c["round_started_at"]:
            return []
        return [
            dict(r)
            for r in db.execute(
                "SELECT * FROM review_items WHERE campaign_id = ? AND round_started_at = ? ORDER BY summary",
                (campaign_id, c["round_started_at"]),
            )
        ]


def decide(campaign_id, item_id, decision, username):
    """Keep or remove one assignment of the round; a removal removes it now."""
    with get_conn() as db:
        row = db.execute(
            "SELECT * FROM review_items WHERE id = ? AND campaign_id = ?", (item_id, campaign_id)
        ).fetchone()
    if row is None:
        return None
    if decision == "remove":
        perm.delete_acl(row["acl_id"])
    else:
        keep(row["acl_id"], f"kept in the review {campaign_id[:8]}", username)
    with get_conn() as db:
        db.execute(
            "UPDATE review_items SET decision = ?, decided_by = ?, decided_at = ? WHERE id = ?",
            (decision, username, datetime.now(UTC).isoformat(), item_id),
        )
        db.commit()
    return dict(row, decision=decision, decided_by=username)


def _start_round(c, now):
    import uuid

    from app.core.audit import log_action

    found = review()["assignments"]
    started = now.isoformat()
    with get_conn() as db:
        for a in found:
            if "to_review" not in a["flags"]:
                continue
            where = "the cluster" if a["resource_type"] == "cluster" else f"{a['resource_type']} {a['resource_id']}"
            summary = f"{a['subject_type']} {a['subject_label']}: {a['role']} on {where}"
            db.execute(
                "INSERT INTO review_items (id, campaign_id, round_started_at, acl_id, summary) VALUES (?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, c["id"], started, a["id"], summary),
            )
        db.execute(
            "UPDATE review_campaigns SET round_started_at = ?, round_due_at = ?, next_at = ? WHERE id = ?",
            (
                started,
                (now + timedelta(days=c["deadline_days"])).isoformat(),
                (now + timedelta(days=c["every_days"])).isoformat(),
                c["id"],
            ),
        )
        db.commit()
    log_action("system", "access_review", c["name"], "success",
               f"review started, due in {c['deadline_days']} days; reviewers: {', '.join(c['reviewers']) or 'administrators'}")  # fmt: skip


def _close_round(c):
    from app.core.audit import log_action

    undecided = [i for i in items(c["id"]) if i["decision"] is None]
    removed = 0
    for i in undecided:
        if c["remove_undecided"] and i["acl_id"] in {a["id"] for a in perm.list_acl()}:
            perm.delete_acl(i["acl_id"])
            removed += 1
        with get_conn() as db:
            db.execute(
                "UPDATE review_items SET decision = ?, decided_by = 'system', decided_at = ? WHERE id = ?",
                ("removed_undecided" if c["remove_undecided"] else None, datetime.now(UTC).isoformat(), i["id"]),
            )
            db.commit()
    with get_conn() as db:
        db.execute("UPDATE review_campaigns SET round_due_at = NULL WHERE id = ?", (c["id"],))
        db.commit()
    log_action("system", "access_review", c["name"], "success",
               f"review closed: {len(undecided)} undecided, {removed} removed")  # fmt: skip


def tick(now=None):
    """Start the rounds that are due and close those past their deadline (hyperlite-scheduler, every minute)."""
    now = now or datetime.now(UTC)
    for c in campaigns():
        if c["round_due_at"] and datetime.fromisoformat(c["round_due_at"]) <= now:
            _close_round(c)
            c["round_due_at"] = None
        if datetime.fromisoformat(c["next_at"]) <= now and not c["round_due_at"]:
            _start_round(c, now)
