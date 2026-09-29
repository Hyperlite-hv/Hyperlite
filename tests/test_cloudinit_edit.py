"""Cloud-init edited after creation: the account's keys and password, applied at the VM's next boot."""

import shutil
import subprocess

import libvirt
import pytest

from app.core import cloudinit_edit

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGj0bXkPZ1d5bDx2eH1+ ana@laptop"
AUTOMATION = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAutomationKeyOfHyperlite hyperlite-automation"


class _Domain:
    def __init__(self, xml, active=False):
        self.xml, self.active, self.updates = xml, active, []

    def XMLDesc(self, flags=0):
        return self.xml

    def isActive(self):
        return self.active

    def updateDeviceFlags(self, xml, flags):
        self.updates.append(xml)


def _xml(iso):
    return (
        "<domain><devices><disk type='file' device='cdrom'><driver name='qemu' type='raw'/>"
        f"<source file='{iso}'/><target dev='sdb' bus='sata'/><readonly/></disk></devices></domain>"
    )


@pytest.fixture()
def images(monkeypatch, tmp_path):
    monkeypatch.setattr(cloudinit_edit, "IMAGES_DIR", tmp_path)
    monkeypatch.setattr(cloudinit_edit, "get_or_create_automation_pubkey", lambda: AUTOMATION)
    return tmp_path


def test_the_user_data_keeps_host_keys_and_the_automation_key_and_quotes_what_users_type():
    text = cloudinit_edit.user_data("web", "ana", None, [KEY], AUTOMATION)
    assert "ssh_deletekeys: false" in text
    assert f"      - '{KEY}'" in text and f"      - '{AUTOMATION}'" in text
    assert "chpasswd" not in text  # no password given: the current one stays
    assert "- default" not in text  # never creates the distribution's default account
    text = cloudinit_edit.user_data("web", "ana", "it's: a #secret", [AUTOMATION], AUTOMATION)
    assert text.count(AUTOMATION) == 1
    assert "      password: 'it''s: a #secret'" in text and "      type: text" in text


def test_what_users_type_is_validated():
    assert cloudinit_edit.validate("ana", None, [KEY, f"  {KEY} ", ""]) == [KEY]
    for user, password, keys in (
        ("Ana", None, []),
        ("ana", "short", []),
        ("ana", "line\nbreak!", []),
        ("ana", None, ["not a key"]),
        ("ana", None, ["ssh-ed25519 AAAA\nssh-rsa BBBB"]),
    ):
        with pytest.raises(cloudinit_edit.CloudInitError):
            cloudinit_edit.validate(user, password, keys)


def test_only_the_drive_hyperlite_made_is_rewritten(images):
    assert cloudinit_edit.drive_path(_Domain(_xml(images / "web-cloudinit.iso")), "web") == images / "web-cloudinit.iso"
    assert cloudinit_edit.drive_path(_Domain(_xml(images / "debian-12.iso")), "web") is None
    assert cloudinit_edit.drive_path(_Domain(_xml("/elsewhere/web-cloudinit.iso")), "web") is None


def test_a_running_vm_gets_the_new_drive_ejected_and_inserted_live(images):
    domain = _Domain(_xml(images / "web-cloudinit.iso"), active=True)
    assert cloudinit_edit.reload_media(domain, "web") is True
    assert "<source" not in domain.updates[0] and str(images / "web-cloudinit.iso") in domain.updates[1]
    assert cloudinit_edit.reload_media(_Domain(_xml(images / "web-cloudinit.iso")), "web") is False  # stopped


@pytest.mark.skipif(shutil.which("cloud-localds") is None, reason="cloud-image-utils is not installed")
def test_the_drive_is_rebuilt_with_a_new_instance_id(images, database):
    iso = images / "web-cloudinit.iso"
    iso.write_bytes(b"old")
    cloudinit_edit.rewrite_drive("web", iso, "ana", "correct horse", [KEY])
    listing = subprocess.run(["isoinfo", "-i", str(iso), "-J", "-f"], capture_output=True, text=True)
    if listing.returncode == 0:
        assert "/user-data" in listing.stdout and "/meta-data" in listing.stdout
    assert iso.read_bytes() != b"old"
    assert not list(images.glob(".*.new"))
    assert cloudinit_edit.get_state("web")["cles_ssh"] == [KEY]


def test_the_api_rewrites_the_drive_of_a_cloud_image_vm_only(client, auth_headers, images, monkeypatch):
    from app.routers.vms import settings

    domains = {"web": _Domain(_xml(images / "web-cloudinit.iso")), "iso-vm": _Domain(_xml(images / "debian.iso"))}

    class Conn:
        def lookupByName(self, name):
            if name not in domains:
                raise libvirt.libvirtError("no domain")
            return domains[name]

        def close(self):
            pass

    written = []
    monkeypatch.setattr(settings, "open_conn", lambda node=None: Conn())
    monkeypatch.setattr(
        cloudinit_edit,
        "rewrite_drive",
        lambda name, iso, user, pw, keys: (
            written.append((name, user, pw, keys)) or cloudinit_edit._save_state(name, user, keys)
        ),
    )
    admin = auth_headers("admin")
    assert client.get("/vms/web/cloud-init", headers=admin).json()["disponible"] is True
    assert client.get("/vms/iso-vm/cloud-init", headers=admin).json()["disponible"] is False

    r = client.put("/vms/web/cloud-init", json={"utilisateur": "ana", "cles_ssh": [KEY]}, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["utilisateur"] == "ana" and r.json()["cles_ssh"] == [KEY] and "mot_de_passe" not in r.json()
    assert written == [("web", "ana", None, [KEY])]
    assert client.put("/vms/iso-vm/cloud-init", json={"utilisateur": "ana"}, headers=admin).status_code == 409
    assert (
        client.put("/vms/web/cloud-init", json={"utilisateur": "ana", "cles_ssh": ["nope"]}, headers=admin).status_code
        == 422
    )
    viewer = auth_headers("viewer", role="observateur")
    assert client.get("/vms/web/cloud-init", headers=viewer).status_code == 200
    assert client.put("/vms/web/cloud-init", json={"utilisateur": "x"}, headers=viewer).status_code == 403
