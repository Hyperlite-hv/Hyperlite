"""Renaming this node: its host name (hostnamectl) and its line in /etc/hosts, so that the name libvirt reports,
the one shown for the node, is exactly the one given."""

import pytest

DEBIAN = (
    "127.0.0.1\tlocalhost\n"
    "127.0.1.1\thyperlite.home\thyperlite\n"
    "\n"
    "# The following lines are desirable for IPv6 capable hosts\n"
    "::1     localhost ip6-localhost ip6-loopback\n"
)


@pytest.fixture()
def host(tmp_path, monkeypatch):
    from app.core import host_system

    hosts = tmp_path / "hosts"
    hosts.write_text(DEBIAN)
    calls = []
    monkeypatch.setattr(host_system, "ETC_HOSTS", hosts)
    monkeypatch.setattr(host_system, "ETC_HOSTNAME", tmp_path / "hostname")
    monkeypatch.setattr(host_system.socket, "sethostname", lambda name: calls.append(["sethostname", name]))
    monkeypatch.setattr(host_system.socket, "gethostname", lambda: "hyperlite")
    return host_system, hosts, calls


def test_a_full_name_sets_the_host_name_and_the_hosts_line(host):
    host_system, hosts, calls = host
    assert host_system.set_hostname("PVE1.lan", current="hyperlite.home") == {
        "nom": "pve1.lan",
        "ancien": "hyperlite.home",
    }
    assert calls == [["sethostname", "pve1"]]
    assert (hosts.parent / "hostname").read_text() == "pve1\n"
    text = hosts.read_text()
    assert "127.0.1.1\tpve1.lan\tpve1\n" in text and "hyperlite" not in text
    assert text.startswith("127.0.0.1\tlocalhost\n") and "ip6-localhost" in text  # the rest is kept


def test_a_short_name_is_shown_as_given(host):
    host_system, hosts, _calls = host
    host_system.set_hostname("pve1", current="hyperlite.home")
    assert "127.0.1.1\tpve1\n" in hosts.read_text()


def test_other_lines_naming_the_host_follow_and_a_missing_line_is_added(host):
    host_system, hosts, _calls = host
    hosts.write_text("127.0.0.1 localhost\n10.0.0.5 hyperlite.home hyperlite # this host\n10.0.0.6 other\n")
    host_system.set_hostname("pve1.lan", current="hyperlite.home")
    lines = hosts.read_text().splitlines()
    assert lines == [
        "127.0.0.1 localhost",
        "127.0.1.1\tpve1.lan\tpve1",
        "10.0.0.5\tpve1.lan\tpve1 # this host",
        "10.0.0.6 other",
    ]


@pytest.mark.parametrize("bad", ["", "bad name", "-x", "localhost", "local.lan", "10.0.0.1", "a" * 64, "x;reboot"])
def test_invalid_names_are_refused_before_anything_changes(host, bad):
    host_system, hosts, calls = host
    with pytest.raises(host_system.SettingError):
        host_system.set_hostname(bad)
    assert calls == [] and hosts.read_text() == DEBIAN


def test_a_refused_host_name_leaves_the_files(host, monkeypatch):
    host_system, hosts, _calls = host

    def refuse(name):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(host_system.socket, "sethostname", refuse)
    with pytest.raises(host_system.SettingError, match="not permitted"):
        host_system.set_hostname("pve1")
    assert hosts.read_text() == DEBIAN and not (hosts.parent / "hostname").exists()


def test_renaming_the_node_is_for_administrators(client, auth_headers, host):
    r = client.put("/host/system/hostname", json={"nom": "pve1"}, headers=auth_headers("olga", role="observateur"))
    assert r.status_code == 403
    r = client.put("/host/system/hostname", json={"nom": "pve1.home"}, headers=auth_headers("alice"))
    assert r.status_code == 200 and r.json()["nom"] == "pve1.home"
    assert client.put("/host/system/hostname", json={"nom": "no way"}, headers=auth_headers("bob")).status_code == 422
