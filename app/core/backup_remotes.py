"""Other Hyperlite clusters whose backup stores this cluster copies (docs/design/backup-store.md, step 4, part 2), as
Proxmox Backup Server's remotes: an address, the SHA-256 of its HTTPS certificate (pinned: a certificate that does
not match is refused before anything is sent) and an API token of a dedicated account there, which holds only
backup_store.read on the stores it may copy (Antho, 2026-10-24). The token is kept encrypted in the cluster's
secrets (backup_remotes, replicated under /priv).

RemoteStore reads such a store as backup_store_sync reads a local one: its configuration, its backups' manifests,
their files and their chunks, as they are (encrypted ones stay sealed: this side keeps no key).
"""

import hashlib
import http.client
import json
import re
import socket
import ssl
import urllib.parse
from datetime import UTC, datetime

from app.core import backup_store

NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$")
HOST = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$|^\[?[0-9A-Fa-f:]+\]?$")
DEFAULT_PORT = 8000
TIMEOUT_S = 60
API = "/api/v1"


class RemoteError(Exception):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.message, self.status = message, status


def _repo():
    from app.repositories import registry

    return registry.backups().sync


def _check_address(address, port):
    address = (address or "").strip()
    if not HOST.match(address):
        raise RemoteError("Invalid address: a host name or an IP address")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise RemoteError("Invalid port")
    return address.strip("[]"), port


def probe(address, port=DEFAULT_PORT):
    """The SHA-256 of the certificate the address presents: compared by an administrator with the one its own
    dashboard shows, before it is pinned."""
    address, port = _check_address(address, port)
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # what is checked is the fingerprint, by a person, then pinned
    try:
        with socket.create_connection((address, port), timeout=10) as raw, context.wrap_socket(raw) as tls:
            der = tls.getpeercert(binary_form=True)
    except OSError as e:
        raise RemoteError(f"{address}:{port} does not answer over HTTPS: {e}", 502) from None
    return hashlib.sha256(der).hexdigest()


class _Client:
    """Calls to a remote's API over HTTPS, its certificate pinned, with its token."""

    def __init__(self, address, port, fingerprint, token):
        self.address, self.port, self.fingerprint, self.token = address, port, fingerprint, token
        self._conn = None

    def _connect(self):
        from app.core import peer_https

        try:
            return peer_https._connect(self.address, self.port, self.fingerprint, 10)
        except peer_https.Unavailable as e:
            raise RemoteError(f"{self.address}: {e}", 502) from None
        except OSError as e:
            raise RemoteError(f"{self.address}:{self.port} does not answer: {e}", 502) from None

    def get(self, path):
        """(status, bytes) of GET <API>path; one retry on a kept connection the remote closed."""
        for attempt in (1, 2):
            if self._conn is None:
                self._conn = self._connect()
            try:
                self._conn.sock.settimeout(TIMEOUT_S)
                self._conn.request("GET", API + path, headers={"Authorization": f"Bearer {self.token}"})
                response = self._conn.getresponse()
                return response.status, response.read()
            except (OSError, http.client.HTTPException) as e:
                self.close()
                if attempt == 2:
                    raise RemoteError(f"{self.address} stopped answering: {e}", 502) from None
        raise AssertionError("unreachable")

    def json(self, path):
        status, body = self.get(path)
        if status in (401, 403):
            raise RemoteError(f"{self.address} refused the token ({status}): {_detail(body)}", 403)
        if status != 200:
            raise RemoteError(f"{self.address} answered {status}: {_detail(body)}", 502)
        return json.loads(body)

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def _detail(body):
    try:
        return str(json.loads(body).get("detail"))[:200]
    except (ValueError, AttributeError):
        return body[:200].decode(errors="replace")


def get(name):
    row = _repo().get_remote(name)
    if not row:
        raise RemoteError(f"No remote '{name}'", 404)
    return row


def _client(row):
    from app.core import secrets_crypto

    try:
        token = secrets_crypto.decrypt(row["token_encrypted"])
    except secrets_crypto.SecretUnreadable:
        raise RemoteError(f"The token of the remote '{row['name']}' cannot be read on this node", 409) from None
    return _Client(row["address"], row["port"], row["fingerprint"], token)


