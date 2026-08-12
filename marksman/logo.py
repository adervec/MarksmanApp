"""Generate the Marksman logo/app icon with the built-in PNG writer (no deps).

Draws a foam dart striking a target: concentric teal rings on a dark badge,
with a cream foam dart -- orange tip, foam body -- coming in from the lower
left. Reuses the same pure-Python drawing primitives as the session
recreations, and produces a PNG plus a Windows ``.ico`` (a 256px PNG in an ICO
container, which Windows 10/11 render for shortcut icons).

The dart shape is generic: a soft cylinder with a rounded head, drawn from
scratch here. No third-party brand, logo, product line or trade dress is used
or referenced -- see THIRD_PARTY_NOTICES.md.

    python -m marksman logo --out assets      # marksman.png + marksman.ico
"""

from __future__ import annotations

import math
import os
import struct
from typing import Tuple

from . import imageio
from .render import _fill_disk, _hline, _ring, _vline

# Badge palette: warm slate field, teal rings, a cream dart with an orange tip.
_BG = (20, 24, 33)
_FACE = (29, 36, 48)
_RING = (47, 196, 178)
_RING_DIM = (28, 116, 108)
_FOAM = (245, 235, 218)
_FOAM_SHADE = (206, 194, 175)
_TIP = (255, 122, 41)
_OUTLINE = (20, 18, 14)


def _capsule(img: imageio.Image, x0: float, y0: float, x1: float, y1: float,
             r: float, color: Tuple[int, int, int]) -> None:
    """Fill every pixel within ``r`` of the segment (x0,y0)-(x1,y1).

    A rounded cylinder is exactly what a foam dart is, so the whole dart is
    two of these: the foam body and the softer tip.
    """
    dx, dy = x1 - x0, y1 - y0
    length2 = dx * dx + dy * dy
    lo_x = int(math.floor(min(x0, x1) - r))
    hi_x = int(math.ceil(max(x0, x1) + r))
    lo_y = int(math.floor(min(y0, y1) - r))
    hi_y = int(math.ceil(max(y0, y1) + r))
    rr = r * r
    for y in range(max(0, lo_y), min(img.height - 1, hi_y) + 1):
        for x in range(max(0, lo_x), min(img.width - 1, hi_x) + 1):
            px, py = x - x0, y - y0
            t = 0.0 if length2 <= 0 else (px * dx + py * dy) / length2
            t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
            ex, ey = px - t * dx, py - t * dy
            if ex * ex + ey * ey <= rr:
                img.set(x, y, *color)


def make_logo(size: int = 512) -> imageio.Image:
    """Draw the logo at ``size`` x ``size`` and return the Image."""
    size = max(64, int(size))
    cx = cy = size // 2
    img = imageio.Image(size, size, bytearray(bytes(_BG) * (size * size)))

    face_r = size * 0.46
    _fill_disk(img, cx, cy, face_r, _FACE)

    # Concentric target rings.
    unit = size / 100.0
    for i, frac in enumerate((0.92, 0.68, 0.44, 0.22)):
        _ring(img, cx, cy, face_r * frac, _RING if i % 2 == 0 else _RING_DIM,
              thickness=max(2.0, unit * 1.1))

    # The dart: flying in from the lower left, tip just past the middle.
    tip_x, tip_y = cx + size * 0.06, cy - size * 0.06
    ux, uy = 0.82, 0.57                      # unit vector back down the shaft
    reach = size * 0.58
    tail_x, tail_y = tip_x - ux * reach, tip_y + uy * reach
    body_r = size * 0.058                    # slim: a dart is ~5x longer than wide
    tip_len = body_r * 1.55
    neck_x, neck_y = tip_x - ux * tip_len, tip_y + uy * tip_len

    # Outline first so body and tip sit inside a single dark rim.
    _capsule(img, tail_x, tail_y, tip_x, tip_y, body_r * 1.34 + unit * 0.7,
             _OUTLINE)
    _capsule(img, tail_x, tail_y, neck_x, neck_y, body_r, _FOAM)
    # A soft shadow along the underside gives the foam some roundness.
    _capsule(img, tail_x + uy * body_r * 0.5, tail_y + ux * body_r * 0.5,
             neck_x + uy * body_r * 0.5, neck_y + ux * body_r * 0.5,
             body_r * 0.26, _FOAM_SHADE)
    _capsule(img, neck_x, neck_y, tip_x, tip_y, body_r * 1.34, _TIP)

    return img


def _ico_from_png(png: bytes) -> bytes:
    """Wrap a (<=256px square) PNG in a single-image ICO container."""
    # ICONDIR(6) + one ICONDIRENTRY(16); width/height 0 means 256.
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 6 + 16)
    return header + entry + png


def save_logo(out_dir: str, size: int = 512) -> Tuple[str, str]:
    """Write ``marksman.png`` (``size``px) and ``marksman.ico`` (256px). Returns paths."""
    os.makedirs(out_dir, exist_ok=True)
    png_path = os.path.join(out_dir, "marksman.png")
    ico_path = os.path.join(out_dir, "marksman.ico")
    imageio.save_png(png_path, make_logo(size))
    ico_png = imageio.encode_png(make_logo(256))
    with open(ico_path, "wb") as fh:
        fh.write(_ico_from_png(ico_png))
    return png_path, ico_path


if __name__ == "__main__":  # tiny self-check
    img = make_logo(128)
    assert img.width == img.height == 128
    png = imageio.encode_png(img)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert _ico_from_png(png)[:4] == b"\x00\x00\x01\x00"
    print("logo self-check OK")
