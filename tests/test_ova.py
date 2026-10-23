"""VMs exported from VMware as an OVA (app/core/ova.py): the descriptor mapped to Hyperlite's settings, the disks
copied out and checked against the manifest, what a hostile file is refused for, the API, and a VM made with several
imported disks. The descriptors here follow VMware's own output (ovftool and ESXi 7 exports, Ubuntu's OVA checked in
docs/design/ova-import.md)."""

import hashlib
import importlib
import io
import tarfile
from unittest.mock import Mock

import libvirt
import pytest

from app.core import ova

HEAD = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Envelope xmlns="http://schemas.dmtf.org/ovf/envelope/1" xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1" '
    'xmlns:rasd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData" '
    'xmlns:vmw="http://www.vmware.com/schema/ovf">'
)


def item(kind, **fields):
    body = "".join(f"<rasd:{k}>{v}</rasd:{k}>" for k, v in fields.items())
    return f"<Item>{body}<rasd:ResourceType>{kind}</rasd:ResourceType></Item>"


def descriptor(
    disks=(("disk1.vmdk", 40, "byte * 2^30"),),
    firmware="bios",
    secure=None,
    os_type="ubuntu64Guest",
    os_desc="Ubuntu Linux (64-bit)",
    memory=("4", "byte * 2^30"),
    nics=(("VM Network", "VmxNet3"),),
    systems=1,
    extra_file_attrs="",
):
    refs = "".join(f'<File ovf:href="{f}" ovf:id="file{i}" {extra_file_attrs}/>' for i, (f, _c, _u) in enumerate(disks))
    disk_section = "".join(
        f'<Disk ovf:capacity="{c}" ovf:capacityAllocationUnits="{u}" ovf:diskId="vmdisk{i}" ovf:fileRef="file{i}"/>'
        for i, (_f, c, u) in enumerate(disks)
    )
    items = item(3, VirtualQuantity=4) + item(4, VirtualQuantity=memory[0], AllocationUnits=memory[1])
    items += "".join(item(17, HostResource=f"ovf:/disk/vmdisk{i}") for i in range(len(disks)))
    items += "".join(item(10, Connection=n, ResourceSubType=m) for n, m in nics)
    configs = f'<vmw:Config ovf:required="false" vmw:key="firmware" vmw:value="{firmware}"/>'
    if secure is not None:
        configs += f'<vmw:Config ovf:required="false" vmw:key="uefi.secureBoot.enabled" vmw:value="{secure}"/>'
    system = (
        '<VirtualSystem ovf:id="vm"><Name>web-01</Name>'
        f'<OperatingSystemSection ovf:id="1" vmw:osType="{os_type}"><Description>{os_desc}</Description>'
        f"</OperatingSystemSection><VirtualHardwareSection>{items}{configs}</VirtualHardwareSection></VirtualSystem>"
    )
    return (
        f"{HEAD}<References>{refs}</References><DiskSection>{disk_section}</DiskSection>{system * systems}</Envelope>"
    )


def make_ova(path, ovf_text, files, manifest=True, digests=None):
    """A tar as ovftool writes it: the .ovf first, then the manifest, then the disks."""
    with tarfile.open(path, "w") as tar:

        def add(name, data):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        add("vm.ovf", ovf_text.encode())
        if manifest:
            lines = [f"SHA256(vm.ovf)= {hashlib.sha256(ovf_text.encode()).hexdigest()}"]
            for name, data in files.items():
                digest = (digests or {}).get(name, hashlib.sha256(data).hexdigest())
                lines.append(f"SHA256({name})= {digest}")
            add("vm.mf", ("\n".join(lines) + "\n").encode())
        for name, data in files.items():
            add(name, data)
    return path


def test_a_windows_uefi_vm_with_two_disks_and_two_cards_is_mapped(tmp_path):
    ovf_text = descriptor(
        disks=(("web-01-disk1.vmdk", 64, "byte * 2^30"), ("web-01-disk2.vmdk", 214748364800, "byte")),
        firmware="efi",
        secure="true",
        os_type="windows2019srv_64Guest",
        os_desc="Microsoft Windows Server 2019 (64-bit)",
        memory=("8192", "byte * 2^20"),
        nics=(("Prod", "E1000e"), ("Backup", "VmxNet3")),
    )
    path = make_ova(tmp_path / "w.ova", ovf_text, {"web-01-disk1.vmdk": b"a" * 512, "web-01-disk2.vmdk": b"b" * 512})
    info = ova.read(path)
    assert (info["name"], info["vcpu"], info["memory_mb"]) == ("web-01", 4, 8192)
    assert (info["firmware"], info["guest_os"]) == ("uefi_secure", "windows")
    assert [(d["file"], d["capacity_gb"]) for d in info["disks"]] == [
        ("web-01-disk1.vmdk", 64),
        ("web-01-disk2.vmdk", 200),
    ]
    assert info["nics"] == [{"network": "Prod", "model": "E1000e"}, {"network": "Backup", "model": "VmxNet3"}]


