"""Backup integrity: a manifest per backup, verification on demand and every week, and the firmware state of UEFI
VMs (NVRAM and TPM) saved and put back with the disks.

Manifest (`manifest.json` next to the files of a backup):
  {"version": 1, "vm": ..., "source": host name, "mode": "chaud"|"froid", "cree_le": ..., "firmware": "bios"|"uefi"|"uefi_secure",
   "fichiers": [{"nom": "sda.qcow2", "role": "disque", "cible": "sda", "taille": ..., "sha256": ...},
                {"nom": "nvram.fd", "role": "nvram", ...}, {"nom": "tpm.tar", "role": "tpm", ...},
                {"nom": "vm-config.json", "role": "config", ...}]}

Verification recomputes every checksum and runs `qemu-img check` on each disk image. A backup is then
"verifie" (every file present, same checksum, images consistent) or "corrompu" (with what is wrong), with the date.
A backup made before manifests existed is checked with what it has: its images, and its single-disk checksum.

UEFI firmware state:
  - NVRAM (`<os><nvram>` of the VM, /var/lib/libvirt/qemu/nvram/<name>_VARS.fd by default): the boot entries and
    the enrolled Secure Boot keys;
  - TPM (/var/lib/libvirt/swtpm/<uuid>/): what the guest sealed in its TPM, BitLocker keys among them.
Both are copied at backup time. On a running VM they are read while QEMU and swtpm may write them; both change
rarely (a boot entry, a key sealed), so the copy is what the guest had moments before, like the disks of a hot backup.
On restore they are put back: in place for the same VM, or under the new VM's name and UUID.
"""

import hashlib
import json
import logging
import shutil
import socket
import subprocess
import tarfile
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import libvirt

from app.core import firmware

logger = logging.getLogger(__name__)

MANIFEST = "manifest.json"
MANIFEST_VERSION = 1
NVRAM_FILE = "nvram.fd"
TPM_FILE = "tpm.tar"
# Overridable in tests.
NVRAM_DIR = Path("/var/lib/libvirt/qemu/nvram")
SWTPM_DIR = Path("/var/lib/libvirt/swtpm")
VERIFY_EVERY_DAYS = 7

VERIFIED = "verifie"
CORRUPT = "corrompu"


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _now():
    return datetime.now(UTC).isoformat()


# --- Firmware state ---------------------------------------------------------------------------------------------


def nvram_path(root):
    """The VM's NVRAM file from its persistent XML, or libvirt's default path when it has not written one yet."""
    if firmware.of_domain(root) == firmware.BIOS:
        return None
    text = (root.findtext("os/nvram") or "").strip()
    return Path(text) if text else NVRAM_DIR / f"{root.findtext('name')}_VARS.fd"


def tpm_dir(uuid):
    return SWTPM_DIR / uuid


def save_firmware_state(domain, dest_dir):
    """Copy the VM's NVRAM and TPM state into the backup directory. Returns the file names written."""
    root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    written = []
    nvram = nvram_path(root)
    if nvram is not None and nvram.is_file():
        shutil.copyfile(nvram, dest_dir / NVRAM_FILE)
        written.append(NVRAM_FILE)
    state = tpm_dir(domain.UUIDString())
    if firmware.has_tpm(root) and state.is_dir():
        with tarfile.open(dest_dir / TPM_FILE, "w") as tar:
            for item in sorted(state.rglob("*")):
                if item.name == ".lock":  # swtpm's runtime lock, recreated when it starts
                    continue
                tar.add(item, arcname=str(item.relative_to(state)), recursive=False)
        written.append(TPM_FILE)
    return written


