"""The secrets vault and VM identities (docs/design/governance.md, sections 8 and 9)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core import api_tokens, permissions, privileged, vault, vm_identity


def test_vault(client, auth_headers):
    admin = auth_headers("root", "admin")
    bob = auth_headers("bob", "viewer")
    made = client.post("/vault", json={"name": "db-root", "value": "s3cret", "readers": ["user:bob"]}, headers=admin)
    assert made.status_code == 201 and "value" not in made.json()
    with vault.get_conn() as db:
        stored = db.execute("SELECT value FROM vault_secrets").fetchone()["value"]
    assert stored != "s3cret"  # sealed
    assert [s["name"] for s in client.get("/vault", headers=bob).json()["items"]] == ["db-root"]
    assert client.post("/vault/reveal/db-root", headers=bob).json()["value"] == "s3cret"
    eve = auth_headers("eve", "viewer")
    assert client.get("/vault", headers=eve).json()["items"] == []
    assert client.post("/vault/reveal/db-root", headers=eve).status_code == 403
    assert client.post("/vault", json={"name": "db-root", "value": "x"}, headers=admin).status_code == 409
    assert client.post("/vault", json={"name": "bad name", "value": "x"}, headers=admin).status_code == 422
    assert client.post("/vault", json={"name": "x", "value": "y"}, headers=bob).status_code == 403
    sid = made.json()["id"]
    assert client.put(f"/vault/{sid}", json={"name": "db-root", "readers": []}, headers=admin).status_code == 200
    assert client.post("/vault/reveal/db-root", headers=bob).status_code == 403
    assert client.post("/vault/reveal/db-root", headers=admin).json()["value"] == "s3cret"  # the value kept


def test_vault_reference(database):
    vault.put("vm-pass", "hunter2", "", "root")
    root = {"username": "root", "role": "admin"}
    assert vault.resolve(root, "vault:vm-pass") == "hunter2"
    assert vault.resolve(root, "plain") == "plain"
    with pytest.raises(vault.VaultError):
        vault.resolve({"username": "bob", "role": "viewer"}, "vault:vm-pass")


@pytest.fixture()
def agent(monkeypatch):
    scripts = []

    def run(op, user=None, timeout=None, **args):
        assert op == "vm.agent_exec"
        scripts.append(args["script"])
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(privileged, "run", run)
    return scripts


def test_vm_identity(database, make_user, agent):
    make_user("root", "admin")
    permissions.create_pool("ci")
    found = vm_identity.enable("root", "runner-1", "operator", "pool", "1", "sh")
    assert found["delivered_at"] and found["last_error"] is None
    token = agent[0].split("'")[3]
    user = api_tokens.verify_token(token)
    assert user["role"] == "delegated" and user["token_scope"]["role"] == "operator"
    # Renewed a week before its end: a new token, the old one revoked.
    vm_identity.renew_due(datetime.now(UTC) + timedelta(days=25))
    assert len(agent) == 2
    assert api_tokens.verify_token(token) is None
    assert api_tokens.verify_token(agent[1].split("'")[3]) is not None
    assert vm_identity.disable("runner-1") and vm_identity.of("runner-1") is None
    assert api_tokens.verify_token(agent[1].split("'")[3]) is None


def test_identity_waits_for_an_agent(database, make_user, monkeypatch):
    from app.daemon.registry import DaemonError

    make_user("root", "admin")

    def no_agent(*a, **k):
        raise DaemonError("failed", "The guest agent does not answer")

    monkeypatch.setattr(privileged, "run", no_agent)
    found = vm_identity.enable("root", "vm-x", "drive", "cluster", "*", "sh")
    assert found["delivered_at"] is None and "agent" in found["last_error"]
