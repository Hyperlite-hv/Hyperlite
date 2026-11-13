"""A VM's screen as a small PNG: libvirt hands the display over as a PPM image (P6); it is scaled down by sampling
and written as PNG with the standard library alone, for the console thumbnail of a VM's overview."""

import re
import struct
import zlib

MAX_SOURCE_BYTES = 64 * 1024 * 1024  # a 4K screen in RGB is 25 MB
_HEADER = re.compile(rb"^P6\s+(?:#[^\n]*\s+)*(\d+)\s+(?:#[^\n]*\s+)*(\d+)\s+(?:#[^\n]*\s+)*(\d+)\s")


def parse_ppm(data):
    """(width, height, RGB bytes) of a binary PPM with 8-bit samples. ValueError for anything else."""
    m = _HEADER.match(data)
    if not m:
        raise ValueError("Not a binary PPM image")
    width, height, maxval = (int(x) for x in m.groups())
    if maxval != 255 or not (0 < width <= 16384 and 0 < height <= 16384):
        raise ValueError("Unsupported PPM image")
    pixels = data[m.end() : m.end() + width * height * 3]
    if len(pixels) != width * height * 3:
        raise ValueError("Truncated PPM image")
    return width, height, pixels


def scale(width, height, pixels, max_width):
    """Nearest-neighbour scaling to at most `max_width` pixels wide, keeping the aspect ratio."""
    if width <= max_width:
        return width, height, pixels
    out_w = max_width
    out_h = max(1, round(height * max_width / width))
    cols = [int(x * width / out_w) * 3 for x in range(out_w)]
    rows = []
    for y in range(out_h):
        row = pixels[int(y * height / out_h) * width * 3 :][: width * 3]
        rows.append(b"".join(row[c : c + 3] for c in cols))
    return out_w, out_h, b"".join(rows)


def png(width, height, pixels):
    """RGB pixels as a PNG file."""

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    stride = width * 3
    raw = b"".join(b"\x00" + pixels[y * stride : (y + 1) * stride] for y in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def thumbnail(ppm, max_width=480):
    return png(*scale(*parse_ppm(ppm), max_width))