def restore_firmware_state(src_dir, domain):
    """Put a backup's NVRAM and TPM state back for `domain` (stopped). Returns what was restored."""
    src_dir = Path(src_dir)
    root = ET.fromstring(domain.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    restored = []
    nvram = nvram_path(root)
    if nvram is not None and (src_dir / NVRAM_FILE).is_file():
        nvram.parent.mkdir(parents=True, exist_ok=True)
        # The file keeps the owner of the one it replaces, or of the NVRAM directory for a new VM, and stays
        # private to that owner like the files libvirt creates.
        owner = (nvram if nvram.exists() else nvram.parent).stat()
        shutil.copyfile(src_dir / NVRAM_FILE, nvram)
        nvram.chmod(0o600)
        shutil.chown(nvram, owner.st_uid, owner.st_gid)
        restored.append("nvram")
    if firmware.has_tpm(root) and (src_dir / TPM_FILE).is_file():
        state = tpm_dir(domain.UUIDString())
        if state.exists():
            shutil.rmtree(state)
        state.mkdir(parents=True, mode=0o711)  # as libvirt creates it: traversable, not listable
        with tarfile.open(src_dir / TPM_FILE) as tar:
            # "tar" filter: owners and modes are kept (swtpm runs as its own user), paths outside the directory,
            # absolute paths and device files are refused.
            tar.extractall(state, filter="tar")
        restored.append("tpm")
    return restored


# --- Manifest ---------------------------------------------------------------------------------------------------


def write_manifest(dest_dir, vm_name, mode, firmware_kind, disks):
    """disks: [(target dev, Path of the copied image)]. Every other file of the backup is listed by its role."""
    dest_dir = Path(dest_dir)
    files = []
    for dev, path in disks:
        files.append({"nom": Path(path).name, "role": "disque", "cible": dev})
    for name, role in ((NVRAM_FILE, "nvram"), (TPM_FILE, "tpm"), ("vm-config.json", "config")):
        if (dest_dir / name).is_file():
            files.append({"nom": name, "role": role})
    for entry in files:
        path = dest_dir / entry["nom"]
        entry["taille"] = path.stat().st_size
        entry["sha256"] = sha256_of(path)
    manifest = {
        "version": MANIFEST_VERSION,
        "vm": vm_name,
        # The host that made it: another site restoring this backup (app/core/site_recovery.py) shows where it
        # comes from.
        "source": socket.gethostname(),
        "mode": mode,
        "cree_le": _now(),
        "firmware": firmware_kind,
        "fichiers": files,
    }
    (dest_dir / MANIFEST).write_text(json.dumps(manifest, indent=2))
    return manifest


def read_manifest(src_dir):
    try:
        return json.loads((Path(src_dir) / MANIFEST).read_text())
    except (OSError, ValueError):
        return None


# --- Verification -----------------------------------------------------------------------------------------------


def _qemu_img_check(path):
    """None if the image is consistent, else what qemu-img reports."""
    try:
        proc = subprocess.run(["qemu-img", "check", "-q", str(path)], capture_output=True, text=True, timeout=3600)
    except (OSError, subprocess.SubprocessError) as e:
        return f"qemu-img check could not run: {e}"
    if proc.returncode == 0:
        return None
    return (proc.stderr or proc.stdout or f"exit code {proc.returncode}").strip()[:300]


def verify(src_dir, single_checksum=None, progress=lambda pct: None):
    """Check a backup directory. Returns (status, problems, checked): status VERIFIED or CORRUPT, problems a list
    of sentences, checked the number of files checked."""
    src_dir = Path(src_dir)
    problems = []
    manifest = read_manifest(src_dir)
    if not src_dir.is_dir():
        return CORRUPT, ["The backup directory no longer exists"], 0
    if manifest is None:
        # A backup made before manifests: check the images, and the one checksum it may have.
        images = sorted(src_dir.glob("*.qcow2"))
        if not images:
            return CORRUPT, ["No disk image in the backup"], 0
        for i, image in enumerate(images):
            error = _qemu_img_check(image)
            if error:
                problems.append(f"{image.name}: {error}")
            progress(int(100 * (i + 1) / len(images)))
        if single_checksum and len(images) == 1 and sha256_of(images[0]) != single_checksum:
            problems.append(f"{images[0].name}: checksum differs from the one recorded at backup time")
        return (CORRUPT if problems else VERIFIED), problems, len(images)

    entries = manifest.get("fichiers") or []
    if not any(e.get("role") == "disque" for e in entries):
        problems.append("The manifest lists no disk")
    for i, entry in enumerate(entries):
        name = Path(str(entry.get("nom", ""))).name  # a manifest never points outside its own directory
        path = src_dir / name
        if not name or not path.is_file():
            problems.append(f"{name or '?'}: missing")
            continue
        if path.stat().st_size != entry.get("taille"):
            problems.append(f"{name}: size differs ({path.stat().st_size} instead of {entry.get('taille')} bytes)")
        elif sha256_of(path) != entry.get("sha256"):
            problems.append(f"{name}: checksum differs, the file changed since the backup")
        if entry.get("role") == "disque":
            error = _qemu_img_check(path)
            if error:
                problems.append(f"{name}: {error}")
        progress(int(100 * (i + 1) / max(len(entries), 1)))
    return (CORRUPT if problems else VERIFIED), problems, len(entries)
