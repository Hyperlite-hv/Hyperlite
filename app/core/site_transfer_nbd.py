"""Read or write a disk through its NBD export for a migration between clusters (app/core/site_transfer.py), run by
the system's Python, which has libnbd's binding (python3-libnbd), as app/core/backup_store_nbd_read.py.

    python3 -I site_transfer_nbd.py read URI           what is allocated, then the end, on its output
    python3 -I site_transfer_nbd.py write URI SIZE     the same frames from its input, written into the export

Frames, on both: ">BQQ" kind, offset, length; DATA (1) is followed by its `length` bytes, ZERO (3) is a range of
zeros, END (0) ends. `write` checks each frame lies inside SIZE and in increasing offsets, flushes at END and exits 0
only then. Exits non-zero on any error, said on its error output. Imports nothing of Hyperlite's."""

import struct
import sys

import nbd  # python3-libnbd

HEAD = struct.Struct(">BQQ")
DATA, ZERO, END = 1, 3, 0
WINDOW = 64 * 1024 * 1024  # asked of block_status at a time
PIECE = 4 * 1024 * 1024  # read at a time
STATE_HOLE, STATE_ZERO = 1, 2


def read(uri):
    handle = nbd.NBD()
    handle.add_meta_context("base:allocation")
    handle.connect_uri(uri)
    size = handle.get_size()
    out = sys.stdout.buffer
    offset = 0
    while offset < size:
        extents = []

        def collect(context, at, entries, err, extents=extents):
            if context == "base:allocation":
                extents.extend(zip(entries[0::2], entries[1::2], strict=True))
            return 0

        handle.block_status(min(WINDOW, size - offset), offset, collect)
        if not extents:
            raise RuntimeError(f"No allocation map at {offset}")
        for length, flags in extents:
            length = min(length, size - offset)
            if length <= 0:
                break
            if flags & STATE_ZERO:
                out.write(HEAD.pack(ZERO, offset, length))
            else:
                end = offset + length
                at = offset
                while at < end:
                    n = min(PIECE, end - at)
                    out.write(HEAD.pack(DATA, at, n))
                    out.write(handle.pread(n, at))
                    at += n
            offset += length
    out.write(HEAD.pack(END, size, 0))
    out.flush()
    handle.shutdown()


def _take(source, n):
    data = source.read(n)
    if len(data) != n:
        raise RuntimeError("The input ended in the middle of a frame")
    return data


def write(uri, size):
    handle = nbd.NBD()
    handle.connect_uri(uri)
    if handle.get_size() < size:
        raise RuntimeError(f"The export holds {handle.get_size()} bytes, fewer than {size}")
    source = sys.stdin.buffer
    position = 0
    while True:
        kind, offset, length = HEAD.unpack(_take(source, HEAD.size))
        if kind == END:
            handle.flush()
            handle.shutdown()
            return
        if kind not in (DATA, ZERO) or offset < position or offset + length > size or length > size:
            raise RuntimeError(f"A frame at {offset} of {length} bytes is not one of the disk's")
        if kind == DATA:
            if length > PIECE:
                raise RuntimeError(f"A data frame of {length} bytes")
            handle.pwrite(_take(source, length), offset)
        else:
            at = offset
            while at < offset + length:  # NBD servers cap one request
                n = min(1024 * 1024 * 1024, offset + length - at)
                handle.zero(n, at)
                at += n
        position = offset + length


def main():
    if sys.argv[1] == "read" and len(sys.argv) == 3:
        read(sys.argv[2])
    elif sys.argv[1] == "write" and len(sys.argv) == 4:
        write(sys.argv[2], int(sys.argv[3]))
    else:
        raise SystemExit("usage: site_transfer_nbd.py read URI | write URI SIZE")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # the caller reads the message, the exit code says it failed
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
