"""Security keys (WebAuthn) as a second factor. A software authenticator (an EC P-256 key, "none" attestation)
answers the real WebAuthn challenges, so py_webauthn verifies genuine signatures: registration, sign-in, a wrong
signature, a replayed answer, another host name, and the places a browser refuses WebAuthn."""

import hashlib
import json
import os

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import bytes_to_base64url

from app.core import webauthn_keys

ORIGIN = "https://hyperlite.example.lan"
PASSWORD = "correct horse battery"


class SoftKey:
    """A minimal authenticator, enough for py_webauthn: packed-free "none" attestation, ES256 signatures."""

    def __init__(self):
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.counter = 0

    def _client_data(self, kind, challenge, origin):
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin, "crossOrigin": False}).encode()

    def create(self, options, origin=ORIGIN):
        numbers = self.private.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")})
        auth_data = (
            hashlib.sha256(options["rp"]["id"].encode()).digest()
            + bytes([0x41])  # user present, attested credential data
            + self.counter.to_bytes(4, "big")
            + bytes(16)  # AAGUID
            + len(self.credential_id).to_bytes(2, "big")
            + self.credential_id
            + cose
        )
        client_data = self._client_data("webauthn.create", options["challenge"], origin)
        return {
            "id": bytes_to_base64url(self.credential_id),
            "rawId": bytes_to_base64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "attestationObject": bytes_to_base64url(
                    cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
                ),
            },
        }

    def get(self, options, origin=ORIGIN, tamper=False):
        self.counter += 1
        auth_data = hashlib.sha256(options["rpId"].encode()).digest() + bytes([0x01]) + self.counter.to_bytes(4, "big")
        client_data = self._client_data("webauthn.get", options["challenge"], origin)
        signature = self.private.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        if tamper:
            signature = signature[:-1] + bytes([signature[-1] ^ 1])
        return {
            "id": bytes_to_base64url(self.credential_id),
            "rawId": bytes_to_base64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "authenticatorData": bytes_to_base64url(auth_data),
                "signature": bytes_to_base64url(signature),
            },
        }


@pytest.fixture()
def alice(client, make_user):
    make_user("alice", role="admin", password=PASSWORD)
    r = client.post("/auth/login", data={"username": "alice", "password": PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}", "Origin": ORIGIN}


def register(client, headers, key, name="YubiKey"):
    options = client.post("/auth/webauthn/keys/options", headers=headers, json={"password": PASSWORD})
    assert options.status_code == 200, options.text
    return client.post(
        "/auth/webauthn/keys", json={"credential": key.create(options.json()), "name": name}, headers=headers
    )


def password_step(client):
    r = client.post("/auth/login", data={"username": "alice", "password": PASSWORD})
    assert r.status_code == 200
    return r.json()


def test_a_registered_key_becomes_the_second_factor_at_sign_in(client, alice):
    key = SoftKey()
    r = register(client, alice, key)
    assert r.status_code == 201, r.text
    assert [k["nom"] for k in r.json()["cles"]] == ["YubiKey"] and r.json()["cles"][0][
        "rp_id"
    ] == "hyperlite.example.lan"
    assert client.get("/auth/me", headers=alice).json()["cles_securite"] == 1

    step = password_step(client)
    assert step["require_2fa"] is True and step["methods"] == ["webauthn"] and "access_token" not in step
    options = client.post(
        "/auth/login/webauthn/options", json={"pre_auth_token": step["pre_auth_token"]}, headers={"Origin": ORIGIN}
    )
    assert options.status_code == 200
    assert [c["id"] for c in options.json()["allowCredentials"]] == [bytes_to_base64url(key.credential_id)]
    answer = key.get(options.json())
    r = client.post(
        "/auth/login/webauthn",
        json={"pre_auth_token": step["pre_auth_token"], "credential": answer},
        headers={"Origin": ORIGIN},
    )
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]
    assert client.get("/auth/webauthn/keys", headers=alice).json()[0]["utilise_le"]

    # The same answer again: its challenge was used up.
    replay = client.post(
        "/auth/login/webauthn",
        json={"pre_auth_token": step["pre_auth_token"], "credential": answer},
        headers={"Origin": ORIGIN},
    )
    assert replay.status_code == 401


