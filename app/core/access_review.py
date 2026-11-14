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
