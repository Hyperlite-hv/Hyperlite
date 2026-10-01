"""Cloud-init after creation: change the account's password and SSH keys of a VM made from a cloud image.

The VM keeps its cloud-init drive (<name>-cloudinit.iso). Writing a new one with a new instance id makes cloud-init
apply it at the next boot, as Proxmox does with its regenerated drive. What must not change with it:
- the guest's SSH host keys: `ssh_deletekeys: false`, otherwise every client would see a changed host key;
- Hyperlite's automation key, which automation jobs use to reach the VM: always kept in the keys.
A password goes through `chpasswd` (applied to an existing account; the `users` module only creates accounts) and is
never stored by Hyperlite: the drive holds it until the next change, like at creation.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import libvirt

from app.core.safe_paths import safe_child
from app.core.vm_builder import IMAGES_DIR, get_or_create_automation_pubkey


def _store():
    from app.repositories import registry

    return registry.objects().sync


USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
KEY_RE = re.compile(
    r"^(ssh-(rsa|ed25519|dss)|ecdsa-sha2-nistp(256|384|521)|sk-(ssh-ed25519|ecdsa-sha2-nistp256)@openssh\.com) [A-Za-z0-9+/=]+( [^\r\n]{0,200})?$"
)
MAX_KEYS = 20


class CloudInitError(ValueError):
    pass


def _drive(domain, vm_name):
    expected = safe_child(IMAGES_DIR, f"{vm_name}-cloudinit.iso")
    root = ET.fromstring(domain.XMLDesc(0))
    for disk in root.findall("./devices/disk[@device='cdrom']"):
        source = disk.find("source")
        if source is not None and source.get("file") == str(expected):
            return expected, disk
    return None, None


def drive_path(domain, vm_name):
    """The VM's cloud-init drive when it has one Hyperlite made, else None (a VM installed from an ISO)."""
    return _drive(domain, vm_name)[0]


def reload_media(domain, vm_name):
    """A running VM keeps the old drive open, even across a guest reboot (the QEMU process stays): eject it and
    insert the new file, so the next boot of the guest reads it."""
    if not domain.isActive():
        return False
    path, disk = _drive(domain, vm_name)
    if disk is None:
        return False
    disk.remove(disk.find("source"))
    domain.updateDeviceFlags(ET.tostring(disk, encoding="unicode"), libvirt.VIR_DOMAIN_AFFECT_LIVE)
    ET.SubElement(disk, "source", {"file": str(path)})
    domain.updateDeviceFlags(ET.tostring(disk, encoding="unicode"), libvirt.VIR_DOMAIN_AFFECT_LIVE)
    return True


def get_state(vm_name):
    """What Hyperlite last wrote: {"utilisateur", "cles_ssh", "modifie_le"} (no password: never stored)."""
    row = _store().cloudinit(vm_name)
    if not row:
        return None
    return {
        "utilisateur": row["username"],
        "cles_ssh": json.loads(row["ssh_keys"] or "[]"),
        "modifie_le": row["updated_at"],
    }


def _save_state(vm_name, username, keys):
    _store().save_cloudinit(vm_name, username, json.dumps(keys), datetime.now(UTC).isoformat())


def delete_state(vm_name):
    _store().delete_cloudinit(vm_name)


def validate(username, password, keys):
    if not USER_RE.match(username or ""):
        raise CloudInitError("Invalid user name: lowercase letters, digits, '_' or '-', up to 32 characters")
    if password is not None:
        if len(password) < 8:
            raise CloudInitError("The password needs at least 8 characters")
        if any(c in password for c in "\r\n"):
            raise CloudInitError("The password must not contain a line break")
    cleaned = []
    for raw in keys or []:
        if any(c in str(raw).strip() for c in "\r\n"):
            # Two keys pasted as one entry: refused rather than merged into one odd key.
            raise CloudInitError("One SSH key per entry: this one spans several lines")
        key = " ".join(str(raw).split())
        if not key:
            continue
        if not KEY_RE.match(key):
            raise CloudInitError(f"Not an SSH public key: {key[:40]}…")
        if key not in cleaned:
            cleaned.append(key)
    if len(cleaned) > MAX_KEYS:
        raise CloudInitError(f"At most {MAX_KEYS} SSH keys")
    return cleaned


def _yaml_str(value):
    """A single-quoted YAML scalar (quotes doubled): nothing typed by a user is interpreted as YAML."""
    return "'" + str(value).replace("'", "''") + "'"


def user_data(vm_name, username, password, keys, automation_key):
    all_keys = [*keys, *([automation_key] if automation_key and automation_key not in keys else [])]
    lines = [
        "#cloud-config",
        f"hostname: {_yaml_str(vm_name)}",
        "manage_etc_hosts: true",
        # A new instance id makes cloud-init run again; the host keys must stay those clients already know.
        "ssh_deletekeys: false",
        "users:",
        f"  - name: {_yaml_str(username)}",
        "    sudo: ALL=(ALL) NOPASSWD:ALL",
        "    shell: /bin/bash",
        "    lock_passwd: false",
    ]
    if all_keys:
        lines.append("    ssh_authorized_keys:")
        lines += [f"      - {_yaml_str(k)}" for k in all_keys]
    if password is not None:
        lines += [
            "chpasswd:",
            "  expire: false",
            "  users:",
            f"    - name: {_yaml_str(username)}",
            f"      password: {_yaml_str(password)}",
            "      type: text",
            "ssh_pwauth: true",
        ]
    return "\n".join(lines) + "\n"


def rewrite_drive(vm_name, iso_path, username, password, keys):
    """Write the new drive next to the old one, then swap them: a failure leaves the old drive in place."""
    automation_key = get_or_create_automation_pubkey()
    workdir = Path(tempfile.mkdtemp(prefix="hyperlite-cloudinit-edit-"))
    try:
        (workdir / "user-data").write_text(user_data(vm_name, username, password, keys, automation_key))
        (workdir / "meta-data").write_text(f"instance-id: {vm_name}-{uuid.uuid4()}\nlocal-hostname: {vm_name}\n")
        tmp_iso = iso_path.with_name(f".{iso_path.name}.new")
        result = subprocess.run(
            ["cloud-localds", str(tmp_iso), str(workdir / "user-data"), str(workdir / "meta-data")],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            tmp_iso.unlink(missing_ok=True)
            raise CloudInitError(f"Cannot build the cloud-init drive: {result.stderr.strip()[:300]}")
        if iso_path.exists():
            shutil.copymode(iso_path, tmp_iso)
            st = iso_path.stat()
            os.chown(tmp_iso, st.st_uid, st.st_gid)
        tmp_iso.replace(iso_path)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    _save_state(vm_name, username, keys)
