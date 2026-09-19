"""Outbound URL validation."""

import pytest

from app.core import notifications, sso
from app.core.http_safety import require_http_url


@pytest.mark.parametrize("url", ["http://example.test/hook", "https://example.test:8443/a?b=c"])
def test_http_and_https_urls_are_accepted(url):
    assert require_http_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.test/x",
        "gopher://example.test",
        "javascript:alert(1)",
        "//example.test/x",
        "example.test/x",
        "",
        None,
    ],
)
def test_other_schemes_and_relative_urls_are_rejected(url):
    with pytest.raises(ValueError):
        require_http_url(url)


def test_webhook_refuses_a_local_file_url():
    with pytest.raises(ValueError):
        notifications._send_webhook({"url": "file:///etc/passwd"}, "title", "message", "event", "ok")


def test_oidc_helpers_refuse_a_local_file_url():
    with pytest.raises(ValueError):
        sso._http_get_json("file:///etc/hostname")
    with pytest.raises(ValueError):
        sso._http_post_form("file:///etc/hostname", {"a": "b"})
