"""The stream a disk goes in from one cluster to another (docs/design/multi-cluster.md, section 9.1, point 4): only
what is allocated, in frames of at most FRAME bytes, each with its offset, compressed with zstd when that makes it
smaller, and a trailer with the SHA-256 of every frame's offset, length and data, which the target compares with
what it received.

    MAGIC, then frames:  ">BQII" kind (DATA or ZSTD), offset, length, stored length, and the stored bytes
                         ">BQ"   ZERO, offset (followed by ">Q" length): a range the target makes zeros
                         ">B"    END, followed by the 32-byte digest

The target checks each frame before it writes it: inside the disk it declared, in increasing offsets, at most FRAME
bytes; a stream that breaks any rule, or ends without its trailer, is refused whole."""

import hashlib
import struct

import zstandard

MAGIC = b"HLMIG1\n"
FRAME = 4 * 1024 * 1024
DATA, ZSTD, ZERO, END = 1, 2, 3, 0
_HEAD = struct.Struct(">BQII")
_ZERO = struct.Struct(">QQ")


class StreamError(Exception):
    pass


def _account(digest, kind, offset, data_or_length):
    if kind == ZERO:
        digest.update(struct.pack(">BQQ", ZERO, offset, data_or_length))
    else:
        digest.update(struct.pack(">BQI", DATA, offset, len(data_or_length)))
        digest.update(data_or_length)


def encode(pieces, level=3):
    """The stream of a disk, as bytes chunks, from its `pieces` in increasing offsets: (offset, data) for what is
    allocated (cut into frames of at most FRAME bytes), (offset, length) for a range of zeros."""
    compressor = zstandard.ZstdCompressor(level=level)
    digest = hashlib.sha256()
    yield MAGIC
    for offset, piece in pieces:
        if isinstance(piece, int):
            _account(digest, ZERO, offset, piece)
            yield struct.pack(">B", ZERO) + _ZERO.pack(offset, piece)
            continue
        view = memoryview(piece)
        for start in range(0, len(view), FRAME):
            data = bytes(view[start : start + FRAME])
            _account(digest, DATA, offset + start, data)
            packed = compressor.compress(data)
            kind, stored = (ZSTD, packed) if len(packed) < len(data) else (DATA, data)
            yield _HEAD.pack(kind, offset + start, len(data), len(stored)) + stored
    yield struct.pack(">B", END) + digest.digest()


def pieces_of(extents, read):
    """encode()'s pieces from `extents` [(offset, length, allocated)] and `read(offset, length)`, a frame at a time."""
    for offset, length, allocated in extents:
        if not allocated:
            yield offset, length
            continue
        end = offset + length
        while offset < end:
            size = min(FRAME, end - offset)
            data = read(offset, size)
            if len(data) != size:
                raise StreamError(f"Read {len(data)} bytes at {offset} instead of {size}")
            yield offset, data
            offset += size


class _Reader:
    def __init__(self, chunks):
        self._chunks, self._buffer = iter(chunks), bytearray()

    def take(self, n):
        while len(self._buffer) < n:
            try:
                self._buffer += next(self._chunks)
            except StopIteration:
                raise StreamError("The stream ended in the middle of a frame") from None
        out = bytes(self._buffer[:n])
        del self._buffer[:n]
        return out

    def rest(self):
        return bool(self._buffer) or any(self._chunks)


def decode(chunks, size, write, zero):
    """Write the stream of a disk of `size` bytes: `write(offset, data)`, `zero(offset, length)`. Its digest, once the
    trailer matched; a StreamError on the first rule broken (nothing is trusted from the remote)."""
    reader = _Reader(chunks)
    if reader.take(len(MAGIC)) != MAGIC:
        raise StreamError("Not a migration stream")
    decompressor = zstandard.ZstdDecompressor()
    digest = hashlib.sha256()
    position = 0
    while True:
        (kind,) = reader.take(1)
        if kind == END:
            expected = reader.take(32)
            if reader.rest():
                raise StreamError("Data after the end of the stream")
            if expected != digest.digest():
                raise StreamError("The stream's digest does not match what was received")
            return digest.hexdigest()
        if kind == ZERO:
            offset, length = _ZERO.unpack(reader.take(_ZERO.size))
            stored = None
        elif kind in (DATA, ZSTD):
            offset, length, stored_length = _HEAD.unpack(bytes([kind]) + reader.take(_HEAD.size - 1))[1:]
            if length > FRAME or stored_length > FRAME + 1024:
                raise StreamError(f"A frame of {length} bytes is larger than {FRAME}")
            stored = reader.take(stored_length)
        else:
            raise StreamError(f"Unknown frame kind {kind}")
        if offset < position or offset + length > size or length == 0:
            raise StreamError(f"A frame at {offset} of {length} bytes is outside the disk or out of order")
        if stored is None:
            _account(digest, ZERO, offset, length)
            zero(offset, length)
        else:
            data = stored
            if kind == ZSTD:
                try:
                    data = decompressor.decompress(stored, max_output_size=length)
                except zstandard.ZstdError as e:
                    raise StreamError(f"A frame at {offset} cannot be decompressed: {e}") from None
            if len(data) != length:
                raise StreamError(f"A frame at {offset} holds {len(data)} bytes instead of {length}")
            _account(digest, DATA, offset, data)
            write(offset, data)
        position = offset + length
