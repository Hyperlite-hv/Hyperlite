"""Accounts as the API manages them: the user list, creation, role and password changes, deletion, TOTP enrolment
and the sign-in record. Password checks, hashing and sessions stay in app/core/security.py."""

from datetime import UTC, datetime

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


def record_login(username):
    _store().record_login(username, datetime.now(UTC).isoformat())


def start_totp(username, sealed_secret):
    _store().set_totp_secret(username, sealed_secret)


def enable_totp(username):
    _store().enable_totp(username)


def disable_totp(username):
    _store().disable_totp(username)
