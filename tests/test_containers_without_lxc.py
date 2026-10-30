"""A node whose libvirt has no LXC driver (a VM-only host, a CI runner) runs no containers: their list is empty and a
route on a named container says why (501), never a 500 with libvirt's error (app/core/libvirt_utils.py)."""

import libvirt
import pytest

from app.core import libvirt_utils


@pytest.fixture()
def no_lxc_driver(monkeypatch):
    real = libvirt.open

    def open_(uri=None):
        if uri == libvirt_utils.LXC_URI:
            raise libvirt.libvirtError("no connection driver available for lxc:///system")
        return real(uri)

    monkeypatch.setattr(libvirt_utils.libvirt, "open", open_)


def test_the_list_is_empty(client, auth_headers, no_lxc_driver):
    r = client.get("/containers", headers=auth_headers("ana", "admin"))
    assert r.status_code == 200 and r.json() == {"items": [], "total": 0}


@pytest.mark.parametrize(
    "method, path", [("GET", "/containers/web"), ("POST", "/containers/web/start"), ("POST", "/containers/web/stop")]
)
def test_a_named_container_is_not_implemented_here(client, auth_headers, no_lxc_driver, method, path):
    r = client.request(method, path, headers=auth_headers("ana", "admin"))
    assert r.status_code == 501, r.text
    assert r.json()["code"] == "not_implemented" and "no LXC driver" in r.json()["detail"]


def test_what_looks_at_every_guest_finds_none(no_lxc_driver):
    assert libvirt_utils.open_lxc_conn_if_any() is None
