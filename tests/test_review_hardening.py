"""Small hardenings found by the code review with ECC's agents: a BMC password cut in an agent's output, a group
claim sent as a string, an IdP error shown to anyone, a ZFS range in a snapshot name."""

import importlib

import pytest

from app.core import ha_fencing, secrets_crypto, sso

snapshots = importlib.import_module("app.routers.vms.snapshots")


def test_a_password_the_agent_echoed_is_hidden_even_where_the_output_is_cut(monkeypatch):
    secret = "Sup3r-Secret-BMC-pw"
    monkeypatch.setattr(secrets_crypto, "decrypt", lambda value: secret)
    out = "x" * 290 + secret + "y" * 5  # the cut at 300 characters from the end falls inside the password
    message = ha_fencing._without_secret({"secret": "sealed"}, out)
    assert "3r-Secret" not in message and "Secret-BMC-pw" not in message


@pytest.mark.parametrize(
    ("claim", "role"), [("admins", "admin"), (["admins"], "admin"), ("a", "viewer"), (None, "viewer")]
)
def test_a_group_claim_sent_as_a_string_is_one_group(claim, role):
    config = {"admin_groups": "admins", "group_claim": "groups"}
    assert sso.resolve_role(config, {"groups": claim}) == role
    assert sso.resolve_role({"admin_groups": "a", "group_claim": "groups"}, {"groups": "admins"}) == "viewer"


@pytest.mark.parametrize("name", ["%zz", "a,b", "pre@x", "a/b", " x"])
def test_a_snapshot_name_with_zfs_syntax_is_refused(name):
    from app.core import vm_snapshots

    with pytest.raises(vm_snapshots.SnapshotError) as err:
        vm_snapshots.check_existing_name(name)
    assert err.value.kind == "refused"  # 422
    vm_snapshots.check_existing_name("before-update-2026")


def test_an_unreachable_identity_provider_is_said_without_its_error(root_client, database, monkeypatch):
    from app.core import secrets_crypto

    sso.set_config(
        enabled=True,
        issuer="https://idp.internal.example:8443/realms/x",
        client_id="hl",
        client_secret="s",
        redirect_uri="https://hv/api/v1/auth/sso/callback",
    )
    monkeypatch.setattr(secrets_crypto, "decrypt", lambda value: "s")

    def unreachable(issuer):
        raise OSError("connect to 10.20.30.40:8443 refused")

    monkeypatch.setattr(sso, "discover", unreachable)
    r = root_client.get("/auth/sso/login", follow_redirects=False)
    assert r.status_code == 502 and "10.20.30.40" not in r.text
