"""Configuration history (app/core/config_history.py): a new version only when the definition changed, at most once a
minute per VM, the oldest dropped beyond KEEP, the diff against the version before, and who acted in between."""

import pytest

from app.core import config_history


class _Dom:
    def __init__(self, xml):
        self.xml = xml

    def XMLDesc(self, flags=0):
        return self.xml


@pytest.fixture()
def history(database):
    config_history.forget()
    yield
    config_history.forget()


def test_a_version_is_kept_only_when_the_definition_changes(history):
    dom = _Dom("<domain>\n  <memory>1024</memory>\n</domain>")
    config_history.observe(dom, "web", monotonic=0)
    config_history.observe(dom, "web", monotonic=100)  # same definition
    dom.xml = "<domain>\n  <memory>2048</memory>   \n</domain>"
    config_history.observe(dom, "web", monotonic=110)  # within the minute: not looked at yet
    assert len(config_history.versions("web")) == 1
    config_history.observe(dom, "web", monotonic=200)
    newest, first = config_history.versions("web")
    assert first["first"] and not newest["first"]
    d = config_history.diff("web", newest["id"])
    assert "-  <memory>1024</memory>" in d["diff"] and "+  <memory>2048</memory>" in d["diff"]
    assert d["previous_id"] == first["id"]
    dom.xml += "\n\n"  # blank lines and trailing spaces are no change
    config_history.observe(dom, "web", monotonic=300)
    assert len(config_history.versions("web")) == 2


def test_only_the_last_versions_are_kept(history, monkeypatch):
    monkeypatch.setattr(config_history, "KEEP", 3)
    for i in range(5):
        config_history.observe(_Dom(f"<domain><vcpu>{i}</vcpu></domain>"), "db", monotonic=i * 100)
    assert len(config_history.versions("db")) == 3


def test_a_restart_does_not_record_the_same_definition_again(history):
    dom = _Dom("<domain/>")
    config_history.observe(dom, "web", monotonic=0)
    config_history.forget()  # the process restarted
    config_history.observe(dom, "web", monotonic=0)
    assert len(config_history.versions("web")) == 1


def test_the_api_shows_versions_and_a_diff(client, auth_headers, history):
    config_history.observe(_Dom("<domain><vcpu>1</vcpu></domain>"), "hv-02:web", monotonic=0)
    config_history.observe(_Dom("<domain><vcpu>2</vcpu></domain>"), "hv-02:web", monotonic=100)
    headers = auth_headers("root")
    items = client.get("/vms/web/config-history?node=hv-02", headers=headers).json()["items"]
    assert len(items) == 2
    assert (
        "+<domain><vcpu>2</vcpu></domain>"
        in client.get(f"/vms/web/config-history/{items[0]['id']}?node=hv-02", headers=headers).json()["diff"]
    )
    assert client.get("/vms/web/config-history?node=hv-03", headers=headers).json()["items"] == []
    assert client.get("/vms/web/config-history/9999", headers=headers).status_code == 404
