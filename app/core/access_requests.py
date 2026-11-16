"""On-demand rights (docs/design/access-2.md, section 6).

An administrator or a delegate makes an account (or a group) *eligible* for a role on a perimeter, for at most some
minutes, granted at once or after an approval. The account then asks, with a reason (and its second factor when it
has one); the right it gets is an ordinary assignment with an end, which grants nothing once past and is removed by
the sweep below. Every step is journaled and notified ("access_request").
"""

import logging
import threading
import uuid
from datetime import UTC, datetime, timedelta

from app.core import permissions as perm
from app.core.audit import log_action
from app.core.database import get_conn

logger = logging.getLogger(__name__)

SWEEP_S = 60


class RequestError(ValueError):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _now():
    return datetime.now(UTC)


def _row(request_id):
    with get_conn() as db:
        row = db.execute("SELECT * FROM access_requests WHERE id = ?", (request_id,)).fetchone()
    if row is None:
        raise RequestError(404, f"Request {request_id} not found")
    return dict(row)


def _update(request_id, **fields):
    names = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as db:
        db.execute(f"UPDATE access_requests SET {names} WHERE id = ?", (*fields.values(), request_id))  # noqa: S608
        db.commit()


def _say(user, request, what):
    where = (
        "the cluster"
        if request["resource_type"] == "cluster"
        else f"{request['resource_type']} {request['resource_id']}"
    )
    log_action(
        user,
        "access_request",
        request["username"],
        "success",
        f"{what}: {request['role']} on {where} for {request['minutes']} min ({request['reason']})",
    )


def listing(user):
    """The requests `user` made, and those it may decide on (an administrator: all), newest first."""
    with get_conn() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM access_requests ORDER BY created_at DESC LIMIT 500")]
    return [
        {**r, "may_decide": _may_decide(user, r) is None}
        for r in rows
        if r["username"] == user["username"] or _may_decide(user, r) is None
    ]


def _may_decide(user, request):
    return perm.grant_refusal(
        user, "user", request["username"], request["role"], request["resource_type"], request["resource_id"]
    )


def ask(user, eligibility_id, minutes, reason, code=None, password=None):
    """A request from `user` under one of its eligibilities; granted at once when the eligibility says so."""

    eligibility = next((e for e in perm.list_eligibility() if e["id"] == eligibility_id), None)
    if eligibility is None or eligibility not in perm.eligible_for(
        user, eligibility["role"], eligibility["resource_type"], eligibility["resource_id"]
    ):
        raise RequestError(404, "You are not eligible for this right")
    if minutes > eligibility["max_minutes"]:
        raise RequestError(422, f"At most {eligibility['max_minutes']} minutes for this right")
    if not reason.strip():
        raise RequestError(422, "Say why you need it")
    _step_up(user, code, password)
    request = {
        "id": uuid.uuid4().hex,
        "username": user["username"],
        "eligibility_id": eligibility_id,
        "role": eligibility["role"],
        "resource_type": eligibility["resource_type"],
        "resource_id": eligibility["resource_id"],
        "minutes": minutes,
        "reason": reason.strip(),
        "status": "pending",
        "created_at": _now().isoformat(),
    }
    with get_conn() as db:
        db.execute(
            f"INSERT INTO access_requests ({', '.join(request)}) VALUES ({', '.join('?' for _ in request)})",  # noqa: S608
            tuple(request.values()),
        )
        db.commit()
    if eligibility["approval"] == "auto":
        return _grant(request, user["username"])
    _say(user["username"], request, "asked for")
    return _row(request["id"])


STEP_UP_MAX_FAILURES = 5
STEP_UP_WINDOW_S = 300


def _step_up(user, code, password):
    """Taking a right asks the person again: the code of their TOTP, or their password for an account without one (a
    session alone is not enough, a security key cannot sign here). Repeated failures lock it for a while, as every
    re-authentication does. An SSO account without TOTP is its identity provider's to check."""
    from app.core import login_guard
    from app.core.security import verify_password
    from app.services import account_service

    name = user["username"]
    if login_guard.recent_failures("reauth", name, STEP_UP_WINDOW_S) >= STEP_UP_MAX_FAILURES:
        raise RequestError(429, f"Too many failed attempts, try again in {STEP_UP_WINDOW_S // 60} minutes")
    account = account_service.get(name) or {}
    if account.get("totp_enabled"):
        if not code:
            raise RequestError(422, "Enter the code of your second factor")
        ok = account_service.check_totp(name, code)
    elif account.get("auth_source") == "sso":
        return
    elif account.get("auth_source") == "ldap":
        from app.core import ldap_auth

        if not password:
            raise RequestError(422, "Enter your password")
        try:
            ok = ldap_auth.authenticate(name, password) is not None
        except (ldap_auth.LdapError, ldap_auth.LocalAccountConflict) as e:
            raise RequestError(502, f"The directory could not check the password: {e}") from None
    else:
        if not password:
            raise RequestError(422, "Enter your password")
        ok = verify_password(password, account.get("hashed_password") or "")
    if not ok:
        login_guard.record_failure("reauth", name)
        raise RequestError(403, "Wrong code" if account.get("totp_enabled") else "Wrong password")
    login_guard.clear_failures("reauth", name)


