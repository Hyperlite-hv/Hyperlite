"""Accounts as the API manages them: the user list, creation, role and password changes, deletion, TOTP enrolment
and the sign-in record. Password checks, hashing and sessions stay in app/core/security.py."""

from app.domain.common import AlreadyExists
from app.repositories import registry


class UserExists(ValueError):
    pass


def _store():
    return registry.accounts().sync


async def list_users():
    return await registry.accounts().list_summary()


def get(username):
    return _store().get(username)


def create(username, hashed_password, role):
    try:
        _store().create(username, hashed_password, role)
    except AlreadyExists:
        raise UserExists(f"User '{username}' already exists") from None


def set_role(username, role):
    _store().update(username, role=role)


def other_admins(username):
    return _store().other_admins(username)


def delete(username):
    _store().delete(username)


def start_totp(username):
    """A new second factor's secret, made, sealed and stored by hyperlite-daemon (it holds the node's keys): the
    secret, shown once for the QR code."""
    from app.core import privileged

    return privileged.run("access.totp_start", user=username, username=username)["secret"]


def check_totp(username, code):
    """Whether `code` is the account's current one, checked by hyperlite-daemon; each code is accepted once."""
    from app.core import privileged

    return privileged.run("access.totp_check", user=username, username=username, code=code)["valid"]


def enable_totp(username):
    _store().enable_totp(username)


def disable_totp(username):
    _store().disable_totp(username)
