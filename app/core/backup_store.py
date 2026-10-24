"""The backup store (docs/design/backup-store.md): disks cut in fixed-size chunks named by their digest, as Proxmox
Backup Server's datastore. A chunk held once serves every backup of every VM that has the same content, and a backup
is an index of chunks: a complete image, never a delta of another, so any backup is deleted or restored alone.

    <store>/.store.json                  the store's id, format and chunk size, whether it is encrypted
    <store>/.chunks/<4 hex>/<64 hex>     the chunks, as blobs (below), by the first 4 hex digits of their digest
    <store>/vm/<vm uuid>/<time>/         one backup: an index per disk (<name>.fidx), its manifest written last
    <store>/.lock                        held by garbage collection on this node (the cluster's lock is the caller's)

A chunk's digest is the SHA-256 of its content; in an encrypted store, an HMAC-SHA-256 keyed from the store's key, so
a digest tells nothing of the content to whoever reads the share. A blob is a 16-byte header (magic, format, flags,
the content's length, a CRC-32 of what follows) and the content, compressed with zstd when that makes it smaller,
encrypted with AES-256-GCM when the store is (a random nonce, the digest as associated data: a chunk cannot be passed
off under another digest). A chunk of zeros is never stored: its index entry is ZERO.

Every file is written under a temporary name then renamed, which is atomic on a local file system and on an NFS
server, so a reader never sees half of one; a chunk already held is never written again, only touched (its access
time), which garbage collection reads.
"""

import base64
import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import struct
import threading
import time
import uuid
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import zstandard
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

FORMAT = 1
CHUNK_SIZE = 4 * 1024 * 1024
ZERO = bytes(32)  # the index entry of a chunk of zeros, never stored
CONFIG = ".store.json"
CHUNKS = ".chunks"
# What garbage collection never deletes: a chunk touched less than this before it began. A backup running meanwhile
# touches every chunk it uses, even one it found already there (PBS keeps 24 h 5 min for the same reason).
GC_GRACE_S = 24 * 3600 + 300

_MAGIC = b"HLBK"
_HEADER = struct.Struct(">4sBBHII")  # magic, format, flags, reserved, content length, CRC-32 of the rest
_COMPRESSED, _ENCRYPTED = 1, 2
_NONCE = 12
_INDEX_MAGIC = b"HLFIDX01"
_FILE_MAGIC = b"HLBF1"
_INDEX_HEADER = struct.Struct(">8sQII")  # magic, disk size, chunk size, number of chunks
_ZSTD_LEVEL = 1