@pytest.mark.parametrize(
    ("firmware", "secure", "expected"), [("bios", None, "bios"), ("efi", None, "uefi"), ("efi", "false", "uefi")]
)
def test_the_firmware_follows_vmwares_setting(tmp_path, firmware, secure, expected):
    path = make_ova(tmp_path / "f.ova", descriptor(firmware=firmware, secure=secure), {"disk1.vmdk": b"x"})
    assert ova.read(path)["firmware"] == expected


@pytest.mark.parametrize(
    ("os_type", "family"),
    [("debian12_64Guest", "linux"), ("windows11_64Guest", "windows"), ("freebsd13_64Guest", "other")],
)
def test_the_guest_family_sets_the_device_models(tmp_path, os_type, family):
    path = make_ova(tmp_path / "o.ova", descriptor(os_type=os_type, os_desc=""), {"disk1.vmdk": b"x"})
    assert ova.read(path)["guest_os"] == family


def _bad(tmp_path, ovf_text=None, files=None, extra=None):
    path = tmp_path / "bad.ova"
    make_ova(path, ovf_text or descriptor(), files or {"disk1.vmdk": b"x"})
    if extra:
        with tarfile.open(path, "a") as tar:
            extra(tar)
    return path


def test_a_dtd_is_refused_before_any_parsing(tmp_path):
    bomb = HEAD.replace(
        '<?xml version="1.0" encoding="UTF-8"?>\n', '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]>'
    )
    path = _bad(tmp_path, ovf_text=bomb + "</Envelope>")
    with pytest.raises(ova.OvaError, match="DTD"):
        ova.read(path)


def test_links_and_paths_inside_the_archive_are_refused(tmp_path):
    def link(tar):
        info = tarfile.TarInfo("evil.vmdk")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/shadow"
        tar.addfile(info)

    with pytest.raises(ova.OvaError, match="does not read"):
        ova.read(_bad(tmp_path, extra=link))

    def path_entry(tar):
        info = tarfile.TarInfo("../outside.vmdk")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))

    with pytest.raises(ova.OvaError, match="does not read"):
        ova.read(_bad(tmp_path, extra=path_entry))


@pytest.mark.parametrize(
    ("ovf_text", "match"),
    [
        (descriptor(extra_file_attrs='ovf:compression="gzip"'), "Compressed"),
        (descriptor(disks=(("missing.vmdk", 1, "byte * 2^30"),)), "not in the OVA"),
        (descriptor(systems=2), "several VMs"),
        (descriptor(memory=("lots", "byte * 2^20")), "could not be read"),
        (descriptor(memory=("4", "parsecs")), "allocation units"),
    ],
)
def test_what_cannot_be_imported_is_said(tmp_path, ovf_text, match):
    with pytest.raises(ova.OvaError, match=match):
        ova.read(_bad(tmp_path, ovf_text=ovf_text))


def test_not_a_tar_file_is_refused(tmp_path):
    path = tmp_path / "x.ova"
    path.write_bytes(b"not a tar")
    with pytest.raises(ova.OvaError, match="Not an OVA"):
        ova.read(path)


def test_the_disks_are_extracted_in_order_and_checked_against_the_manifest(tmp_path):
    files = {"d1.vmdk": b"first" * 1000, "d2.vmdk": b"second" * 1000}
    ovf_text = descriptor(disks=(("d1.vmdk", 10, "byte * 2^30"), ("d2.vmdk", 20, "byte * 2^30")))
    path = make_ova(tmp_path / "vm.ova", ovf_text, files)
    out = tmp_path / "out"
    out.mkdir()
    assert ova.extract_disks(path, out, "web") == ["web-disk1.vmdk", "web-disk2.vmdk"]
    assert (out / "web-disk1.vmdk").read_bytes() == files["d1.vmdk"]
    assert (out / "web-disk2.vmdk").read_bytes() == files["d2.vmdk"]
    with pytest.raises(ova.OvaError, match="exists already"):
        ova.extract_disks(path, out, "web")


