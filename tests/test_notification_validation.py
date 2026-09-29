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


# --- Editing a channel in place ---


def _email_channel(client, headers):
    config = {
        "smtp_host": "smtp.example.invalid",
        "smtp_port": 587,
        "from_addr": "hv@example.invalid",
        "to_addr": "ops@example.invalid",
        "smtp_user": "hv",
        "smtp_password": "s3cret",
    }
    res = client.post(
        "/notifications/channels",
        headers=headers,
        json={"type": "email", "name": "mail", "config": config, "events": ["create_vm"]},
    )
    assert res.status_code == 201
    return res.json()["id"], config


def _stored(channel_id):
    from app.core import notifications

    return next(c for c in notifications.list_channels() if c["id"] == channel_id)


def test_an_email_channel_is_edited_in_place_and_keeps_its_password(client, auth_headers):
    headers = auth_headers("admin")
    channel_id, config = _email_channel(client, headers)
    edited = {**config, "smtp_host": "relay.example.invalid", "smtp_port": 465, "smtp_password": ""}
    res = client.patch(
        f"/notifications/channels/{channel_id}",
        headers=headers,
        json={"name": "mail 2", "config": edited, "events": ["delete_vm"]},
    )
    assert res.status_code == 200, res.text
    channel = _stored(channel_id)
    assert channel["name"] == "mail 2"
    assert channel["config"]["smtp_host"] == "relay.example.invalid"
    assert channel["config"]["smtp_password"] == "s3cret"  # kept, still decryptable
    assert channel["events"] == ["delete_vm"]
    assert channel["enabled"] is True


def test_a_new_password_replaces_the_old_one_and_can_be_cleared(client, auth_headers):
    headers = auth_headers("admin")
    channel_id, config = _email_channel(client, headers)
    client.patch(
        f"/notifications/channels/{channel_id}", headers=headers, json={"config": {**config, "smtp_password": "n3w"}}
    )
    assert _stored(channel_id)["config"]["smtp_password"] == "n3w"
    client.patch(f"/notifications/channels/{channel_id}", headers=headers, json={"clear_smtp_password": True})
    assert not _stored(channel_id)["config"].get("smtp_password")


def test_enabling_alone_still_works(client, auth_headers):
    headers = auth_headers("admin")
    channel_id, _ = _email_channel(client, headers)
    assert (
        client.patch(f"/notifications/channels/{channel_id}", headers=headers, json={"enabled": False}).status_code
        == 200
    )
    channel = _stored(channel_id)
    assert channel["enabled"] is False and channel["name"] == "mail"


def test_an_edit_is_validated_like_a_creation(client, auth_headers):
    headers = auth_headers("admin")
    res = client.post(
        "/notifications/channels",
        headers=headers,
        json={"type": "webhook", "name": "hook", "config": {"url": "https://example.invalid/h"}, "events": []},
    )
    channel_id = res.json()["id"]
    url = f"/notifications/channels/{channel_id}"
    assert client.patch(url, headers=headers, json={"config": {"url": "file:///etc/passwd"}}).status_code == 422
    assert client.patch(url, headers=headers, json={"events": ["no_such_event"]}).status_code == 422
    assert client.patch(url, headers=headers, json={"name": "  "}).status_code == 422
    assert client.patch("/notifications/channels/9999", headers=headers, json={"enabled": True}).status_code == 404
    assert client.delete("/notifications/channels/9999", headers=headers).status_code == 404


def test_only_administrators_edit_a_channel(client, auth_headers):
    admin = auth_headers("admin")
    channel_id, _ = _email_channel(client, admin)
    viewer = auth_headers("olga", role="observateur")
    assert client.patch(f"/notifications/channels/{channel_id}", headers=viewer, json={"name": "x"}).status_code == 403
