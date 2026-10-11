"""Updating a node that has no network, from an update file published next to the ISO (scripts/build-update-file.sh,
docs/design/updates-1.0.md step 6).

The file is an uncompressed tar archive of:
- manifest.json: {"version": "<hyperlite version>", "debian": "<major>", "files": {"<path>": "<sha256>", ...}};
- manifest.json.sig: a detached signature of manifest.json by the key of the Hyperlite APT repository, which every
  node already trusts (KEYRING, written by the installation);
- hyperlite_<version>_amd64.deb;
- offline/debs/ and offline/wheels/: the Debian packages and Python wheels of the ISO's bundle
  (installer/build-offline-bundle.sh), so the node's own packages are brought up to date too;
- notes/<version>.en.md (and .fr.md): the release notes, shown before updating.

Nothing is extracted before the signature is checked, an archive member the manifest does not list is refused, and
every extracted file must match its checksum. Everything lives in WORK_DIR, outside the application directory (the
update backs that directory up first, and must not archive half a gigabyte of packages each time).
"""

import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

WORK_DIR = Path("/var/lib/hyperlite/update-file")
KEYRING = Path("/usr/share/keyrings/hyperlite-archive-keyring.gpg")
OS_RELEASE = Path("/etc/os-release")
MANIFEST = "manifest.json"
SIGNATURE = "manifest.json.sig"
MANIFEST_MAX_BYTES = 4 * 1024 * 1024
NOTES_MAX_BYTES = 512 * 1024
# A release (1.0.1), a test build (1.0.1~test.20261011.1530) or a dated version from before 1.0.0.
VERSION_RE = re.compile(r"^(\d+\.\d+\.\d+(~[a-z0-9.]+)?|\d{4}\.\d{2}\.\d{2}\.\d{4}(~test)?)$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CHUNK = 1024 * 1024


def upload_path():
    return WORK_DIR / "update.tar"


def extracted_dir():
    return WORK_DIR / "files"


class OfflineUpdateError(Exception):
    """A sentence for the administrator: what is wrong with the file."""


def _member_name_ok(name):
    return bool(name) and not name.startswith("/") and ".." not in Path(name).parts and "\\" not in name


def _read_member(tar, name, limit):
    try:
        member = tar.getmember(name)
    except KeyError:
        raise OfflineUpdateError(f"This is not a Hyperlite update file: {name} is missing") from None
    if not member.isfile() or member.size > limit:
        raise OfflineUpdateError(f"This is not a Hyperlite update file: {name} is not valid")
    return tar.extractfile(member).read()


def _check_signature(manifest, signature):
    if not KEYRING.exists():
        raise OfflineUpdateError(f"The key of the Hyperlite repository is missing on this node ({KEYRING})")
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / MANIFEST
        sig = Path(tmp) / SIGNATURE
        data.write_bytes(manifest)
        sig.write_bytes(signature)
        try:
            done = subprocess.run(
                ["gpgv", "--keyring", str(KEYRING), str(sig), str(data)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise OfflineUpdateError(f"The signature could not be checked: {e}") from None
    if done.returncode != 0:
        raise OfflineUpdateError("The signature of this update file is not valid: it was not published by Hyperlite")


def node_debian():
    """This node's Debian major release ("13"), None when unknown."""
    try:
        for line in OS_RELEASE.read_text().splitlines():
            if line.startswith("VERSION_ID="):
                return line.split("=", 1)[1].strip().strip('"').split(".")[0]
    except OSError:
        pass
    return None


def inspect(path=None):
    """What an update file brings, once its signature and its contents are checked against each other:
    {"version", "debian", "files", "notes": {"en": text, "fr": text}, "taille_mo"}."""
    path = path or upload_path()
    try:
        tar = tarfile.open(path, "r:")  # noqa: SIM115 -- closed by the with below, once opened
    except (OSError, tarfile.TarError):
        raise OfflineUpdateError("This is not a Hyperlite update file (an uncompressed tar archive)") from None
    with tar:
        manifest = _read_member(tar, MANIFEST, MANIFEST_MAX_BYTES)
        signature = _read_member(tar, SIGNATURE, 64 * 1024)
        _check_signature(manifest, signature)
        try:
            data = json.loads(manifest)
            version = str(data["version"])
            debian = str(data["debian"])
            files = {str(k): str(v) for k, v in data["files"].items()}
        except (ValueError, KeyError, TypeError, AttributeError):
            raise OfflineUpdateError("The manifest of this update file is not valid") from None
        if not VERSION_RE.match(version) or f"hyperlite_{version}_amd64.deb" not in files:
            raise OfflineUpdateError("The manifest of this update file is not valid")
        if not all(_member_name_ok(n) and SHA256_RE.match(h) for n, h in files.items()):
            raise OfflineUpdateError("The manifest of this update file is not valid")
        names = set()
        for member in tar.getmembers():
            if member.isdir() and _member_name_ok(member.name):
                continue
            if member.name in (MANIFEST, SIGNATURE):
                continue
            if not member.isfile() or member.name not in files:
                raise OfflineUpdateError(f"This update file holds an unexpected entry: {member.name}")
            names.add(member.name)
        missing = set(files) - names
        if missing:
            raise OfflineUpdateError(f"This update file is incomplete: {sorted(missing)[0]} is missing")
        notes = {}
        for lang in ("en", "fr"):
            name = f"notes/{version}.{lang}.md"
            member = tar.getmember(name) if name in files else None
            if member is not None and member.size <= NOTES_MAX_BYTES:
                text = tar.extractfile(member).read()
                if hashlib.sha256(text).hexdigest() == files[name]:
                    notes[lang] = text.decode("utf-8", "replace")
    here = node_debian()
    if here and debian != here:
        raise OfflineUpdateError(
            f"This update file is for Debian {debian}, and this node runs Debian {here}: download the file of this "
            "node's Debian release"
        )
    return {
        "version": version,
        "debian": debian,
        "files": files,
        "notes": notes,
        "taille_mo": round(Path(path).stat().st_size / (1024 * 1024), 1),
    }


def receive(fileobj):
    """Store an uploaded update file and check it; a refused file is not kept."""
    WORK_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    clean_extracted()
    partial = WORK_DIR / "update.tar.part"
    try:
        with open(partial, "wb") as out:
            shutil.copyfileobj(fileobj, out, CHUNK)
        info = inspect(partial)
        partial.replace(upload_path())
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return info


def stored():
    """The update file already received and checked, or None."""
    if not upload_path().exists():
        return None
    try:
        return inspect()
    except OfflineUpdateError:
        return None


def extract():
    """The checked contents of the received file, in extracted_dir(): (version, the hyperlite package, the bundle)."""
    info = inspect()
    clean_extracted()
    root = extracted_dir()
    root.mkdir(mode=0o700, parents=True)
    with tarfile.open(upload_path(), "r:") as tar:
        for name, expected in info["files"].items():
            dest = root / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with tar.extractfile(tar.getmember(name)) as src, open(dest, "wb") as out:
                while chunk := src.read(CHUNK):
                    digest.update(chunk)
                    out.write(chunk)
            if digest.hexdigest() != expected:
                clean_extracted()
                raise OfflineUpdateError(f"{name} does not match the signed manifest: the file is damaged")
    return info["version"], root / f"hyperlite_{info['version']}_amd64.deb", root / "offline"


def local_apt_options(bundle):
    """apt-get options that read the packages of the extracted bundle and nothing else: no network, and the node's
    own sources and package lists left as they are."""
    debs = bundle / "debs"
    shutil.copyfile(debs / "packages.index", debs / "Packages")
    lists = WORK_DIR / "lists"
    (lists / "partial").mkdir(parents=True, exist_ok=True)
    source = WORK_DIR / "local.list"
    # trusted=yes: these files were checked against the signed manifest above, apt has no signature of its own here.
    source.write_text(f"deb [trusted=yes] file:{debs} ./\n")
    return [
        "-o", f"Dir::Etc::sourcelist={source}",
        "-o", "Dir::Etc::sourceparts=-",
        "-o", f"Dir::State::Lists={lists}",
        "-o", "APT::Get::List-Cleanup=false",
    ]  # fmt: skip


def clean_extracted():
    shutil.rmtree(extracted_dir(), ignore_errors=True)
    shutil.rmtree(WORK_DIR / "lists", ignore_errors=True)


def discard():
    """Remove everything an offline update left: the received file and its contents."""
    shutil.rmtree(WORK_DIR, ignore_errors=True)
