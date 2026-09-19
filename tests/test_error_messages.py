"""Error categorisation helper."""

from app.core.error_messages import describe_exception


def test_changed_ssh_host_key_gets_an_actionable_message():
    raw = "Cannot recv data: @@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@"
    message = describe_exception(RuntimeError(raw))
    assert "host key" in message
    assert "remove it and add it again" in message
    assert raw in message  # the original message is never hidden


def test_untrusted_ssh_host_key_is_recognised():
    assert "host key" in describe_exception(RuntimeError("Host key verification failed."))


def test_unknown_errors_keep_their_original_text():
    assert describe_exception(ValueError("something unusual")) == "something unusual"
