"""The update channel of a node (docs/design/updates-1.0.md, section 7): the suite of Hyperlite's APT source, stable
or test, chosen per node; a node going to a release is not shown the notes of the test builds before it."""

import pytest

from app.core import host_system
from app.routers import update

OFFICIAL = "https://hyperlite-hv.github.io"
LINE = f"deb [signed-by=/usr/share/keyrings/hyperlite-archive-keyring.gpg] {OFFICIAL} stable main"


@pytest.fixture()
def source(tmp_path, monkeypatch):
    path = tmp_path / "hyperlite.list"
    monkeypatch.setattr(host_system, "HYPERLITE_LIST", path)
    return path


def test_the_suite_is_read_and_changed_in_the_source_line(source):
    source.write_text(f"# Hyperlite\n{LINE}\n")
    assert host_system.channel() == "stable"
    assert host_system.set_channel("test") == "test"
    assert source.read_text() == f"# Hyperlite\n{LINE.replace(' stable ', ' test ')}\n"
    assert host_system.set_channel("stable") == "stable"
    assert source.read_text() == f"# Hyperlite\n{LINE}\n"


@pytest.mark.parametrize("name", ["unstable", "stable main", "", "test\ndeb http://evil/ stable main"])
def test_only_a_known_channel_is_written(source, name):
    source.write_text(LINE + "\n")
    with pytest.raises(host_system.SettingError):
        host_system.set_channel(name)
    assert source.read_text() == LINE + "\n"


def test_a_node_without_the_repository_has_no_channel(source):
    assert host_system.channel() is None
    with pytest.raises(host_system.SettingError):
        host_system.set_channel("test")
    assert not source.exists()


def test_the_api(source, client, auth_headers):
    source.write_text(LINE + "\n")
    admin = auth_headers("root")
    assert client.get("/update/channel", headers=admin).json() == {"canal": "stable", "channels": ["stable", "test"]}
    r = client.put("/update/channel", json={"canal": "test"}, headers=admin)
    assert r.status_code == 200 and r.json()["canal"] == "test"
    assert client.put("/update/channel", json={"canal": "nightly"}, headers=admin).status_code == 422
    eve = auth_headers("eve", role="viewer")
    assert client.put("/update/channel", json={"canal": "stable"}, headers=eve).status_code == 403
    assert host_system.channel() == "test"


def test_the_source_to_restore_names_the_node_channel(source, monkeypatch, tmp_path):
    conf = tmp_path / "apt-source.conf"
    conf.write_text(f'HYPERLITE_APT_URL="{OFFICIAL}"\n')
    monkeypatch.setattr(update, "APT_SOURCE_CONF", conf)
    source.write_text(LINE.replace(" stable ", " test ") + "\n")
    assert f"{OFFICIAL} test main" in update._source_problem("hyperlite:\n  Version table:\n")


def test_a_release_does_not_show_the_test_builds_notes(monkeypatch, tmp_path):
    conf = tmp_path / "apt-source.conf"
    conf.write_text(f'HYPERLITE_APT_URL="{OFFICIAL}"\n')
    monkeypatch.setattr(update, "APT_SOURCE_CONF", conf)
    index = '[{"version": "1.0.1", "fr": false}, {"version": "1.0.1~test.20261011.1530", "fr": false}]'
    monkeypatch.setattr(update, "_fetch_text", lambda url: index if url.endswith("index.json") else url)
    # A stable node going from 1.0.0 to 1.0.1: only the release's notes.
    assert [n["version"] for n in update._release_notes("1.0.0", "1.0.1", "en")] == ["1.0.1"]
    # A test node going to the next test build: that build's notes.
    later = "1.0.2~test.20261012.0900"
    assert [n["version"] for n in update._release_notes("1.0.0", later, "en")] == ["1.0.1", "1.0.1~test.20261011.1530"]
