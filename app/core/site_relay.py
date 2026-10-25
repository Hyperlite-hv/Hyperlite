"""The target's guard on a live migration between clusters (docs/design/multi-cluster.md, section 9.2, choice A): the
source cluster's libvirt talks to this host's libvirt through it, and it lets through only what a migration's
destination answers, as Proxmox's tunnel carries only its own small command set.

libvirt's RPC framing (src/rpc/virnetprotocol.x): a 4-byte length (itself included), a 24-byte header (program,
version, procedure, type, serial, status), the XDR payload. From the source, only calls of REMOTE_PROGRAM's procedures
in CALLS and keepalive messages pass; a call to prepare or finish the migration is read, its parameters and cookie
checked (check_destination_xml: the domain the source asks this host to run must be the incoming one, on its prepared
volumes and chosen networks, with nothing of this host's). Anything else closes the channel. What this host's libvirt
answers goes back as it is."""

import re
import struct
import xml.etree.ElementTree as ET

REMOTE_PROGRAM = 0x20008086
KEEPALIVE_PROGRAM = 0x6B656570
CALL, MESSAGE = 0, 2
HEADER = struct.Struct(">IIIiIi")  # program, version, procedure, type, serial, status
MAX_MESSAGE = 32 * 1024 * 1024  # VIR_NET_MESSAGE_MAX
PREPARE3_PARAMS, FINISH3_PARAMS = 303, 306
# The procedures a migration's destination answers (src/remote/remote_protocol.x), and only those: what the source's
# libvirt 11.3 called through this relay during a whole live migration in the lab, nothing that defines, starts, reads
# or changes anything else.
CALLS = {
    1: "CONNECT_OPEN",
    2: "CONNECT_CLOSE",
    3: "CONNECT_GET_TYPE",
    4: "CONNECT_GET_VERSION",
    60: "CONNECT_SUPPORTS_FEATURE",
    66: "AUTH_LIST",
    360: "CONNECT_REGISTER_CLOSE_CALLBACK",
    361: "CONNECT_UNREGISTER_CLOSE_CALLBACK",
    PREPARE3_PARAMS: "DOMAIN_MIGRATE_PREPARE3_PARAMS",
    FINISH3_PARAMS: "DOMAIN_MIGRATE_FINISH3_PARAMS",
}
PERSIST_DEST = 1 << 3  # VIR_MIGRATE_PERSIST_DEST: this host defines the VM itself once it runs (from what it runs)
# The migration's parameters the source may give (VIR_MIGRATE_PARAM_*); "persistent_xml" is not one of them.
PARAMS = {
    "destination_xml", "destination_name", "migrate_uri", "disks_uri", "migrate_disks", "bandwidth",
    "bandwidth.postcopy", "listen_address", "disks_port", "graphics_uri", "compression",
    "compression.mt.level", "compression.mt.threads", "compression.mt.dthreads", "compression.xbzrle.cache",
    "compression.zlib.level", "compression.zstd.level", "auto_converge.initial", "auto_converge.increment",
    "parallel.connections", "tls.destination", "migrate_disks_detect_zeroes",
}  # fmt: skip


class Refused(Exception):
    pass


# ---- XDR ----


class _Xdr:
    def __init__(self, data):
        self.data, self.at = data, 0

    def take(self, n):
        if self.at + n > len(self.data):
            raise Refused("A call shorter than its own fields")
        out = self.data[self.at : self.at + n]
        self.at += n
        return out

    def uint(self):
        return struct.unpack(">I", self.take(4))[0]

    def int(self):
        return struct.unpack(">i", self.take(4))[0]

    def opaque(self, limit=MAX_MESSAGE):
        n = self.uint()
        if n > limit:
            raise Refused("A field longer than allowed")
        out = self.take(n)
        self.take((4 - n % 4) % 4)
        return out

    def string(self):
        return self.opaque(4 * 1024 * 1024).decode("utf-8", errors="strict")


def _typed_params(x):
    """{field: value} of a remote_typed_param list."""
    out = {}
    for _ in range(min(x.uint(), 256)):
        field = x.string()
        kind = x.int()
        if kind in (1, 2, 6):  # int, uint, boolean
            value = x.int() if kind != 2 else x.uint()
        elif kind in (3, 4):  # llong, ullong
            value = struct.unpack(">q" if kind == 3 else ">Q", x.take(8))[0]
        elif kind == 5:
            value = struct.unpack(">d", x.take(8))[0]
        elif kind == 7:
            value = x.string()
        else:
            raise Refused(f"A parameter of an unknown type ({kind})")
        out.setdefault(field, []).append(value)
    return out