def save(name, address, port, fingerprint, token, username):
    """Record a remote once its certificate matches `fingerprint` and its token is accepted."""
    from app.core import secrets_crypto

    if not NAME.match(name or ""):
        raise RemoteError("Invalid name: letters, digits, '.', '-' and '_', 63 characters at most")
    if _repo().get_remote(name):
        raise RemoteError(f"A remote '{name}' already exists", 409)
    address, port = _check_address(address, port)
    fingerprint = (fingerprint or "").replace(":", "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise RemoteError("The fingerprint is the SHA-256 of the remote's certificate: 64 hexadecimal digits")
    if not token or len(token) > 4096:
        raise RemoteError("Give the API token of the remote's dedicated account")
    client = _Client(address, port, fingerprint, token)
    try:
        client.json("/backup-stores")
    finally:
        client.close()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    _repo().save_remote(name, address, port, fingerprint, secrets_crypto.encrypt(token), username, now)
    return describe(get(name))


def describe(row):
    return {k: row[k] for k in ("name", "address", "port", "fingerprint", "created_by", "created_at")}


def stores(name):
    """The names of the remote's stores (those its token may read are marked when it can tell)."""
    client = _client(get(name))
    try:
        listed = client.json("/backup-stores")
    finally:
        client.close()
    items = listed.get("items", listed) if isinstance(listed, dict) else listed
    return [{"name": s["name"], "encrypted": bool(s.get("encrypted"))} for s in items if isinstance(s, dict)]


def remove(name):
    """Forget a remote no copy store names (a copy store names its source "<remote>:<store>")."""
    get(name)
    users = [r["name"] for r in _repo().list_stores() if (r.get("copy_of") or "").startswith(f"{name}:")]
    if users:
        raise RemoteError(f"The remote '{name}' is copied into {', '.join(users)}", 409)
    _repo().delete_remote(name)


def split_source(source):
    """("remote", "store") for a copy's source "remote:store"; None for a store of this cluster."""
    if source and ":" in source:
        remote, _, store = source.partition(":")
        return remote, store
    return None


class RemoteStore:
    """A remote's store, read as backup_store_sync reads a local one (the same methods as Store's it uses)."""

    def __init__(self, remote_name, store_name):
        self.name = store_name
        self._client = _client(get(remote_name))
        self._base = f"/backup-stores/{urllib.parse.quote(store_name, safe='')}/export"
        export = self._client.json(self._base)
        self.config = export["config"]
        self._backups = [(b["vm_uuid"], b["time"], b["manifest"]) for b in export["backups"]]
        self.chunk_size = int(self.config["chunk_size"])
        self.encrypted = bool(self.config.get("encrypted"))

    @property
    def id(self):
        return self.config["id"]

    def backups(self, vm_uuid=None):
        return [b for b in self._backups if vm_uuid is None or b[0] == vm_uuid]

    def manifest(self, vm_uuid, stamp):
        for vm, time, manifest in self._backups:
            if (vm, time) == (vm_uuid, stamp):
                return manifest
        raise FileNotFoundError(f"{vm_uuid}/{stamp}")

    def _file(self, vm_uuid, stamp, name):
        path = (
            f"{self._base}/backups/{urllib.parse.quote(vm_uuid)}/{urllib.parse.quote(stamp)}/{urllib.parse.quote(name)}"
        )
        status, body = self._client.get(path)
        if status != 200:
            raise RemoteError(f"{name} of the backup {vm_uuid}/{stamp} could not be read ({status})", 502)
        return body

    def backup_files(self, vm_uuid, stamp):
        manifest = self.manifest(vm_uuid, stamp)
        names = [f"{disk}.fidx" for disk in manifest["disks"]] + list(manifest.get("files") or {})
        return [(name, self._file(vm_uuid, stamp, name)) for name in names]

    def index_of(self, vm_uuid, stamp, disk):
        raw = self._file(vm_uuid, stamp, f"{disk}.fidx")
        if hashlib.sha256(raw).hexdigest() != self.manifest(vm_uuid, stamp)["disks"][disk]["sha256"]:
            raise ValueError(f"index of {disk} does not match the manifest")
        return backup_store.Index.decode(raw)

    def check_blob(self, digest):
        status, blob = self._client.get(f"{self._base}/chunks/{digest.hex()}")
        if status != 200:
            raise backup_store.MissingChunk(digest)
        backup_store.check_blob_bytes(digest, blob)
        return blob

    def close(self):
        self._client.close()
