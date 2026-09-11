import re
import subprocess
import uuid
from pathlib import Path

IMAGES_DIR = Path("/var/lib/libvirt/images")
BASE_IMAGE = IMAGES_DIR / "base" / "debian-12-generic-amd64.qcow2"
BASE_IMAGE_URL = "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2"

NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]{1,62}$")


def validate_name(name):
    if not NAME_RE.match(name):
        return "Nom de VM invalide (lettres/chiffres/tirets, 2-63 caracteres, doit commencer par une lettre ou un chiffre)"
    return None


def ensure_base_image():
    BASE_IMAGE.parent.mkdir(parents=True, exist_ok=True)
    if not BASE_IMAGE.exists():
        subprocess.run(
            ["wget", "-q", "-O", str(BASE_IMAGE), BASE_IMAGE_URL],
            check=True,
        )
    return BASE_IMAGE


def create_disk(vm_name, disk_gb):
    ensure_base_image()
    disk_path = IMAGES_DIR / f"{vm_name}.qcow2"
    subprocess.run(
        [
            "qemu-img", "create", "-f", "qcow2",
            "-F", "qcow2", "-b", str(BASE_IMAGE),
            str(disk_path), f"{disk_gb}G",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return disk_path


def create_cloudinit_iso(vm_name, password=None):
    workdir = Path(f"/tmp/hyperlite-cloudinit-{vm_name}")
    workdir.mkdir(exist_ok=True)
    user_data = workdir / "user-data"
    meta_data = workdir / "meta-data"

    pwd = password or "hyperlite"
    ud = [
        "#cloud-config",
        f"hostname: {vm_name}",
        "manage_etc_hosts: true",
        "users:",
        "  - name: hyperlite",
        "    sudo: ALL=(ALL) NOPASSWD:ALL",
        "    shell: /bin/bash",
        f"    plain_text_passwd: '{pwd}'",
        "    lock_passwd: false",
        "chpasswd:",
        "  expire: false",
        "ssh_pwauth: true",
    ]
    user_data.write_text("\n".join(ud) + "\n")
    meta_data.write_text(f"instance-id: {vm_name}-{uuid.uuid4()}\nlocal-hostname: {vm_name}\n")

    iso_path = IMAGES_DIR / f"{vm_name}-cloudinit.iso"
    subprocess.run(
        ["cloud-localds", str(iso_path), str(user_data), str(meta_data)],
        check=True,
        capture_output=True,
        text=True,
    )
    return iso_path


def build_domain_xml(vm_name, vcpu, memory_mb, disk_path, cloudinit_path, network="default"):
    return f"""
<domain type='kvm'>
  <name>{vm_name}</name>
  <memory unit='MiB'>{memory_mb}</memory>
  <currentMemory unit='MiB'>{memory_mb}</currentMemory>
  <vcpu placement='static'>{vcpu}</vcpu>
  <os>
    <type arch='x86_64' machine='pc-i440fx'>hvm</type>
    <boot dev='hd'/>
  </os>
  <features>
    <acpi/>
    <apic/>
  </features>
  <cpu mode='host-model'/>
  <clock offset='utc'/>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>destroy</on_crash>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='{disk_path}'/>
      <target dev='vda' bus='virtio'/>
    </disk>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='{cloudinit_path}'/>
      <target dev='sda' bus='sata'/>
      <readonly/>
    </disk>
    <interface type='network'>
      <source network='{network}'/>
      <model type='virtio'/>
    </interface>
    <console type='pty'/>
    <channel type='unix'>
      <target type='virtio' name='org.qemu.guest_agent.0'/>
    </channel>
    <graphics type='vnc' port='-1' autoport='yes' listen='127.0.0.1'/>
  </devices>
</domain>
"""