# ---- What the source may ask ----


class CallFilter:
    """Fed the bytes the source's libvirt sends, a message at a time; returns those to pass on to this host's libvirt,
    raises Refused on the first that is not a migration's."""

    def __init__(self, expected):
        self.expected, self._buffer, self.seen = expected, bytearray(), []

    def feed(self, data):
        self._buffer += data
        out = bytearray()
        while len(self._buffer) >= 4:
            (length,) = struct.unpack(">I", self._buffer[:4])
            if length < 4 + HEADER.size or length > MAX_MESSAGE + 4:
                raise Refused(f"A message of {length} bytes")
            if len(self._buffer) < length:
                break
            message = bytes(self._buffer[:length])
            del self._buffer[:length]
            self._check(message)
            out += message
        return bytes(out)

    def _check(self, message):
        program, _version, procedure, kind, _serial, _status = HEADER.unpack(message[4 : 4 + HEADER.size])
        if program == KEEPALIVE_PROGRAM and kind == MESSAGE and procedure in (1, 2):
            return
        if program != REMOTE_PROGRAM or kind != CALL or procedure not in CALLS:
            raise Refused(f"Not a migration's call (program {program:#x}, procedure {procedure}, type {kind})")
        self.seen.append(CALLS[procedure])
        if procedure in (PREPARE3_PARAMS, FINISH3_PARAMS):
            x = _Xdr(message[4 + HEADER.size :])
            params = _typed_params(x)
            cookie = x.opaque(16 * 1024 * 1024)
            flags = x.uint()
            check_call(params, cookie, flags, self.expected, prepare=procedure == PREPARE3_PARAMS)


def check_call(params, cookie, flags, expected, prepare):
    unknown = set(params) - PARAMS
    if unknown:
        raise Refused(f"Migration parameters not allowed: {', '.join(sorted(unknown))}")
    if flags & PERSIST_DEST:
        raise Refused("The source may not define the VM here: this host defines it from what it runs")
    for name in params.get("destination_name", []):
        if name != expected["name"]:
            raise Refused(f"Another name than the incoming VM's: {name}")
    for uri in params.get("migrate_uri", []) + params.get("disks_uri", []):
        if not str(uri).startswith("unix://" + expected["sockets"]):
            raise Refused("A migration channel outside this incoming VM's sockets")
    xmls = params.get("destination_xml", [])
    if prepare and len(xmls) != 1:
        raise Refused("The domain to prepare is missing")
    for xml in xmls:
        check_destination_xml(xml, expected)
    _check_cookie(cookie)


def _check_cookie(cookie):
    """libvirt's migration cookie is XML; a persistent definition inside it is refused (this host writes its own)."""
    if not cookie:
        return
    text = cookie.rstrip(b"\0").decode("utf-8", errors="replace")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise Refused("A migration cookie that is not XML") from None
    if root.find(".//domain") is not None or root.find(".//persistent") is not None:
        raise Refused("A migration cookie with a domain definition")


# ---- The domain the source asks this host to run ----

FORBIDDEN = {"hostdev", "filesystem", "redirdev", "smartcard", "shmem", "kernel", "initrd", "dtb", "cmdline"}
CHARDEVS = {"serial", "console", "parallel", "channel"}
CHAR_TYPES = {"pty", "unix", "spicevmc", "qemu-vdagent", "null"}
QEMU_DIR = "/var/lib/libvirt/qemu/"
_NS = re.compile(r"^\{(?!http://libvirt.org/schemas/domain/1.0\})")


