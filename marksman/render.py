"""Render a graphical *recreation* of a session from its stored data.

Every session keeps the shot coordinates (millimetres from the point of aim)
and the name of the target they were scored on.  That is enough to redraw a
clean diagram of the result -- the target rings with the shots plotted on them,
the point of aim, and the group centre -- **without** the original photo or
video.

This is the property that lets the bulky source media be deleted to save space
while the visual result is preserved: the recreation is a pure function of the
coordinate data, which is never thrown away (see ``marksman cleanup``).

Pure standard library: it draws straight into :class:`marksman.imageio.Image`
and writes a PNG.
"""

from __future__ import annotations

import math
import os
from typing import List, Optional, Tuple

from . import imageio
from .models import Session, TargetSpec

# Palette -- a classic black-bull paper target on off-white.
_BG = (252, 250, 246)
_BLACK = (26, 26, 26)
_RINGLINE_ON_WHITE = (70, 70, 70)
_RINGLINE_ON_BLACK = (232, 232, 232)
_POA = (150, 150, 150)
_SHOT = (206, 32, 32)
_SHOT_EDGE = (252, 250, 246)     # thin light rim so shots read on the black
_GROUP = (38, 96, 208)

DEFAULT_SIZE_PX = 720
DEFAULT_MARGIN_PX = 26


# --------------------------------------------------------------------------- #
# Low-level drawing
# --------------------------------------------------------------------------- #

def _fill_disk(img: imageio.Image, cx: int, cy: int, r: float,
               color: Tuple[int, int, int]) -> None:
    ri = max(0, int(round(r)))
    rr = (r + 0.5) * (r + 0.5)
    for y in range(max(0, cy - ri), min(img.height - 1, cy + ri) + 1):
        dy = y - cy
        for x in range(max(0, cx - ri), min(img.width - 1, cx + ri) + 1):
            dx = x - cx
            if dx * dx + dy * dy <= rr:
                img.set(x, y, *color)


def _ring(img: imageio.Image, cx: int, cy: int, r: float,
          color: Tuple[int, int, int], thickness: float = 1.6) -> None:
    if r < 0.5:
        return
    half = thickness / 2.0 + 0.5
    lo, hi = max(0.0, r - half), r + half
    lo2, hi2 = lo * lo, hi * hi
    ri = int(math.ceil(hi))
    for y in range(max(0, cy - ri), min(img.height - 1, cy + ri) + 1):
        dy = y - cy
        for x in range(max(0, cx - ri), min(img.width - 1, cx + ri) + 1):
            dx = x - cx
            d2 = dx * dx + dy * dy
            if lo2 <= d2 <= hi2:
                img.set(x, y, *color)


def _vline(img: imageio.Image, x: int, y0: int, y1: int,
           color: Tuple[int, int, int]) -> None:
    if not (0 <= x < img.width):
        return
    for y in range(max(0, min(y0, y1)), min(img.height - 1, max(y0, y1)) + 1):
        img.set(x, y, *color)


def _hline(img: imageio.Image, y: int, x0: int, x1: int,
           color: Tuple[int, int, int]) -> None:
    if not (0 <= y < img.height):
        return
    for x in range(max(0, min(x0, x1)), min(img.width - 1, max(x0, x1)) + 1):
        img.set(x, y, *color)


# --------------------------------------------------------------------------- #
# Recreation
# --------------------------------------------------------------------------- #

def _black_radius_mm(target: TargetSpec) -> float:
    """Radius of the black aiming area.

    On bullseye-style faces the black covers the higher-value rings; we approximate
    it by the outer edge of the 7-ring, falling back to ~45 % of the outer
    radius when there is no 7-ring.
    """
    for ring in target.rings:
        if ring.value == 7:
            return ring.radius_mm
    return 0.45 * target.outer_radius_mm


def render_session(session: Session, target: Optional[TargetSpec] = None,
                   size_px: int = DEFAULT_SIZE_PX,
                   margin_px: int = DEFAULT_MARGIN_PX,
                   shot_radius_mm: Optional[float] = None,
                   show_group: bool = True) -> imageio.Image:
    """Draw a recreation of ``session`` and return the :class:`Image`.

    ``target`` supplies the scoring rings (if known); without it the shots are
    still plotted against the point of aim.  ``shot_radius_mm`` sizes the shot
    marks to the projectile calibre when available.
    """
    size = max(160, int(size_px))
    cx = cy = size // 2

    # Physical half-extent we must fit: the target face and every shot.
    extents = [1.0]
    if target is not None and target.outer_radius_mm > 0:
        extents.append(target.outer_radius_mm)
    for s in session.shots:
        extents.append(math.hypot(s.x_mm, s.y_mm))
    half_mm = max(extents) * 1.08
    usable_px = (size / 2.0) - margin_px
    mm_per_px = (half_mm / usable_px) if usable_px > 0 else 1.0
    if mm_per_px <= 0:
        mm_per_px = 1.0

    def to_px(x_mm: float, y_mm: float) -> Tuple[int, int]:
        return (int(round(cx + x_mm / mm_per_px)),
                int(round(cy - y_mm / mm_per_px)))   # +y is up

    img = imageio.Image(size, size, bytearray(bytes(_BG) * (size * size)))

    # Rings (and black bull) ------------------------------------------------ #
    if target is not None and target.rings:
        black_r_mm = _black_radius_mm(target)
        if black_r_mm > 0:
            _fill_disk(img, cx, cy, black_r_mm / mm_per_px, _BLACK)
        for ring in target.rings:
            inside_black = ring.radius_mm <= black_r_mm + 1e-6
            color = _RINGLINE_ON_BLACK if inside_black else _RINGLINE_ON_WHITE
            _ring(img, cx, cy, ring.radius_mm / mm_per_px, color)

    # Point of aim crosshair ------------------------------------------------ #
    cross = max(6, size // 36)
    _vline(img, cx, cy - cross, cy + cross, _POA)
    _hline(img, cy, cx - cross, cx + cross, _POA)

    # Group centre + extreme-spread circle ---------------------------------- #
    if show_group and session.shots:
        gx = sum(s.x_mm for s in session.shots) / len(session.shots)
        gy = sum(s.y_mm for s in session.shots) / len(session.shots)
        gpx, gpy = to_px(gx, gy)
        es = session.stats.extreme_spread_mm if session.stats else None
        if es:
            _ring(img, gpx, gpy, (es / 2.0) / mm_per_px, _GROUP, thickness=1.0)
        _vline(img, gpx, gpy - 7, gpy + 7, _GROUP)
        _hline(img, gpy, gpx - 7, gpx + 7, _GROUP)

    # Shots ----------------------------------------------------------------- #
    if shot_radius_mm and shot_radius_mm > 0:
        r_px = max(2.0, shot_radius_mm / mm_per_px)
    else:
        r_px = max(3.0, size / 170.0)
    for s in session.shots:
        px, py = to_px(s.x_mm, s.y_mm)
        _fill_disk(img, px, py, r_px + 1.4, _SHOT_EDGE)
        _fill_disk(img, px, py, r_px, _SHOT)

    return img


def save_recreation(session: Session, path: str,
                    target: Optional[TargetSpec] = None, **kwargs) -> str:
    """Render ``session`` and write it as a PNG at ``path``; return ``path``."""
    img = render_session(session, target=target, **kwargs)
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    imageio.save_png(path, img)
    return path
