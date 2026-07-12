"""Generate the Marksman logo/app icon with the built-in PNG writer (no deps).

Draws a reticle-over-target badge with a tight airsoft BB group, reusing the same
pure-Python drawing primitives as the session recreations. Produces a PNG and a
Windows ``.ico`` (a 256px PNG embedded in the ICO container, which Windows 10/11
render for shortcut icons).

    python -m marksman logo --out assets      # marksman.png + marksman.ico
"""

from __future__ import annotations

import os
import struct
from typing import Tuple

from . import imageio
from .render import _fill_disk, _hline, _ring, _vline

# Tacticool badge palette: deep navy field, neon rings, orange BB group.
_BG = (16, 20, 28)
_FACE = (24, 30, 42)
_RING = (45, 212, 160)
_RING_DIM = (32, 120, 96)
_RETICLE = (226, 232, 240)
_BB = (255, 150, 40)
_BB_EDGE = (24, 30, 42)


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

    # Gapped reticle crosshair (vertical + horizontal, clear centre).
    reach = int(face_r * 0.86)
    gap = int(size * 0.11)
    tw = max(1, int(unit * 0.9))          # half thickness
    for d in range(-tw, tw + 1):
        _vline(img, cx + d, cy - reach, cy - gap, _RETICLE)
        _vline(img, cx + d, cy + gap, cy + reach, _RETICLE)
        _hline(img, cy + d, cx - reach, cx - gap, _RETICLE)
        _hline(img, cy + d, cx + gap, cx + reach, _RETICLE)

    # A tight BB group, high-and-right of centre (a good honest cluster).
    group_r = size * 0.055
    offsets = [(-0.6, -0.9), (0.7, -0.4), (-0.2, 0.5), (1.1, 0.7), (0.2, -0.1)]
    for ox, oy in offsets:
        px = int(cx + size * 0.06 + ox * group_r)
        py = int(cy - size * 0.06 + oy * group_r)
        _fill_disk(img, px, py, group_r + max(1.5, unit * 0.5), _BB_EDGE)
        _fill_disk(img, px, py, group_r, _BB)
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
