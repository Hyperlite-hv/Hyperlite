"""Outbound notifications, tested against a real local HTTP receiver."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.core import notifications


@pytest.fixture()
def receiver():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            received.append(json.loads(self.rfile.read(length)))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/hook", received
    server.shutdown()
    server.server_close()


def test_a_subscribed_event_reaches_the_webhook(database, receiver):
    url, received = receiver
    notifications.create_channel("webhook", "ops", {"url": url}, ["create_vm"], "alice")
    notifications.notify("create_vm", "VM created", "vm1 is ready")
    assert len(received) == 1
    assert received[0]["event"] == "create_vm" and received[0]["title"] == "VM created"
    assert received[0]["source"] == "hyperlite"


def test_a_channel_only_receives_the_events_it_subscribed_to(database, receiver):
    url, received = receiver
    notifications.create_channel("webhook", "deletions", {"url": url}, ["delete_vm"], "alice")
    notifications.notify("create_vm", "VM created", "vm1")
    assert received == []


def test_a_channel_without_a_filter_receives_every_notifiable_event(database, receiver):
    url, received = receiver
    notifications.create_channel("webhook", "all", {"url": url}, [], "alice")
    notifications.notify("create_vm", "a", "b")
    notifications.notify("delete_vm", "a", "b")
    assert len(received) == 2


def test_events_that_are_not_notifiable_are_ignored(database, receiver):
    url, received = receiver
    notifications.create_channel("webhook", "all", {"url": url}, [], "alice")
    notifications.notify("list_networks", "noise", "noise")
    assert received == []


def test_disabled_channels_are_skipped(database, receiver):
    url, received = receiver
    notifications.create_channel("webhook", "ops", {"url": url}, [], "alice")
    channel = notifications.list_channels()[0]
    notifications.set_enabled(channel["id"], False)
    notifications.notify("create_vm", "a", "b")
    assert received == []


def test_a_failing_channel_never_raises_and_does_not_block_the_others(database, receiver):
    url, received = receiver
    notifications.create_channel("webhook", "dead", {"url": "http://127.0.0.1:9/unreachable"}, [], "alice")
    notifications.create_channel("webhook", "alive", {"url": url}, [], "alice")
    notifications.notify("create_vm", "a", "b")  # must not raise
    assert len(received) == 1


def test_the_test_button_sends_regardless_of_event_filters(database, receiver):
    url, received = receiver
    notifications.create_channel("webhook", "ops", {"url": url}, ["delete_vm"], "alice")
    notifications.send_to_channel(notifications.list_channels()[0], "Test", "hello")
    assert received and received[0]["event"] == "test"


def test_unknown_channel_type_is_rejected(database):
    with pytest.raises(ValueError):
        notifications.send_to_channel({"type": "pager", "config": {}}, "t", "m")


def test_smtp_password_is_encrypted_at_rest(database):
    notifications.create_channel(
        "email",
        "mail",
        {"smtp_host": "smtp.example.test", "smtp_password": "s3cret", "to": "a@example.test"},
        [],
        "alice",
    )
    with database.get_conn() as conn:
        raw = conn.execute("SELECT config FROM notification_channels").fetchone()["config"]
    assert "s3cret" not in raw
