from app.routers import update


def test_out_of_sync_mirror_gets_an_actionable_message():
    stderr = "Err:2 https://x stable/main amd64 Packages\n  File has unexpected size (3800 != 3368). Mirror sync in progress?"
    message = update._explain_apt_error(stderr)
    assert "try again" in message
    assert "3800 != 3368" in message


def test_other_apt_errors_are_passed_through():
    assert update._explain_apt_error("Could not resolve 'example.test'") == "Could not resolve 'example.test'"


def test_empty_output_is_safe():
    assert update._explain_apt_error(None) == ""
