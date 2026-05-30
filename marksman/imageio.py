"""Minimal, dependency-free image IO.

A small PNG decoder/encoder built only on the standard library (``zlib`` +
``struct``) so the vision layer can read marked-up targets without numpy or
Pillow.  If Pillow *is* installed it is used instead (so JPEG and other formats
work too); otherwise PNG is fully supported here.

Images are represented by :class:`Image`: width, height and a flat ``bytearray``
of 8-bit RGB samples (3 bytes per pixel, row-major, top-left origin).
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import Tuple

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


@dataclass
class Image:
    width: int
    height: int
    rgb: bytearray            # length == width * height * 3

    def get(self, x: int, y: int) -> Tuple[int, int, int]:
        i = (y * self.width + x) * 3
        return (self.rgb[i], self.rgb[i + 1], self.rgb[i + 2])

    def set(self, x: int, y: int, r: int, g: int, b: int) -> None:
        i = (y * self.width + x) * 3
        self.rgb[i] = r & 0xFF
        self.rgb[i + 1] = g & 0xFF
        self.rgb[i + 2] = b & 0xFF


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def load(path: str) -> Image:
    """Load an image as RGB. Uses Pillow if available, else the PNG decoder."""
    try:  # Pillow handles many formats (JPEG etc.); use it when present.
        from PIL import Image as _PILImage  # type: ignore
        im = _PILImage.open(path).convert("RGB")
        w, h = im.size
        return Image(w, h, bytearray(im.tobytes()))
    except Exception:
        pass
    with open(path, "rb") as fh:
        data = fh.read()
    return decode_png(data)


# --------------------------------------------------------------------------- #
# PNG decode
# --------------------------------------------------------------------------- #

def decode_png(data: bytes) -> Image:
    if data[:8] != _PNG_SIG:
        raise ValueError("Not a PNG file (bad signature). "
                         "Install Pillow to read other formats.")
    pos = 8
    width = height = 0
    bit_depth = color_type = interlace = 0
    palette = b""
    idat = bytearray()

    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length  # 4 len + 4 type + body + 4 crc

        if ctype == b"IHDR":
            (width, height, bit_depth, color_type,
             _comp, _filter, interlace) = struct.unpack(">IIBBBBB", body)
        elif ctype == b"PLTE":
            palette = body
        elif ctype == b"IDAT":
            idat += body
        elif ctype == b"IEND":
            break

    if interlace != 0:
        raise ValueError("Interlaced PNG not supported.")
    if bit_depth != 8:
        raise ValueError("Only 8-bit PNG supported (got bit depth %d)." % bit_depth)

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        raise ValueError("Unsupported PNG colour type %d." % color_type)

    raw = zlib.decompress(bytes(idat))
    samples = _unfilter(raw, width, height, channels)
    rgb = _to_rgb(samples, width, height, color_type, palette)
    return Image(width, height, rgb)


def _unfilter(raw: bytes, width: int, height: int, channels: int) -> bytearray:
    """Reverse PNG scanline filters; return raw samples (channels per pixel)."""
    stride = width * channels
    out = bytearray(stride * height)
    prev = bytearray(stride)
    pos = 0
    for row in range(height):
        ftype = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if ftype == 0:
            pass
        elif ftype == 1:  # Sub
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif ftype == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:  # Average
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                line[i] = (line[i] + ((a + b) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                line[i] = (line[i] + _paeth(a, b, c)) & 0xFF
        else:
            raise ValueError("Unknown PNG filter type %d." % ftype)
        out[row * stride:(row + 1) * stride] = line
        prev = line
    return out


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _to_rgb(samples: bytearray, width: int, height: int,
            color_type: int, palette: bytes) -> bytearray:
    n = width * height
    rgb = bytearray(n * 3)
    if color_type == 2:  # RGB
        return bytearray(samples)
    if color_type == 6:  # RGBA -> drop alpha
        for i in range(n):
            rgb[i * 3] = samples[i * 4]
            rgb[i * 3 + 1] = samples[i * 4 + 1]
            rgb[i * 3 + 2] = samples[i * 4 + 2]
        return rgb
    if color_type == 0:  # grayscale
        for i in range(n):
            g = samples[i]
            rgb[i * 3] = rgb[i * 3 + 1] = rgb[i * 3 + 2] = g
        return rgb
    if color_type == 4:  # grayscale + alpha
        for i in range(n):
            g = samples[i * 2]
            rgb[i * 3] = rgb[i * 3 + 1] = rgb[i * 3 + 2] = g
        return rgb
    if color_type == 3:  # palette
        for i in range(n):
            idx = samples[i] * 3
            rgb[i * 3] = palette[idx]
            rgb[i * 3 + 1] = palette[idx + 1]
            rgb[i * 3 + 2] = palette[idx + 2]
        return rgb
    raise ValueError("Unsupported PNG colour type %d." % color_type)


# --------------------------------------------------------------------------- #
# PNG encode (used by tests and the demo generator)
# --------------------------------------------------------------------------- #

def encode_png(img: Image) -> bytes:
    stride = img.width * 3
    raw = bytearray()
    for row in range(img.height):
        raw.append(0)  # filter type 0 (None)
        raw += img.rgb[row * stride:(row + 1) * stride]
    compressed = zlib.compress(bytes(raw), 9)

    out = bytearray(_PNG_SIG)
    out += _chunk(b"IHDR", struct.pack(">IIBBBBB", img.width, img.height,
                                       8, 2, 0, 0, 0))
    out += _chunk(b"IDAT", compressed)
    out += _chunk(b"IEND", b"")
    return bytes(out)


def _chunk(ctype: bytes, body: bytes) -> bytes:
    return (struct.pack(">I", len(body)) + ctype + body
            + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF))


def save_png(path: str, img: Image) -> None:
    with open(path, "wb") as fh:
        fh.write(encode_png(img))