class StoreError(Exception):
    """The store cannot be used: not a store, another format, a share that does not keep access times. `message`:
    the sentence for the person who asked."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class DamagedChunk(Exception):
    """A chunk does not match its digest, its CRC or its tag: what verification reports."""

    def __init__(self, digest, reason):
        super().__init__(f"chunk {digest.hex()}: {reason}")
        self.digest = digest


class MissingChunk(Exception):
    def __init__(self, digest):
        super().__init__(f"chunk {digest.hex()} is missing")
        self.digest = digest


def _now_iso():
    return datetime.now(UTC).isoformat(timespec="seconds")


def _write_atomic(path, data):
    """`data` at `path`, whole or not at all: written under a temporary name in the same directory, then renamed."""
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise


def _derive(key, purpose):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"hyperlite-backup-store " + purpose).derive(key)


@dataclass(frozen=True)
class Index:
    """A disk of a backup: its size in bytes and its chunks' digests, in order (ZERO for a chunk of zeros)."""

    size: int
    chunk_size: int
    digests: tuple

    def encode(self):
        body = _INDEX_HEADER.pack(_INDEX_MAGIC, self.size, self.chunk_size, len(self.digests)) + b"".join(self.digests)
        return body + hashlib.sha256(body).digest()

    @classmethod
    def decode(cls, raw):
        if len(raw) < _INDEX_HEADER.size + 32:
            raise ValueError("index too short")
        body, checksum = raw[:-32], raw[-32:]
        if not hmac.compare_digest(hashlib.sha256(body).digest(), checksum):
            raise ValueError("index checksum does not match")
        magic, size, chunk_size, count = _INDEX_HEADER.unpack_from(body)
        if magic != _INDEX_MAGIC:
            raise ValueError("not an index")
        if chunk_size <= 0 or len(body) != _INDEX_HEADER.size + 32 * count or count != -(-size // chunk_size):
            raise ValueError("index sizes do not match")
        start = _INDEX_HEADER.size
        return cls(size, chunk_size, tuple(body[start + 32 * i : start + 32 * (i + 1)] for i in range(count)))


class Store:
    """A store opened at `root`, with its key when it is encrypted (32 bytes)."""

    def __init__(self, root, key=None):
        self.root = Path(root)
        try:
            self.config = json.loads((self.root / CONFIG).read_text())
        except FileNotFoundError:
            raise StoreError(f"{self.root} is not a backup store") from None
        except ValueError as e:
            raise StoreError(f"{self.root}: unreadable store configuration: {e}") from None
        if self.config.get("format") != FORMAT:
            raise StoreError(f"{self.root}: store format {self.config.get('format')} is not supported")
        self.chunk_size = int(self.config["chunk_size"])
        self.encrypted = bool(self.config.get("encrypted"))
        if self.encrypted:
            if key is None:
                raise StoreError(f"{self.root} is encrypted: its key is needed")
            if not hmac.compare_digest(fingerprint(key), self.config.get("key_fingerprint", "")):
                raise StoreError(f"{self.root}: this is not the store's key")
            self._id_key, self._aead = _derive(key, b"id"), AESGCM(_derive(key, b"chunk"))
            self._file_aead = AESGCM(_derive(key, b"file"))
        self._key = key
        self._local = threading.local()  # zstd's contexts are not shared between threads

    @property
    def id(self):
        return self.config["id"]

    # ---- Chunks ----

    def digest(self, content):
        if self.encrypted:
            return hmac.new(self._id_key, content, hashlib.sha256).digest()
        return hashlib.sha256(content).digest()

    def chunk_path(self, digest):
        name = digest.hex()
        return self.root / CHUNKS / name[:4] / name

    def _zstd(self):
        local = self._local
        if not hasattr(local, "c"):
            local.c, local.d = zstandard.ZstdCompressor(level=_ZSTD_LEVEL), zstandard.ZstdDecompressor()
        return local

    def encode(self, digest, content):
        flags, payload = 0, content
        packed = self._zstd().c.compress(content)
        if len(packed) < len(content):
            flags, payload = _COMPRESSED, packed
        if self.encrypted:
            nonce = os.urandom(_NONCE)
            flags |= _ENCRYPTED
            payload = nonce + self._aead.encrypt(nonce, payload, digest)
        header = _HEADER.pack(_MAGIC, FORMAT, flags, 0, len(content), zlib.crc32(payload))
        return header + payload

    def decode(self, digest, blob):
        if len(blob) < _HEADER.size:
            raise DamagedChunk(digest, "too short")
        magic, fmt, flags, _, length, crc = _HEADER.unpack_from(blob)
        payload = blob[_HEADER.size :]
        if magic != _MAGIC or fmt != FORMAT:
            raise DamagedChunk(digest, "not a chunk of this format")
        if zlib.crc32(payload) != crc:
            raise DamagedChunk(digest, "CRC does not match")
        if bool(flags & _ENCRYPTED) != self.encrypted:
            raise DamagedChunk(digest, "encrypted in an unencrypted store, or the other way")
        if self.encrypted:
            try:
                payload = self._aead.decrypt(payload[:_NONCE], payload[_NONCE:], digest)
            except Exception:  # InvalidTag: tampered with, or moved under another digest
                raise DamagedChunk(digest, "authentication failed") from None
        try:
            content = self._zstd().d.decompress(payload, max_output_size=length) if flags & _COMPRESSED else payload
        except zstandard.ZstdError as e:
            raise DamagedChunk(digest, f"cannot be decompressed: {e}") from None
        if len(content) != length or not hmac.compare_digest(self.digest(content), digest):
            raise DamagedChunk(digest, "content does not match its digest")
        return content

    def put(self, content):
        """Store one chunk; returns its digest (ZERO for zeros, which is not stored). A chunk already held is only
        touched, so garbage collection keeps it for the backup that uses it."""
        if not content.strip(b"\0"):
            return ZERO
        digest = self.digest(content)
        path = self.chunk_path(digest)
        try:
            os.utime(path)
            return digest
        except FileNotFoundError:
            pass
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(path, self.encode(digest, content))
        return digest

    def touch(self, digest):
        """Keep a chunk an index reuses without reading it (an incremental backup). MissingChunk when it is gone."""
        if digest == ZERO:
            return
        try:
            os.utime(self.chunk_path(digest))
        except FileNotFoundError:
            raise MissingChunk(digest) from None

    def get(self, digest, size=None):
        """A chunk's content; `size` for a chunk of zeros (the last one of a disk may be shorter)."""
        if digest == ZERO:
            return bytes(self.chunk_size if size is None else size)
        try:
            blob = self.chunk_path(digest).read_bytes()
        except FileNotFoundError:
            raise MissingChunk(digest) from None
        return self.decode(digest, blob)

    # ---- Disks ----

    def write_disk(self, read, size):
        """Cut a disk of `size` bytes in chunks, `read(offset, length)` giving its bytes; returns its Index."""
        digests = []
        for offset in range(0, size, self.chunk_size):
            length = min(self.chunk_size, size - offset)
            content = read(offset, length)
            if len(content) != length:
                raise OSError(f"short read at {offset}: {len(content)} of {length} bytes")
            digests.append(self.put(content))
        return Index(size, self.chunk_size, tuple(digests))

    def read_disk(self, index, write):
        """Every byte of a disk, in order: `write(offset, data)` for each chunk (zeros included)."""
        for i, digest in enumerate(index.digests):
            offset = i * index.chunk_size
            write(offset, self.get(digest, min(index.chunk_size, index.size - offset)))

    # ---- Backups ----

    def backup_dir(self, vm_uuid, stamp):
        return self.root / "vm" / str(uuid.UUID(vm_uuid)) / stamp

    def write_backup(self, vm_uuid, stamp, indexes, manifest, files=None):
        """Record a backup once its chunks are stored: one index per disk ({name: Index}), small files kept whole
        (the NVRAM, the TPM state), then the manifest, last. Until the manifest is there, the backup does not exist."""
        for index in indexes.values():
            for digest in set(index.digests) - {ZERO}:
                if not self.chunk_path(digest).exists():  # swept by a garbage collection that raced the backup
                    raise MissingChunk(digest)
        directory = self.backup_dir(vm_uuid, stamp)
        directory.mkdir(parents=True, exist_ok=False)
        listed = {}
        for name, index in indexes.items():
            raw = index.encode()
            _write_atomic(directory / f"{_safe(name)}.fidx", raw)
            listed[name] = {"size": index.size, "sha256": hashlib.sha256(raw).hexdigest()}
        kept = {}
        for name, data in (files or {}).items():
            _write_atomic(directory / _safe(name), self._seal_file(vm_uuid, stamp, name, data))
            kept[name] = {"encrypted": self.encrypted}
        manifest = {**manifest, "store": self.id, "vm_uuid": str(uuid.UUID(vm_uuid)), "time": stamp, "disks": listed}
        manifest["files"] = kept
        _write_atomic(directory / "manifest.json", json.dumps(manifest, indent=1).encode())
        return directory

    def _file_aad(self, vm_uuid, stamp, name):
        # A file is bound to its backup and its name: one cannot be passed off as another's NVRAM or configuration.
        return f"{uuid.UUID(vm_uuid)}/{stamp}/{name}".encode()

    def _seal_file(self, vm_uuid, stamp, name, data):
        if not self.encrypted:
            return data
        nonce = os.urandom(_NONCE)
        return _FILE_MAGIC + nonce + self._file_aead.encrypt(nonce, data, self._file_aad(vm_uuid, stamp, name))

    def read_file(self, vm_uuid, stamp, name):
        """A file kept whole with a backup (its NVRAM, its TPM state, its configuration), decrypted. A file written
        before files were encrypted, or by an unencrypted store, is read as it is."""
        raw = (self.backup_dir(vm_uuid, stamp) / _safe(name)).read_bytes()
        if not self.encrypted:
            return raw
        if not raw.startswith(_FILE_MAGIC):
            raise DamagedChunk(b"", f"{name} is not encrypted in an encrypted store")
        body = raw[len(_FILE_MAGIC) :]
        try:
            return self._file_aead.decrypt(body[:_NONCE], body[_NONCE:], self._file_aad(vm_uuid, stamp, name))
        except Exception:  # InvalidTag
            raise DamagedChunk(b"", f"{name}: authentication failed") from None

    def sealed_key(self, public_pem):
        """The store's key encrypted with a master recovery key's public half (RSA-OAEP, SHA-256), as PBS writes it
        into each backup: with the private half, a backup is read again even when every node and secret is lost."""
        public = serialization.load_pem_public_key(public_pem)
        sealed = public.encrypt(
            self._key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
        )
        return base64.b64encode(sealed).decode()

    def backups(self, vm_uuid=None):
        """The complete backups, as (vm uuid, time, manifest), oldest first."""
        out = []
        base = self.root / "vm"
        vms = [base / str(uuid.UUID(vm_uuid))] if vm_uuid else sorted(base.iterdir()) if base.is_dir() else []
        for vm in vms:
            if not vm.is_dir():
                continue
            for backup in sorted(vm.iterdir()):
                try:
                    out.append((vm.name, backup.name, json.loads((backup / "manifest.json").read_text())))
                except (FileNotFoundError, NotADirectoryError, ValueError):
                    continue  # a backup that stopped midway: its chunks go at the next garbage collection
        return out

    def index_of(self, vm_uuid, stamp, disk):
        directory = self.backup_dir(vm_uuid, stamp)
        manifest = json.loads((directory / "manifest.json").read_text())
        raw = (directory / f"{_safe(disk)}.fidx").read_bytes()
        if hashlib.sha256(raw).hexdigest() != manifest["disks"][disk]["sha256"]:
            raise ValueError(f"index of {disk} does not match the manifest")
        return Index.decode(raw)

    def delete_backup(self, vm_uuid, stamp):
        """Remove a backup's index and files; its chunks go at the next garbage collection. The manifest goes first,
        so a deletion that stops midway leaves no backup listed with indexes missing."""
        directory = self.backup_dir(vm_uuid, stamp)
        with contextlib.suppress(FileNotFoundError):
            (directory / "manifest.json").unlink()
        for path in sorted(directory.rglob("*"), reverse=True):
            path.rmdir() if path.is_dir() else path.unlink()
        directory.rmdir()

    def verify(self, vm_uuid, stamp):
        """Read every chunk of a backup and check it. Returns the damaged or missing chunks' digests ([] when sound);
        a damaged chunk is renamed .bad, so the next backup that holds its content writes it again, which repairs
        every backup that uses it."""
        manifest = json.loads((self.backup_dir(vm_uuid, stamp) / "manifest.json").read_text())
        bad, checked = [], set()
        for disk in manifest["disks"]:
            index = self.index_of(vm_uuid, stamp, disk)
            for i, digest in enumerate(index.digests):
                if digest in checked:
                    continue
                checked.add(digest)
                try:
                    self.get(digest, min(index.chunk_size, index.size - i * index.chunk_size))
                except MissingChunk:
                    bad.append(digest)
                except DamagedChunk:
                    bad.append(digest)
                    path = self.chunk_path(digest)
                    with contextlib.suppress(FileNotFoundError):
                        os.replace(path, path.with_name(path.name + ".bad"))
        return bad

    # ---- Garbage collection ----

    def collect_garbage(self, now=None):
        """Delete the chunks no backup uses, as PBS's: mark every chunk an index names (its access time), then sweep
        the chunks not touched since GC_GRACE_S before the mark began. One at a time on this node (.lock); across the
        nodes of a cluster the caller holds the cluster's lock named after the store's id. Returns what it did."""
        start = time.time() if now is None else now
        cutoff = start - GC_GRACE_S
        with open(self.root / ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            marked = 0
            for vm, stamp, manifest in self.backups():
                for disk in manifest.get("disks", {}):
                    try:
                        index = self.index_of(vm, stamp, disk)
                    except (OSError, ValueError, KeyError) as e:
                        # The chunks it names are unknown: sweeping now could delete them.
                        raise StoreError(f"garbage collection stopped, backup {vm}/{stamp} unreadable: {e}") from None
                    for digest in set(index.digests) - {ZERO}:
                        with contextlib.suppress(FileNotFoundError):
                            os.utime(self.chunk_path(digest))
                            marked += 1
            removed = freed = kept = 0
            for prefix in sorted((self.root / CHUNKS).iterdir()):
                for path in list(prefix.iterdir()):  # a list: the sweep renames entries of this directory
                    if path.suffix == ".sweep":  # left by a collection that stopped between its rename and its check
                        original = path.with_suffix("")
                        with contextlib.suppress(FileNotFoundError):
                            if original.exists():
                                path.unlink()
                            else:
                                os.replace(path, original)
                        if not original.exists():
                            continue
                        path = original
                    size = _sweep(path, cutoff)
                    if size is None:
                        kept += 1
                    else:
                        removed += 1
                        freed += size
        return {"marked": marked, "removed": removed, "freed_bytes": freed, "kept": kept}


def _sweep(path, cutoff):
    """Delete `path` when it was not touched since `cutoff`: its size, or None when it is kept. A backup may touch the
    chunk between the check and the deletion: the chunk is first renamed, so a touch after the rename finds nothing
    and writes the chunk again, and a touch before it shows on the renamed file, which then goes back."""
    try:
        if path.stat().st_atime >= cutoff:
            return None
        doomed = path.with_name(path.name + ".sweep")
        os.replace(path, doomed)
    except FileNotFoundError:
        return None
    st = doomed.stat()
    if st.st_atime >= cutoff:
        os.replace(doomed, path)  # touched meanwhile; a copy written meanwhile holds the same content
        return None
    doomed.unlink()
    return st.st_size


def _safe(name):
    """A disk's or a file's name as one file name of the backup's directory."""
    if not name or name in (".", "..") or "/" in name or "\0" in name or name == "manifest.json":
        raise ValueError(f"invalid name in a backup: {name!r}")
    return name


def fingerprint(key):
    """What the store's configuration keeps of its key: enough to refuse another key, nothing to rebuild it."""
    return hashlib.sha256(_derive(key, b"fingerprint")).hexdigest()


def new_key():
    return secrets.token_bytes(32)


def new_master_key():
    """A master recovery key, as PBS's: (private PEM, public PEM), RSA 4096. Hyperlite keeps the public half only."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private_pem, public_pem


def check_public_key(public_pem):
    """A master key's public half given by the administrator: an RSA key of at least 3072 bits, PEM. Its PEM bytes."""
    try:
        public = serialization.load_pem_public_key(public_pem.encode() if isinstance(public_pem, str) else public_pem)
    except (ValueError, TypeError):
        raise StoreError("The master key is not a public key in PEM format") from None
    if not isinstance(public, rsa.RSAPublicKey) or public.key_size < 3072:
        raise StoreError("The master key must be an RSA key of 3072 bits or more")
    return public.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def recover_key(manifest, private_pem):
    """The store's key from a backup's manifest and the master recovery key's private half."""
    sealed = manifest.get("recovery_key")
    if not sealed:
        raise StoreError("This backup was made without a master recovery key")
    private = serialization.load_pem_private_key(private_pem, password=None)
    try:
        return private.decrypt(
            base64.b64decode(sealed),
            padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
        )
    except ValueError:
        raise StoreError("This is not the master key this backup was made with") from None


def key_code(key):
    """The store's key as a person writes it down: base32, in groups of four (and back with key_from_code)."""
    text = base64.b32encode(key).decode().rstrip("=")
    return "-".join(text[i : i + 4] for i in range(0, len(text), 4))


def key_from_code(code):
    text = "".join(ch for ch in code.upper() if ch.isalnum())
    try:
        key = base64.b32decode(text + "=" * (-len(text) % 8))
    except (ValueError, base64.binascii.Error):
        raise StoreError("This is not a store key") from None
    if len(key) != 32:
        raise StoreError("This is not a store key")
    return key


def create(root, encrypted_with=None, chunk_size=CHUNK_SIZE):
    """Make a new store in the directory `root` (created when missing, refused when it already holds anything).
    `encrypted_with`: the store's key, for an encrypted store. Refused on a file system that does not keep a set
    access time, which garbage collection needs."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise StoreError(f"{root} is not empty")
    probe = root / ".atime-probe"
    probe.write_bytes(b"")
    try:
        os.utime(probe, (1_000_000_000, 1_000_000_000))
        os.utime(probe)
        if probe.stat().st_atime < time.time() - 3600:
            raise StoreError(f"{root} does not keep access times, which garbage collection needs")
    finally:
        probe.unlink()
    (root / CHUNKS).mkdir()  # its 65,536 directories are made as chunks arrive
    config = {"format": FORMAT, "id": uuid.uuid4().hex, "chunk_size": chunk_size, "created": _now_iso()}
    config["encrypted"] = encrypted_with is not None
    if encrypted_with is not None:
        config["key_fingerprint"] = fingerprint(encrypted_with)
    _write_atomic(root / CONFIG, json.dumps(config, indent=1).encode())
    return Store(root, encrypted_with)
