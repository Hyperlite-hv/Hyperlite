"""SSO configuration persistence."""

import pytest

from app.core import sso


def test_config_round_trip_encrypts_the_client_secret(database):
    sso.set_config(enabled=1, issuer="https://idp.example.test", client_id="hyperlite", client_secret="s3cret")
    with database.get_conn() as conn:
        stored = conn.execute("SELECT client_secret FROM sso_config WHERE id = 1").fetchone()["client_secret"]
    assert stored != "s3cret"  # never stored in clear text
    assert sso.get_config()["client_secret"] == "s3cret"


def test_partial_update_keeps_other_fields(database):
    sso.set_config(enabled=1, issuer="https://idp.example.test", client_id="hyperlite")
    sso.set_config(client_id="renamed")
    config = sso.get_config()
    assert config["issuer"] == "https://idp.example.test"
    assert config["client_id"] == "renamed"


def test_unknown_column_names_are_rejected_before_reaching_sql(database):
    with pytest.raises(ValueError):
        sso.set_config(**{"issuer = 'x', enabled": "1"})
    with pytest.raises(ValueError):
        sso.set_config(unknown_field="value")
