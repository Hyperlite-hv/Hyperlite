"""Security headers on every response, API documentation off by default, and a /health that only tells a
signed-in caller which host and software versions it runs."""

import base64
import hashlib
from types import SimpleNamespace

import pytest

from app import main
from app.core import http_headers


class FakeConn:
    def getType(self):
        return "QEMU"

    def getHostname(self):
        return "hv-01"

    def getLibVersion(self):
        return 11003000

    def close(self):
        pass


@pytest.fixture()
def libvirt_up(monkeypatch):
    monkeypatch.setattr(main, "open_conn", lambda *a, **k: FakeConn())


def test_every_response_carries_the_security_headers(client):
    r = client.get("/auth/sso/status")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert "unsafe-eval" not in csp
    # Error responses are covered too.
    assert "Content-Security-Policy" in client.get("/vms").headers


def test_hsts_is_opt_in_and_https_only(monkeypatch):
    assert "Strict-Transport-Security" not in http_headers.security_headers("/", "https", "h", [])
    monkeypatch.setenv("HYPERLITE_HSTS_MAX_AGE", "31536000")
    assert http_headers.security_headers("/", "https", "h", [])["Strict-Transport-Security"] == "max-age=31536000"
    assert "Strict-Transport-Security" not in http_headers.security_headers("/", "http", "h", [])
    monkeypatch.setenv("HYPERLITE_HSTS_MAX_AGE", "soon")
    assert "Strict-Transport-Security" not in http_headers.security_headers("/", "https", "h", [])


def test_the_inline_scripts_of_index_html_are_allowed_by_hash(tmp_path):
    body = '\n      try { document.documentElement.classList.add("dark"); } catch (e) {}\n    '
    index = tmp_path / "index.html"
    index.write_text(f'<html><head><script>{body}</script><script type="module" src="/assets/x.js"></script></head>')
    expected = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
    assert http_headers.inline_script_hashes(index) == [f"'sha256-{expected}'"]
    assert http_headers.inline_script_hashes(tmp_path / "missing.html") == []
    # An end tag written with trailing whitespace is still an end tag: the script must still be found and hashed.
    spaced = tmp_path / "spaced.html"
    spaced.write_text(f"<script>{body}</script ><SCRIPT>b()</SCRIPT\n>")
    assert len(http_headers.inline_script_hashes(spaced)) == 2
    csp = http_headers.content_security_policy([f"'sha256-{expected}'"], "hv:8000")
    assert f"script-src 'self' 'sha256-{expected}'" in csp
    assert "connect-src 'self' wss://hv:8000 ws://hv:8000" in csp


def test_a_forged_host_header_cannot_inject_into_the_policy():
    csp = http_headers.content_security_policy([], "evil; script-src *")
    assert "evil" not in csp and "connect-src 'self';" in csp


def test_api_documentation_is_off_by_default(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        r = client.get(path)
        # The SPA catch-all answers instead: no API description is served.
        assert "openapi" not in r.text.lower(), path


def test_health_tells_an_anonymous_caller_nothing_about_the_host(client, libvirt_up):
    r = client.get("/health")
    assert r.status_code == 200
    assert set(r.json()) == {"status", "environment"}


def test_health_gives_the_details_to_a_signed_in_caller(client, auth_headers, libvirt_up):
    r = client.get("/health", headers=auth_headers("alice"))
    body = r.json()
    assert body["hostname"] == "hv-01"
    assert body["hyperlite_version"] and body["kernel"] and body["python_version"]


def test_health_ignores_an_invalid_token(client, libvirt_up):
    r = client.get("/health", headers={"Authorization": "Bearer not-a-token"})
    assert set(r.json()) == {"status", "environment"}


def test_health_gives_the_running_version_to_the_host_itself(libvirt_up):
    request = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))
    body = main.health(request)
    assert set(body) == {"status", "environment", "hyperlite_version"}


def test_an_unknown_api_path_is_a_json_404_and_a_page_is_the_dashboard(client, monkeypatch, tmp_path):
    """An API client calling a path that does not exist got the dashboard page with a 200, which reads as a success."""
    from app import main

    (tmp_path / "index.html").write_text("<!doctype html><title>Hyperlite</title>")
    monkeypatch.setattr(main, "DASHBOARD_DIST", str(tmp_path))
    r = client.get("/kubernetes", headers={"Accept": "application/json"})
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
    assert client.get("/no/such/route").status_code == 404
    page = client.get("/vm/web", headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
    assert page.status_code == 200 and "<title>Hyperlite</title>" in page.text


def test_large_answers_are_compressed_for_clients_that_accept_it(client, auth_headers, monkeypatch, tmp_path):
    from app import main

    (tmp_path / "index.html").write_text("<!doctype html><title>Hyperlite</title>" + "x" * 5000)
    monkeypatch.setattr(main, "DASHBOARD_DIST", str(tmp_path))
    r = client.get("/vm/web", headers={"Accept": "text/html", "Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip" and "<title>Hyperlite</title>" in r.text
    small = client.get("/no/such/route", headers={"Accept": "application/json", "Accept-Encoding": "gzip"})
    assert small.status_code == 404 and "content-encoding" not in small.headers  # under 1 kB: not worth it