def check_destination_xml(xml, expected):
    """Refused unless `xml` is the incoming VM on what this host prepared for it: `expected` {"name", "uuid",
    "volumes" {path}, "networks" {name}, "emulators" {path}}."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise Refused("The domain is not XML") from None
    if root.tag != "domain" or root.get("type") not in ("kvm", "qemu"):
        raise Refused("Not a KVM domain")
    if root.findtext("name") != expected["name"] or (root.findtext("uuid") or "").lower() != expected["uuid"].lower():
        raise Refused("Another VM than the incoming one")
    metadata = root.find("metadata")
    inert = {id(e) for e in metadata.iter()} if metadata is not None else set()  # notes and tags, read by nobody
    for element in root.iter():
        if id(element) in inert:
            continue
        tag = element.tag
        if not isinstance(tag, str) or _NS.match(tag):
            raise Refused(f"An element outside libvirt's domain schema: {tag}")
        if tag in FORBIDDEN:
            raise Refused(f"A {tag} of this host is not given to another cluster's VM")
    for seclabel in root.iter("seclabel"):
        if seclabel.get("type") == "none" or seclabel.get("relabel") == "no" or seclabel.find("label") is not None:
            raise Refused("The VM's confinement may not be changed")
    emulator = root.findtext("devices/emulator")
    if emulator is not None and emulator.strip() not in expected["emulators"]:
        raise Refused(f"An emulator this host does not have: {emulator}")
    os_el = root.find("os")
    if os_el is not None:
        loader, nvram = os_el.find("loader"), os_el.find("nvram")
        if loader is not None and loader.text and not loader.text.strip().startswith("/usr/share/"):
            raise Refused("A firmware outside /usr/share")
        if nvram is not None and (nvram.text or nvram.find("source") is not None):
            path = (nvram.text or "").strip()
            if nvram.find("source") is not None or not path.startswith(QEMU_DIR + "nvram/") or ".." in path:
                raise Refused("A UEFI variables file outside this host's own")
    for backing in root.iter("memoryBacking"):
        source = backing.find("source")
        if source is not None and source.get("type") == "file":
            raise Refused("Memory backed by a file of this host")
    devices = root.find("devices")
    if devices is None:
        raise Refused("A domain without devices")
    checked = set()
    for disk in devices.findall("disk"):
        source = disk.find("source")
        if disk.get("device") in ("cdrom", "floppy"):
            if source is not None and any(source.get(a) for a in ("file", "dev", "dir", "volume", "name")):
                raise Refused("A medium of this host in a drive")
        elif disk.get("device") == "disk" and disk.get("type") in ("file", "block") and source is not None:
            path = source.get("file") or source.get("dev")
            if path not in expected["volumes"]:
                raise Refused(f"A disk on something else than a volume prepared for it: {path}")
            for backing in disk.findall("backingStore"):
                if len(backing) or backing.attrib:
                    raise Refused("A disk with a backing file of this host")
        else:
            raise Refused(f"A disk this host does not give: {disk.get('device')} {disk.get('type')}")
        if source is not None:
            checked.add(id(source))
        for backing in disk.iter("backingStore"):
            checked.update(id(s) for s in backing.iter("source"))
    for interface in devices.findall("interface"):
        source = interface.find("source")
        if interface.get("type") != "network" or source is None or source.get("network") not in expected["networks"]:
            raise Refused("A card on something else than a network chosen for it")
        if interface.find("script") is not None or interface.find("backend") is not None:
            raise Refused("A card with a script or a backend of this host")
        checked.add(id(source))
    for chardev in (e for e in devices if e.tag in CHARDEVS):
        if chardev.get("type") not in CHAR_TYPES:
            raise Refused(f"A {chardev.tag} of type {chardev.get('type')}")
        for source in chardev.findall("source"):
            path = source.get("path")
            # A pty's path is the source's terminal, which this host's libvirt allocates again.
            if chardev.get("type") == "unix" and path and (not path.startswith(QEMU_DIR) or ".." in path):
                raise Refused(f"A {chardev.tag} socket outside this host's own")
            checked.add(id(source))
    for graphics in devices.findall("graphics"):
        for listen in graphics.findall("listen"):
            if listen.get("type") == "address" and listen.get("address") not in (None, "127.0.0.1", "::1"):
                raise Refused("A console listening on another address than this host's own")
            if listen.get("type") == "socket" and not (listen.get("socket") or QEMU_DIR).startswith(QEMU_DIR):
                raise Refused("A console socket outside this host's own")
        if graphics.get("listen") not in (None, "127.0.0.1", "::1"):
            raise Refused("A console listening on another address than this host's own")
    for tpm in devices.findall("tpm"):
        backend = tpm.find("backend")
        if backend is None or backend.get("type") != "emulator" or backend.find("source") is not None:
            raise Refused("A TPM other than an emulated one")
    for rng in devices.findall("rng"):
        backend = rng.find("backend")
        if backend is None or backend.get("model") not in ("random", "builtin"):
            raise Refused("A random number source other than this host's own")
        if backend.get("model") == "random" and (backend.text or "/dev/urandom").strip() not in (
            "/dev/urandom",
            "/dev/random",
        ):
            raise Refused("A random number source other than this host's own")
    # Whatever else names a file, a device or a socket of this host.
    for source in root.iter("source"):
        if id(source) not in checked and any(source.get(a) for a in ("file", "dev", "dir", "path", "socket")):
            raise Refused("A device naming a path of this host")
