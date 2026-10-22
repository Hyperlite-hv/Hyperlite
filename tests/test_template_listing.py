"""What GET /templates says of a template: besides its description, the facts a client sizes a deployment by, as
vSphere's template is read (its firmware, its disk), so a Terraform configuration can write
`size_gb = data.hyperlite_template.x.disks[0].size_gb` and never shrink the disk it deploys."""

import json

import pytest

from app.core import templates_store

GB = 1024**3


def _qcow2(path, virtual_size):
    # The header fields a reader needs: the magic, then the virtual size at byte 24 (big-endian).
    path.write_bytes(b"QFI\xfb" + bytes(20) + virtual_size.to_bytes(8, "big") + bytes(32))


def _template(tpl_dir, name, os_xml):
    (tpl_dir / f"{name}.json").write_text(json.dumps({"name": name, "vcpu": 2, "memory_mb": 2048}))
    (tpl_dir / f"{name}.xml").write_text(f"<domain type='kvm'><name>{name}</name>{os_xml}<devices/></domain>")


@pytest.fixture()
def tpl_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(templates_store, "TEMPLATES_DIR", tmp_path)
    return tmp_path


def test_a_template_lists_its_firmware_and_its_disk_size_rounded_up(tpl_dir, client, auth_headers):
    _template(tpl_dir, "debian", "<os><type>hvm</type></os>")
    _qcow2(tpl_dir / "debian.qcow2", 3 * GB)
    _template(
        tpl_dir, "win11", "<os firmware='efi'><firmware><feature enabled='yes' name='secure-boot'/></firmware></os>"
    )
    _qcow2(tpl_dir / "win11.qcow2", 40 * GB + 1)  # a byte over 40 GB: a 40 GB disk would be smaller than the template
    _template(tpl_dir, "alpine", "<os><loader type='pflash'/></os>")
    (tpl_dir / "alpine.qcow2").write_bytes(bytes(1024))  # a raw image under the qcow2 name: its file size
    r = client.get("/templates", headers=auth_headers("alice"))
    assert r.status_code == 200
    by_name = {t["name"]: t for t in r.json()["items"]}
    assert by_name["debian"]["firmware"] == "bios" and by_name["debian"]["disks"] == [{"size_gb": 3}]
    assert by_name["win11"]["firmware"] == "uefi_secure" and by_name["win11"]["disks"] == [{"size_gb": 41}]
    assert by_name["alpine"]["firmware"] == "uefi" and by_name["alpine"]["disks"] == [{"size_gb": 1}]


def test_a_template_whose_disk_is_still_being_copied_lists_no_disk(tpl_dir):
    _template(tpl_dir, "big", "<os/>")
    [listed] = templates_store.list_templates()
    assert listed["disks"] == [] and listed["firmware"] == "bios"
