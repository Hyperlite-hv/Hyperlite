"""A VM's screen as a small PNG: libvirt hands the display over as a PPM image (P6); it is scaled down by sampling
and written as PNG with the standard library alone, for the console thumbnail of a VM's overview."""

import struct
import zlib

MAX_SOURCE_BYTES = 64 * 1024 * 1024  # a 4K screen in RGB is 25 MB
HEADER_MAX = 4096  # the magic, three numbers and their comments: far less in practice
_SPACE = b" \t\r\n\v\f"


def _header(data):
    """The three numbers of a P6 header and where its pixels start, read token by token (a regular expression with
    repeated comments and spaces backtracks without end on a crafted header)."""
    if data[:2] != b"P6":
        raise ValueError("Not a binary PPM image")
    numbers, i, end = [], 2, min(len(data), HEADER_MAX)
    while len(numbers) < 3:
        if i >= end:
            raise ValueError("Not a binary PPM image")
        c = data[i : i + 1]
        if c in _SPACE:
            i += 1
        elif c == b"#":
            newline = data.find(b"\n", i, end)
            if newline < 0:
                raise ValueError("Not a binary PPM image")
            i = newline + 1
        elif c.isdigit():
            start = i
            while i < end and data[i : i + 1].isdigit():
                i += 1
            if i - start > 6:
                raise ValueError("Unsupported PPM image")
            numbers.append(int(data[start:i]))
        else:
            raise ValueError("Not a binary PPM image")
    if i >= end or data[i : i + 1] not in _SPACE:  # one whitespace character, then the pixels
        raise ValueError("Not a binary PPM image")
    return numbers, i + 1


def parse_ppm(data):
    """(width, height, RGB bytes) of a binary PPM with 8-bit samples. ValueError for anything else."""
    (width, height, maxval), start = _header(data)
    if maxval != 255 or not (0 < width <= 16384 and 0 < height <= 16384):
        raise ValueError("Unsupported PPM image")
    pixels = data[start : start + width * height * 3]
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
