import pytest


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.invalid/x", "javascript:alert(1)", ""])
def test_webhook_channel_with_a_non_http_url_is_refused_at_creation(client, auth_headers, url):
    res = client.post(
        "/notifications/channels",
        headers=auth_headers("admin"),
        json={"type": "webhook", "name": "bad", "config": {"url": url}, "events": []},
    )
    assert res.status_code == 422
    assert "Invalid webhook URL" in res.json()["detail"]


def test_webhook_channel_with_an_https_url_is_accepted(client, auth_headers):
    res = client.post(
        "/notifications/channels",
        headers=auth_headers("admin"),
        json={"type": "webhook", "name": "ok", "config": {"url": "https://example.invalid/hook"}, "events": []},
    )
    assert res.status_code == 201
