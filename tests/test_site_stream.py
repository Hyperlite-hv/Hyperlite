"""The stream a disk goes in to another cluster (app/core/site_stream.py): what is allocated arrives where it was,
the rest as zeros, and a stream that breaks a rule is refused whole."""

import os
import struct

import pytest

from app.core import site_stream

SIZE = 3 * site_stream.FRAME + 12345


def _disk():
    data = bytearray(SIZE)
    data[0:5000] = os.urandom(5000)
    data[site_stream.FRAME : 2 * site_stream.FRAME + 7] = b"x" * (site_stream.FRAME + 7)  # compresses well
    data[SIZE - 100 :] = os.urandom(100)
    return bytes(data)


EXTENTS = [
    (0, site_stream.FRAME, True),
    (site_stream.FRAME, site_stream.FRAME + 7, True),
    (2 * site_stream.FRAME + 7, SIZE - 100 - (2 * site_stream.FRAME + 7), False),
    (SIZE - 100, 100, True),
]


def _receive(chunks, size=SIZE):
    out = bytearray(b"\xff" * size)

    def write(offset, data):
        out[offset : offset + len(data)] = data

    def zero(offset, length):
        out[offset : offset + length] = bytes(length)

    return site_stream.decode(chunks, size, write, zero), bytes(out)


def test_a_disk_arrives_as_it_was_and_holes_are_zeros():
    disk = _disk()
    chunks = list(site_stream.encode(site_stream.pieces_of(EXTENTS, lambda o, n: disk[o : o + n])))
    digest, out = _receive(chunks)
    assert out == disk and len(digest) == 64
    assert sum(map(len, chunks)) < SIZE // 2  # holes not sent, repeated data compressed
    # The chunks as a network gives them: cut anywhere.
    flat = b"".join(chunks)
    assert _receive([flat[i : i + 777] for i in range(0, len(flat), 777)])[1] == disk


def _stream(*frames, digest=None):
    return [site_stream.MAGIC, *frames, struct.pack(">B", site_stream.END) + (digest or b"\0" * 32)]


def test_a_stream_that_breaks_a_rule_is_refused():
    disk = _disk()
    good = b"".join(site_stream.encode(site_stream.pieces_of(EXTENTS, lambda o, n: disk[o : o + n])))
    with pytest.raises(site_stream.StreamError, match="digest"):
        _receive([good[:-1] + bytes([good[-1] ^ 1])])
    with pytest.raises(site_stream.StreamError, match="ended in the middle"):
        _receive([good[:-10]])
    with pytest.raises(site_stream.StreamError, match="after the end"):
        _receive([good + b"more"])
    with pytest.raises(site_stream.StreamError, match="Not a migration stream"):
        _receive([b"GET / HTTP/1.1\r\n"])
    with pytest.raises(site_stream.StreamError, match="outside the disk"):
        _receive([b"".join(site_stream.encode([(SIZE - 10, bytes(20))]))])
    beyond = struct.pack(">BQII", site_stream.DATA, 0, site_stream.FRAME + 1, 4)
    with pytest.raises(site_stream.StreamError, match="larger than"):
        _receive(_stream(beyond + b"abcd"))
    backwards = list(site_stream.encode([(100, bytes(10))]))[1] + list(site_stream.encode([(50, bytes(10))]))[1]
    with pytest.raises(site_stream.StreamError, match="out of order"):
        _receive(_stream(backwards))
    with pytest.raises(site_stream.StreamError, match="Unknown frame"):
        _receive(_stream(b"\x09"))
    lying = struct.pack(">BQII", site_stream.ZSTD, 0, 10, 4) + b"abcd"
    with pytest.raises(site_stream.StreamError, match="cannot be decompressed"):
        _receive(_stream(lying))
