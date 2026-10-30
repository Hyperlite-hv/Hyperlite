"""Read some ranges of an NBD export, run by the system's Python, which has libnbd's binding (python3-libnbd): the
service's environment does not carry it (app/core/backup_store_vm.py, an incremental backup's changed chunks).

    python3 -I backup_store_nbd_read.py URI RANGES        RANGES: a file of "offset length" lines

Writes each range's bytes to its output, in the order given, nothing else. Exits non-zero on any error, said on its
error output. Imports nothing of Hyperlite's.
"""

import sys

import nbd  # python3-libnbd

MAX_REQUEST = 32 * 1024 * 1024  # NBD servers refuse larger reads


def main():
    uri, ranges = sys.argv[1], sys.argv[2]
    handle = nbd.NBD()
    handle.connect_uri(uri)
    out = sys.stdout.buffer
    with open(ranges) as f:
        for line in f:
            offset, length = (int(x) for x in line.split())
            while length > 0:
                n = min(length, MAX_REQUEST)
                out.write(handle.pread(n, offset))
                offset += n
                length -= n
    out.flush()
    handle.shutdown()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # the caller reads the message, the exit code says it failed
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
