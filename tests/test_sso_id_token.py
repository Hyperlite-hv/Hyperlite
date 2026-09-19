"""OpenID Connect ID token validation (signature, claims, algorithm policy)."""

import base64
import hashlib
import hmac
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core import sso

ISSUER = "https://idp.example.test"
CLIENT_ID = "hyperlite-client"
KID = "test-key-1"
NONCE = "expected-nonce"
CONFIG = {"issuer": ISSUER, "client_id": CLIENT_ID}
DISCOVERY = {"jwks_uri": f"{ISSUER}/jwks"}


@pytest.fixture(scope="module")
def private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture()
def jwks(private_key, monkeypatch):
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    document = {"keys": [jwk]}
    monkeypatch.setattr(sso, "_http_get_json", lambda url: document)
    return document


def _claims(**overrides):
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "user-1",
        "exp": int(time.time()) + 300,
        "iat": int(time.time()),
        "nonce": NONCE,
        "groups": ["ops"],
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def _sign(private_key, claims, kid=KID):
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


def test_valid_token_is_accepted(private_key, jwks):
    claims = sso.validate_id_token(CONFIG, DISCOVERY, _sign(private_key, _claims()), NONCE)
    assert claims["sub"] == "user-1"


def test_wrong_audience_is_rejected(private_key, jwks):
    token = _sign(private_key, _claims(aud="another-client"))
    with pytest.raises(jwt.PyJWTError):
        sso.validate_id_token(CONFIG, DISCOVERY, token, NONCE)


def test_wrong_issuer_is_rejected(private_key, jwks):
    token = _sign(private_key, _claims(iss="https://evil.example.test"))
    with pytest.raises(jwt.PyJWTError):
        sso.validate_id_token(CONFIG, DISCOVERY, token, NONCE)


def test_expired_token_is_rejected(private_key, jwks):
    token = _sign(private_key, _claims(exp=int(time.time()) - 3600))
    with pytest.raises(jwt.PyJWTError):
        sso.validate_id_token(CONFIG, DISCOVERY, token, NONCE)


def test_token_without_expiry_is_rejected(private_key, jwks):
    token = _sign(private_key, _claims(exp=None))
    with pytest.raises(jwt.PyJWTError):
        sso.validate_id_token(CONFIG, DISCOVERY, token, NONCE)


def test_wrong_nonce_is_rejected(private_key, jwks):
    token = _sign(private_key, _claims(nonce="replayed-nonce"))
    with pytest.raises(jwt.PyJWTError):
        sso.validate_id_token(CONFIG, DISCOVERY, token, NONCE)


def test_unknown_key_id_is_rejected(private_key, jwks):
    token = _sign(private_key, _claims(), kid="unknown-kid")
    with pytest.raises(jwt.PyJWTError):
        sso.validate_id_token(CONFIG, DISCOVERY, token, NONCE)


def test_token_signed_by_another_key_is_rejected(jwks):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(jwt.PyJWTError):
        sso.validate_id_token(CONFIG, DISCOVERY, _sign(other, _claims()), NONCE)


def test_hmac_algorithm_confusion_attack_is_rejected(private_key, jwks):
    """Forge an HS256 token using the IdP public key as the HMAC secret."""
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )

    def b64(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": KID}).encode())
    payload = b64(json.dumps(_claims()).encode())
    signature = b64(hmac.new(public_pem, f"{header}.{payload}".encode(), hashlib.sha256).digest())
    with pytest.raises(jwt.PyJWTError):
        sso.validate_id_token(CONFIG, DISCOVERY, f"{header}.{payload}.{signature}", NONCE)


def test_unsigned_token_is_rejected(jwks):
    def b64(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    token = f"{b64({'alg': 'none', 'typ': 'JWT', 'kid': KID})}.{b64(_claims())}."
    with pytest.raises(jwt.PyJWTError):
        sso.validate_id_token(CONFIG, DISCOVERY, token, NONCE)