def test_a_wrong_signature_is_refused_and_counts_as_a_failure(client, alice, database):
    key = SoftKey()
    register(client, alice, key)
    step = password_step(client)
    options = client.post(
        "/auth/login/webauthn/options", json={"pre_auth_token": step["pre_auth_token"]}, headers={"Origin": ORIGIN}
    ).json()
    r = client.post(
        "/auth/login/webauthn",
        json={"pre_auth_token": step["pre_auth_token"], "credential": key.get(options, tamper=True)},
        headers={"Origin": ORIGIN},
    )
    assert r.status_code == 401
    with database.get_conn() as db:
        assert (
            db.execute("SELECT COUNT(*) FROM login_failures WHERE kind = 'user' AND key = 'alice'").fetchone()[0] == 1
        )


def test_another_key_or_another_host_name_does_not_sign_in(client, alice):
    register(client, alice, SoftKey())
    step = password_step(client)
    # Another name for the same server: the key was registered for hyperlite.example.lan only.
    r = client.post(
        "/auth/login/webauthn/options",
        json={"pre_auth_token": step["pre_auth_token"]},
        headers={"Origin": "https://hl.other.lan"},
    )
    assert r.status_code == 400 and "registered for hl.other.lan" in r.json()["detail"]
    # A key that was never registered answers the right challenge.
    options = client.post(
        "/auth/login/webauthn/options", json={"pre_auth_token": step["pre_auth_token"]}, headers={"Origin": ORIGIN}
    ).json()
    r = client.post(
        "/auth/login/webauthn",
        json={"pre_auth_token": step["pre_auth_token"], "credential": SoftKey().get(options)},
        headers={"Origin": ORIGIN},
    )
    assert r.status_code == 401


@pytest.mark.parametrize(
    ("origin", "text"),
    [
        (None, "no Origin"),
        ("https://192.0.2.10", "IP address"),
        ("https://[2001:db8::1]:8443", "IP address"),
        ("http://hyperlite.example.lan", "HTTPS"),
        ("ftp://hyperlite.example.lan", "Unrecognised"),
    ],
)
def test_requests_a_browser_would_refuse_are_refused_with_the_reason(origin, text):
    with pytest.raises(webauthn_keys.WebAuthnError) as err:
        webauthn_keys.relying_party(origin)
    assert text in err.value.message


def test_localhost_over_http_is_allowed_like_browsers_do():
    assert webauthn_keys.relying_party("http://localhost:8011") == ("localhost", "http://localhost:8011")


def test_an_expired_or_foreign_challenge_is_refused(client, alice, database, monkeypatch):
    key = SoftKey()
    options = client.post("/auth/webauthn/keys/options", headers=alice, json={"password": PASSWORD}).json()
    with database.get_conn() as db:
        db.execute("UPDATE webauthn_challenges SET expire_le = '2000-01-01T00:00:00+00:00'")
        db.commit()
    r = client.post("/auth/webauthn/keys", json={"credential": key.create(options)}, headers=alice)
    assert r.status_code == 400 and "expired" in r.json()["detail"]
    # A challenge the server never issued.
    forged = dict(options, challenge=bytes_to_base64url(os.urandom(32)))
    r = client.post("/auth/webauthn/keys", json={"credential": key.create(forged)}, headers=alice)
    assert r.status_code == 400


def test_totp_and_keys_are_both_offered_and_removing_a_key_takes_the_password(client, alice, database):
    with database.get_conn() as db:
        db.execute("UPDATE users SET totp_secret = 'JBSWY3DPEHPK3PXP', totp_enabled = 1 WHERE username = 'alice'")
        db.commit()
    key_id = register(client, alice, SoftKey()).json()["id"]
    assert password_step(client)["methods"] == ["totp", "webauthn"]
    r = client.request("DELETE", f"/auth/webauthn/keys/{key_id}", json={"password": "wrong"}, headers=alice)
    assert r.status_code == 400 and len(client.get("/auth/webauthn/keys", headers=alice).json()) == 1
    r = client.request("DELETE", f"/auth/webauthn/keys/{key_id}", json={"password": PASSWORD}, headers=alice)
    assert r.status_code == 200 and r.json()["cles"] == []
    assert password_step(client)["methods"] == ["totp"]


def test_the_same_key_cannot_be_registered_twice_and_deleting_the_user_removes_its_keys(client, alice, auth_headers):
    key = SoftKey()
    register(client, alice, key)
    options = client.post("/auth/webauthn/keys/options", headers=alice, json={"password": PASSWORD}).json()
    assert [c["id"] for c in options["excludeCredentials"]] == [bytes_to_base64url(key.credential_id)]
    assert (
        client.post("/auth/webauthn/keys", json={"credential": key.create(options)}, headers=alice).status_code == 409
    )

    admin = auth_headers("root")
    assert client.delete("/auth/users/alice", headers=admin).status_code == 200
    assert webauthn_keys.list_keys("alice") == []
