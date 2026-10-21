"""Session tokens signed with Ed25519 key pairs held by hyperlite-daemon, as Proxmox VE signs its tickets with
authkey.key, which only root reads, and pveproxy verifies them with authkey.pub (docs/design/privilege-separation.md,
section 12).

  /etc/hyperlite/priv/authkey.key   the key signing now (root, 0600): read by the daemon only
  /etc/hyperlite/authkeys.json      the public keys, newest first (0644): what the API verifies with

A new pair every ROTATE_S, made when a token is signed after that time; a public key is kept KEEP_S after its successor
appeared, so a session of "Stay signed in" (7 days) outlives the rotations. A token names its key (`kid`).

The API asks the daemon to sign (`auth.sign`). HYPERLITE_AUTH_SIGNING=local signs in this process instead, with the
keys under HYPERLITE_ETC_DIR: the test suite and the e2e backend, which run no daemon; a node never sets it."""

import base64
import contextlib
import hashlib
import itertools
import json
import os
import threading
import time

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from jwt import PyJWTError

from app.core import paths

ALGORITHM = "EdDSA"
ROTATE_S = 24 * 3600
KEEP_S = 8 * 24 * 3600
SIGNING_ENV = "HYPERLITE_AUTH_SIGNING"

_lock = threading.Lock()
_cache = {}  # path -> ((mtime_ns, size, inode), parsed content)


def private_path():
    return paths.PRIV / "authkey.key"


def public_path():
    return paths.ETC / "authkeys.json"


def _kid(raw):
    return hashlib.sha256(raw).hexdigest()[:16]


def _raw(public_key):
    return public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _cached(path, parse):
    """`parse(bytes)` of the file, read again only when it changed; None when it does not exist."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        _cache.pop(path, None)
        return None
    stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
    hit = _cache.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    with open(path, "rb") as f:
        value = parse(f.read())
    _cache[path] = (stamp, value)
    return value


def _public_keys():
    """The published keys, newest first: [{"kid", "public" (base64 of the raw key), "created"}]."""
    return _cached(public_path(), lambda data: json.loads(data)["keys"]) or []


def _write(path, data, mode):
    """Replace `path` with `data` at once (a reader sees the old file or the new one), `mode` from the start."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def ensure_keys(now=None):
    """The first key pair, or a new one once the newest is ROTATE_S old; the public keys retired KEEP_S ago dropped.
    The public key is published before its private key replaces the previous one: a token is never signed with a key
    the API cannot verify yet."""
    now = int(now if now is not None else time.time())
    with _lock:
        keys = list(_public_keys())
        if keys and now - keys[0]["created"] < ROTATE_S and private_path().exists():
            return
        private = Ed25519PrivateKey.generate()
        raw = _raw(private.public_key())
        keys.insert(0, {"kid": _kid(raw), "public": base64.b64encode(raw).decode(), "created": now})
        kept = keys[:1] + [k for prev, k in itertools.pairwise(keys) if now - prev["created"] < KEEP_S]
        paths.PRIV.mkdir(parents=True, exist_ok=True)
        os.chmod(paths.PRIV, 0o700)
        _write(public_path(), json.dumps({"keys": kept}, indent=1).encode() + b"\n", 0o644)
        pem = private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        _write(private_path(), pem, 0o600)


def sign_local(claims):
    """A token for `claims`, signed in this process: the daemon (auth.sign), or the tests (HYPERLITE_AUTH_SIGNING)."""
    ensure_keys()
    private = _cached(private_path(), lambda data: serialization.load_pem_private_key(data, password=None))
    return jwt.encode(claims, private, algorithm=ALGORITHM, headers={"kid": _kid(_raw(private.public_key()))})


def sign(claims):
    """A token for `claims` (exp and iat as whole seconds), signed by hyperlite-daemon. Raises DaemonError when the
    daemon does not answer: no session can be opened without it, as on Proxmox VE without pvedaemon."""
    if os.environ.get(SIGNING_ENV) == "local":
        return sign_local(claims)
    from app.core import daemon_client

    return daemon_client.call("auth.sign", {"claims": claims}, user=claims.get("sub"))  # named in its log


def _verification_key(kid):
    for entry in _public_keys():
        if entry.get("kid") == kid:
            return Ed25519PublicKey.from_public_bytes(base64.b64decode(entry["public"]))
    return None


def decode(token):
    """The claims of a token one of this node's keys signed, or None: not such a token, expired, or forged."""
    try:
        header = jwt.get_unverified_header(token)
    except PyJWTError:
        return None
    if header.get("alg") != ALGORITHM:
        return None
    try:
        key = _verification_key(header.get("kid"))
    except (OSError, ValueError, KeyError):
        return None
    if key is None:
        return None
    try:
        return jwt.decode(token, key, algorithms=[ALGORITHM])
    except PyJWTError:
        return None
