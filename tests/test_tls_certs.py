"""The web interface's HTTPS certificate: import (checked like uvicorn loads it), go back, Let's Encrypt command."""

import datetime

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.core import tls_certs


def _pem_pair(cn="hv1.example.org", days=90, start_days=-1, issuer_key=None, issuer_cn=None):
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_cn)]) if issuer_cn else subject
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now + datetime.timedelta(days=start_days))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(cn)]), critical=False)
        .sign(issuer_key or key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode(), key_pem.decode()


@pytest.fixture()
def tls_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tls_certs, "TLS_DIR", tmp_path / "tls")
    return tmp_path / "tls"


@pytest.fixture()
def restarts(monkeypatch):
    from app.routers import certificate

    calls = []
    monkeypatch.setattr(certificate, "_restart_soon", lambda: calls.append(True))
    return calls


def test_import_installs_the_pair_keeps_the_previous_and_restarts(client, auth_headers, tls_dir, restarts):
    old_cert, old_key = _pem_pair("old.example.org")
    tls_certs.install(old_cert.encode(), old_key.encode(), "auto")
    chain, _ = _pem_pair()
    ca_key = ec.generate_private_key(ec.SECP256R1())
    signed, signed_key = _pem_pair(issuer_key=ca_key, issuer_cn="Test CA")

    admin = auth_headers("root", "admin")
    r = client.post("/host/certificate", json={"certificat": signed, "cle": signed_key, "chaine": chain}, headers=admin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["sujet"], body["noms"], body["auto_signe"], body["source"], body["precedent"]) == (
        "hv1.example.org",
        ["hv1.example.org"],
        False,
        "import",
        True,
    )
    assert body["emetteur"] == "CN=Test CA" and 88 <= body["jours_restants"] <= 90
    assert restarts == [True]
    assert (tls_dir / "hyperlite.key").stat().st_mode & 0o777 == 0o600
    assert (tls_dir / "hyperlite.crt").read_text().count("BEGIN CERTIFICATE") == 2

    r = client.post("/host/certificate/previous", headers=admin)
    assert r.json()["sujet"] == "old.example.org" and restarts == [True, True]
    assert client.get("/host/certificate", headers=auth_headers("watcher", "observateur")).status_code == 403


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (lambda: ("nope", _pem_pair()[1]), "not a PEM certificate"),
        (lambda: (_pem_pair()[0], "nope"), "not a PEM private key"),
        (lambda: (_pem_pair()[0], _pem_pair()[1]), "does not belong"),
        (lambda: _pem_pair(days=-1, start_days=-10), "expired"),
        (lambda: _pem_pair(start_days=5, days=30), "valid only from"),
    ],
)
def test_bad_pairs_are_refused_and_nothing_changes(client, auth_headers, tls_dir, restarts, make, message):
    cert, key = make()
    r = client.post("/host/certificate", json={"certificat": cert, "cle": key}, headers=auth_headers("root", "admin"))
    assert r.status_code == 422 and message in r.json()["detail"]
    assert restarts == [] and not (tls_dir / "hyperlite.crt").exists()


def test_encrypted_key_is_refused(tls_dir):
    cert, _ = _pem_pair()
    key = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.BestAvailableEncryption(b"pw")
    )
    with pytest.raises(tls_certs.CertError, match="passphrase"):
        tls_certs.import_pem(cert, key.decode())


def test_nothing_to_restore_without_a_previous_pair(client, auth_headers, tls_dir, restarts):
    admin = auth_headers("root", "admin")
    r = client.post("/host/certificate/previous", headers=admin)
    assert r.status_code == 422 and restarts == []
    assert client.get("/host/certificate", headers=admin).json()["present"] is False


def test_acme_command_is_validated_and_uses_the_deploy_hook(monkeypatch):
    monkeypatch.setattr(tls_certs.shutil, "which", lambda name: f"/usr/bin/{name}")
    cmd = tls_certs.acme_command("hv1.example.org", "ops@example.org", staging=True)
    assert cmd[:3] == ["/usr/bin/certbot", "certonly", "--standalone"]
    assert "--domains=hv1.example.org" in cmd and cmd[-1] == "--test-cert"
    assert cmd[cmd.index("--deploy-hook") + 1].endswith("scripts/acme-deploy-hook.sh")
    for domain, email in [
        ("hv1", "a@b.org"),
        ("-x.example.org", "a@b.org"),
        ("a.org; rm -rf /", "a@b.org"),
        ("a.org", "nope"),
    ]:
        with pytest.raises(tls_certs.CertError):
            tls_certs.acme_command(domain, email)
    monkeypatch.setattr(tls_certs.shutil, "which", lambda name: None)
    with pytest.raises(tls_certs.CertError, match="apt install certbot"):
        tls_certs.acme_command("hv1.example.org", "ops@example.org")


def test_acme_failure_returns_certbot_last_lines(client, auth_headers, monkeypatch, tls_dir):
    import subprocess

    from app.routers import certificate

    monkeypatch.setattr(tls_certs.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        certificate.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 1, "", "Challenge failed\nTimeout during connect (likely firewall problem)"
        ),
    )
    r = client.post(
        "/host/certificate/acme",
        json={"domaine": "hv1.example.org", "email": "ops@example.org"},
        headers=auth_headers("root", "admin"),
    )
    assert r.status_code == 502 and "likely firewall problem" in r.json()["detail"]