def test_a_disk_that_does_not_match_the_manifest_leaves_nothing_behind(tmp_path):
    files = {"d1.vmdk": b"first", "d2.vmdk": b"second"}
    ovf_text = descriptor(disks=(("d1.vmdk", 10, "byte * 2^30"), ("d2.vmdk", 20, "byte * 2^30")))
    path = make_ova(tmp_path / "vm.ova", ovf_text, files, digests={"d2.vmdk": "0" * 64})
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(ova.OvaError, match="manifest"):
        ova.extract_disks(path, out, "web")
    assert list(out.iterdir()) == []


def test_an_ova_without_manifest_is_extracted_as_it_is(tmp_path):
    path = make_ova(tmp_path / "vm.ova", descriptor(), {"disk1.vmdk": b"x" * 10}, manifest=False)
    out = tmp_path / "out"
    out.mkdir()
    assert ova.extract_disks(path, out, "vm") == ["vm-disk1.vmdk"]


# --- The API ---


@pytest.fixture()
def disks_dir(monkeypatch, tmp_path):
    from app.routers import vm_disks

    monkeypatch.setattr(vm_disks, "IMPORTED_DISKS_DIR", tmp_path)
    return tmp_path


def test_an_ova_is_read_and_extracted_through_the_api(client, auth_headers, disks_dir):
    admin = auth_headers("admin")
    files = {"d1.vmdk": b"first", "d2.vmdk": b"second"}
    ovf_text = descriptor(disks=(("d1.vmdk", 10, "byte * 2^30"), ("d2.vmdk", 20, "byte * 2^30")))
    make_ova(disks_dir / "Web Server (prod).ova", ovf_text, files)

    r = client.get("/vm-disks/Web Server (prod).ova/ovf", headers=admin)
    assert r.status_code == 200, r.text
    assert [d["import_disk"] for d in r.json()["disks"]] == [
        "Web-Server--prod--disk1.vmdk",
        "Web-Server--prod--disk2.vmdk",
    ]

    r = client.post("/vm-disks/Web Server (prod).ova/extract", headers=admin)
    assert r.status_code == 200 and r.json()["disks"] == [
        "Web-Server--prod--disk1.vmdk",
        "Web-Server--prod--disk2.vmdk",
    ]
    listed = {d["name"] for d in client.get("/vm-disks", headers=admin).json()["items"]}
    assert {"Web-Server--prod--disk1.vmdk", "Web-Server--prod--disk2.vmdk"} <= listed

    r = client.post("/vm-disks/Web Server (prod).ova/extract", headers=admin)
    assert r.status_code == 422 and "exists already" in r.json()["detail"]
    assert client.get("/vm-disks/nope.ova/ovf", headers=admin).status_code == 404
    assert client.get("/vm-disks/d1.vmdk/ovf", headers=admin).status_code == 422
    viewer = auth_headers("eve", role="viewer")
    assert client.post("/vm-disks/Web Server (prod).ova/extract", headers=viewer).status_code == 403


def test_a_large_extraction_runs_as_a_task(client, auth_headers, disks_dir):
    import time

    admin = auth_headers("admin")
    make_ova(disks_dir / "big.ova", descriptor(), {"disk1.vmdk": b"x" * 1000})
    r = client.post("/vm-disks/big.ova/extract?wait=false", headers=admin)
    assert r.status_code == 202, r.text
    for _ in range(100):
        task = client.get(f"/tasks/{r.json()['task_id']}", headers=admin).json()
        if task["status"] != "running":
            break
        time.sleep(0.05)
    assert task["status"] == "succeeded" and (disks_dir / "big-disk1.vmdk").exists()


def test_an_ova_upload_is_accepted(client, auth_headers, disks_dir):
    path = make_ova(disks_dir.parent / "up.ova", descriptor(), {"disk1.vmdk": b"x"})
    r = client.post(
        "/vm-disks", files={"file": ("up.ova", path.read_bytes(), "application/x-tar")}, headers=auth_headers("admin")
    )
    assert r.status_code == 201 and (disks_dir / "up.ova").exists()


# --- A VM made from the extracted disks ---

creation = importlib.import_module("app.routers.vms.create")