def _grant(request, by):
    expires_at = (_now() + timedelta(minutes=request["minutes"])).isoformat()
    acl_id = perm.create_dated_acl(
        "user", request["username"], request["role"], request["resource_type"], request["resource_id"], expires_at,
        None, request["reason"], by, "request",
    )  # fmt: skip
    _update(
        request["id"],
        status="granted",
        acl_id=acl_id,
        expires_at=expires_at,
        decided_by=by,
        decided_at=_now().isoformat(),
    )
    _say(by, request, "granted" if by != request["username"] else "taken")
    return _row(request["id"])


def decide(user, request_id, approve):
    request = _row(request_id)
    if request["status"] != "pending":
        raise RequestError(409, f"This request is {request['status']}")
    refusal = _may_decide(user, request)
    if refusal:
        raise RequestError(403, refusal)
    if approve:
        # What made the request valid is read again at the decision: the account still there and not ended, still
        # covered by the eligibility it asked under.
        from app.core import account_expiry
        from app.services import account_service

        account = account_service.get(request["username"])
        eligibility = next((e for e in perm.list_eligibility() if e["id"] == request["eligibility_id"]), None)
        if (
            account is None
            or account_expiry.expired(request["username"])
            or eligibility is None
            or eligibility
            not in perm.eligible_for(
                account, eligibility["role"], eligibility["resource_type"], eligibility["resource_id"]
            )
        ):
            _update(request_id, status="refused", decided_by=user["username"], decided_at=_now().isoformat())
            raise RequestError(409, "The account is no longer eligible for this right: the request is closed")
        return _grant(request, user["username"])
    _update(request_id, status="refused", decided_by=user["username"], decided_at=_now().isoformat())
    _say(user["username"], request, "refused")
    return _row(request_id)


def cancel(user, request_id):
    """The account withdraws its pending request, or gives back a right it holds before its end."""
    request = _row(request_id)
    if request["username"] != user["username"] and _may_decide(user, request) is not None:
        raise RequestError(403, "Only the account that asked, or one that may decide, may do this")
    if request["status"] == "pending":
        _update(request_id, status="cancelled", decided_at=_now().isoformat())
        _say(user["username"], request, "withdrawn")
    elif request["status"] == "granted":
        if request["acl_id"]:
            perm.delete_acl(request["acl_id"])
        _update(request_id, status="ended", decided_at=_now().isoformat())
        _say(user["username"], request, "ended early")
    else:
        raise RequestError(409, f"This request is {request['status']}")
    return _row(request_id)


def sweep():
    """Remove the assignments whose end passed (dated rights and on-demand ones alike), journal each, and close the
    requests they came from."""
    for row in perm.sweep_ended():
        where = "the cluster" if row["resource_type"] == "cluster" else f"{row['resource_type']} {row['resource_id']}"
        log_action("system", "access_request", row["subject_id"], "success", f"ended: {row['role']} on {where}")
        with get_conn() as db:
            db.execute(
                "UPDATE access_requests SET status = 'ended' WHERE acl_id = ? AND status = 'granted'", (row["id"],)
            )
            db.commit()


def _loop():
    stop = threading.Event()
    try:
        from app.core import workbench

        workbench.adopt()  # at start: the Terminal boxes still running are kept, the others ended
    except Exception:
        logger.warning("Terminal sessions not re-adopted", exc_info=True)
    while not stop.wait(SWEEP_S):
        try:
            sweep()
        except Exception:  # the next round tries again; an ended right grants nothing meanwhile (permissions._in_force)
            logger.warning("Ended rights not swept", exc_info=True)
        try:
            from app.core import access_review

            access_review.tick()
        except Exception:  # a round starts or closes at the next minute
            logger.warning("Scheduled access reviews not run", exc_info=True)
        try:
            from app.core import vm_identity

            vm_identity.renew_due()
        except Exception:  # the next minute tries again; a token still has a week to run
            logger.warning("VM identities not renewed", exc_info=True)
        for name, job in (("guest updates", "guest_updates.tick"), ("cost budgets", "costs.check_budgets_if_due"),
                          ("terminal sessions", "workbench.sweep"), ("patch plans", "patch_plans.tick"),
                          ("VM group autoscaling", "vm_group_scaling.tick"),
                          ("guest scripts", "guest_scripts.tick")):  # fmt: skip
            try:
                module, fn = job.split(".")
                getattr(__import__(f"app.core.{module}", fromlist=[fn]), fn)()
            except Exception:  # each minute tries again
                logger.warning("%s not run", name.capitalize(), exc_info=True)


def start_access_sweep():
    threading.Thread(target=_loop, daemon=True, name="access-sweep").start()


def forget_account(username):
    """A deleted account's eligibilities go, and its requests close (its dated rights went with its assignments): none
    is handed to the next account created under that name."""
    for e in perm.list_eligibility():
        if e["subject_type"] == "user" and e["subject_id"] == username:
            perm.delete_eligibility(e["id"])
    with get_conn() as db:
        db.execute(
            "UPDATE access_requests SET status = 'ended' WHERE username = ? AND status IN ('pending', 'granted')",
            (username,),
        )
        db.commit()
