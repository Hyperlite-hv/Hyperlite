"""Image gallery (#348): the official cloud images of a few distributions, downloaded into the imported disks library
(app/core/disk_library.py IMPORTS) once their checksum, published by the distribution next to the image, matches.

A VM is then created from the image like from any imported disk (VMCreate.import_disk), with cloud-init when the
image has it, which these all do.

The download runs as a task: written into the spool by the API while its hash is computed, checked against the
distribution's checksum list, then moved into the library by hyperlite-daemon (disk.library_store), like an upload.
A mismatch, a missing checksum or a file that is not a disk image leaves nothing behind.
"""

import hashlib
import logging
import re
import urllib.request
from datetime import UTC, datetime

from app.core import disk_library, privileged
from app.core.audit import log_action
from app.core.tasks import create_task, finish_task, start_thread, update_task_progress

logger = logging.getLogger(__name__)

# id → what it is, where it comes from and how it is checked. `checksums` is the distribution's own list; `algo` the
# hash it holds. "latest" URLs move with each point release: the list is read at download time, next to the image.
CATALOG = {
    "debian-13": {
        "name": "Debian 13 (trixie)",
        "family": "debian",
        "url": "https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2",
        "checksums": "https://cloud.debian.org/images/cloud/trixie/latest/SHA512SUMS",
        "algo": "sha512",
    },
    "debian-12": {
        "name": "Debian 12 (bookworm)",
        "family": "debian",
        "url": "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2",
        "checksums": "https://cloud.debian.org/images/cloud/bookworm/latest/SHA512SUMS",
        "algo": "sha512",
    },
    "ubuntu-24.04": {
        "name": "Ubuntu 24.04 LTS (Noble Numbat)",
        "family": "ubuntu",
        "url": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img",
        "checksums": "https://cloud-images.ubuntu.com/noble/current/SHA256SUMS",
        "algo": "sha256",
    },
    "ubuntu-22.04": {
        "name": "Ubuntu 22.04 LTS (Jammy Jellyfish)",
        "family": "ubuntu",
        "url": "https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img",
        "checksums": "https://cloud-images.ubuntu.com/jammy/current/SHA256SUMS",
        "algo": "sha256",
    },
    "rocky-9": {
        "name": "Rocky Linux 9",
        "family": "rhel",
        "url": "https://dl.rockylinux.org/pub/rocky/9/images/x86_64/Rocky-9-GenericCloud-Base.latest.x86_64.qcow2",
        "checksums": "https://dl.rockylinux.org/pub/rocky/9/images/x86_64/CHECKSUM",
        "algo": "sha256",
    },
    "almalinux-9": {
        "name": "AlmaLinux 9",
        "family": "rhel",
        "url": "https://repo.almalinux.org/almalinux/9/cloud/x86_64/images/AlmaLinux-9-GenericCloud-latest.x86_64.qcow2",
        "checksums": "https://repo.almalinux.org/almalinux/9/cloud/x86_64/images/CHECKSUM",
        "algo": "sha256",
    },
}

MAX_IMAGE_BYTES = 8 * 1024**3  # these images are 300 MB to 1 GB; anything far larger is not what was asked for
MAX_CHECKSUM_BYTES = 1024 * 1024
CHUNK = 4 * 1024 * 1024
TIMEOUT_S = 60
# The magic bytes of a qcow2 image; Ubuntu's .img files are qcow2 too.
QCOW2_MAGIC = b"QFI\xfb"


def file_name(image_id):
    """The name the image takes in the imported disks library."""
    return f"{image_id}-cloud.qcow2"


def catalog():
    """The images, each with whether it is already in the library and when it was downloaded."""
    present = {}
    if disk_library.IMPORTS.is_dir():
        for p in disk_library.IMPORTS.iterdir():
            present[p.name] = p
    out = []
    for image_id, entry in CATALOG.items():
        path = present.get(file_name(image_id))
        stat = path.stat() if path else None
        out.append(
            {
                "id": image_id,
                "name": entry["name"],
                "family": entry["family"],
                "url": entry["url"],
                "algo": entry["algo"],
                "file": file_name(image_id),
                "downloaded": bool(path),
                "size": stat.st_size if stat else None,
                "downloaded_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat() if stat else None,
            }
        )
    return out


def expected_hash(listing, filename):
    """The hash `listing` (a checksum file) gives for `filename`: GNU style ("<hash>  name" or "<hash> *name") or BSD
    style ("SHA256 (name) = <hash>"). None when the file is not listed."""
    for line in listing.splitlines():
        line = line.strip()
        bsd = re.match(r"^[A-Z0-9-]+ \((.+)\) = ([0-9a-fA-F]+)$", line)
        if bsd and bsd.group(1) == filename:
            return bsd.group(2).lower()
        gnu = re.match(r"^([0-9a-fA-F]{64,128}) [ *]?(.+)$", line)
        if gnu and gnu.group(2).strip() == filename:
            return gnu.group(1).lower()
    return None


def _open(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Hyperlite image gallery"})  # noqa: S310 -- fixed https URLs
    return urllib.request.urlopen(request, timeout=TIMEOUT_S)  # noqa: S310


def _read_checksums(entry):
    with _open(entry["checksums"]) as r:
        return r.read(MAX_CHECKSUM_BYTES).decode("utf-8", errors="replace")


def start_download(image_id, username):
    """Starts the download of a gallery image as a task; returns its id. KeyError for an unknown image."""
    entry = CATALOG[image_id]
    task_id = create_task("gallery_download", file_name(image_id), username=username)
    start_thread(_download, image_id, entry, username, task_id, task_id=task_id, name=f"gallery-{image_id}")
    return task_id


def _download(image_id, entry, username, task_id):
    name = file_name(image_id)
    spool_name, partial = disk_library.spool_file()
    try:
        filename = entry["url"].rsplit("/", 1)[1]
        want = expected_hash(_read_checksums(entry), filename)
        if not want:
            raise RuntimeError(f"{filename} is not in the distribution's checksum list")
        digest = hashlib.new(entry["algo"])
        done = 0
        with _open(entry["url"]) as r, open(partial, "wb") as out:
            total = int(r.headers.get("Content-Length") or 0)
            head = b""
            while chunk := r.read(CHUNK):
                if not head:
                    head = chunk[:4]
                done += len(chunk)
                if done > MAX_IMAGE_BYTES:
                    raise RuntimeError("The image is larger than expected")
                digest.update(chunk)
                out.write(chunk)
                if total:
                    update_task_progress(task_id, min(99, int(done * 100 / total)))
        if head != QCOW2_MAGIC:
            raise RuntimeError("The download is not a qcow2 disk image")
        if digest.hexdigest() != want:
            raise RuntimeError(f"The {entry['algo']} checksum does not match the distribution's")
        privileged.run(
            "disk.library_store", user=username, library="import", spool_name=spool_name, name=name, replace=True
        )
        finish_task(task_id, "succeeded")
        log_action(
            username, "gallery_download", name, "success", f"{entry['name']}, {done} bytes, {entry['algo']} checked"
        )
    except Exception as e:
        detail = getattr(e, "detail", None) or str(e) or e.__class__.__name__
        finish_task(task_id, "failed", detail)
        log_action(username, "gallery_download", name, "failed", detail)
    finally:
        partial.unlink(missing_ok=True)