@pytest.fixture()
def created(monkeypatch, tmp_path):
    conn = Mock()
    conn.lookupByName.side_effect = libvirt.libvirtError("no domain")
    conn.defineXML.return_value = Mock()
    made = []
    monkeypatch.setattr(creation, "IMPORTED_DISKS_DIR", tmp_path)
    monkeypatch.setattr(creation, "open_conn", lambda: conn)
    monkeypatch.setattr(creation.firmware, "check_choice", lambda conn, choice: None)
    monkeypatch.setattr(creation, "validate_vm_resources", lambda *a: [])
    monkeypatch.setattr(creation, "generate_mac", lambda conn: "52:54:00:00:00:01")
    monkeypatch.setattr(creation, "allocate_static_ip", lambda *a: None)
    monkeypatch.setattr(
        creation,
        "create_disk",
        lambda name, size, index, blank=False, target_dir=None: made.append(("blank", index)) or f"/i/{index}",
    )
    monkeypatch.setattr(
        creation,
        "create_disk_from_import",
        lambda name, src, target_dir=None, index=0: made.append(("import", index, src.name)) or f"/i/{index}",
    )
    monkeypatch.setattr(creation, "create_cloudinit_reseed_iso", lambda name: "/i/seed.iso")
    monkeypatch.setattr(creation, "build_domain_xml", lambda *a, **k: "<domain/>")
    monkeypatch.setattr(creation, "refresh_pools_for_paths", lambda *a: None)
    monkeypatch.setattr(creation, "_domain_summary", lambda domain: {"name": "web"})
    monkeypatch.setattr(creation, "create_task", lambda kind, *a, **k: "t")
    for name in ("set_vm_ssh_user", "set_vm_os_label", "set_vm_auto_cleanup", "log_action"):
        monkeypatch.setattr(creation, name, Mock())
    for f in ("web-disk1.vmdk", "web-disk2.vmdk"):
        (tmp_path / f).write_bytes(b"x")
    return made


def test_a_vm_takes_every_disk_of_the_ova(created):
    payload = creation.VMCreate(
        name="web",
        vcpu=4,
        memory_mb=8192,
        import_disk="web-disk1.vmdk",
        disks=[{"size_gb": 64}, {"size_gb": 200, "import_disk": "web-disk2.vmdk"}, {"size_gb": 10}],
    )
    creation._create_vm(payload, {"username": "admin"})
    assert created == [("import", 0, "web-disk1.vmdk"), ("import", 1, "web-disk2.vmdk"), ("blank", 2)]


@pytest.mark.parametrize(
    ("disks", "match"),
    [
        ([{"size_gb": 64, "import_disk": "web-disk2.vmdk"}], "first disk"),
        ([{"size_gb": 64}, {"size_gb": 1, "import_disk": "nope.vmdk"}], "not found"),
        ([{"size_gb": 64}, {"size_gb": 1, "import_disk": "../etc/passwd"}], "invalid imported disk name"),
    ],
)
def test_imported_data_disks_are_checked(created, disks, match):
    from fastapi import HTTPException

    payload = creation.VMCreate(name="web", vcpu=1, memory_mb=512, import_disk="web-disk1.vmdk", disks=disks)
    with pytest.raises(HTTPException) as e:
        creation._create_vm(payload, {"username": "admin"})
    assert e.value.status_code in (400, 422) and match in str(e.value.detail)
    assert created == []


def test_a_system_disk_name_that_leaves_the_directory_is_refused_as_a_422(created):
    from fastapi import HTTPException

    payload = creation.VMCreate(name="web", vcpu=1, memory_mb=512, import_disk="../etc/passwd", disks=[{"size_gb": 8}])
    with pytest.raises(HTTPException) as e:
        creation._create_vm(payload, {"username": "admin"})
    assert e.value.status_code == 422 and "Invalid imported disk name" in str(e.value.detail)


@pytest.mark.parametrize(
    ("guest_os", "bus", "nic"), [("windows", "sata", "e1000e"), ("linux", "scsi", "virtio"), ("auto", "scsi", "virtio")]
)
def test_an_imported_windows_vm_gets_devices_windows_boots_from(monkeypatch, created, guest_os, bus, nic):
    built = {}
    monkeypatch.setattr(creation, "build_domain_xml", lambda *a, **k: built.update(k) or "<domain/>")
    payload = creation.VMCreate(
        name="web", vcpu=2, memory_mb=4096, import_disk="web-disk1.vmdk", guest_os=guest_os, disks=[{"size_gb": 64}]
    )
    creation._create_vm(payload, {"username": "admin"})
    assert (built["disk_bus"], built["interface_model"]) == (bus, nic)
