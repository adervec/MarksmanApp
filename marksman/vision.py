"""Extract shot positions from a marked-up target image.

This is the perception half of the core feature.  Given a photo or scan of a
target on which the groupings have been marked, it returns a list of
:class:`~marksman.models.Shot` in millimetres relative to the point of aim --
exactly what :mod:`marksman.grouping` consumes.

Two detection modes:

* ``"marker"`` -- the shooter has dotted/circled each shot with a coloured pen
  (red by default).  Robust and the recommended workflow.
* ``"holes"``  -- detect dark impact marks directly.  Works on clean scans of
  light targets; less reliable on busy photos.

Calibration (pixels -> millimetres) can be supplied directly, derived from the
known physical size of the target face, or from two reference points a known
distance apart.  The point of aim defaults to the image centre but can be set
explicitly or auto-detected from the dark bull.

Pure standard library: images are decoded by :mod:`marksman.imageio` (PNG, or
anything Pillow reads if it is installed) and all pixel work is done here in
plain Python -- no numpy, no OpenCV.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .models import Shot
from . import imageio


# Named colour presets as HSV gates: (hue ranges in degrees, min S, min V).
# Hue ranges are inclusive; multiple ranges handle hue wrap-around (red).
_COLOR_PRESETS = {
    "red":    ([(0, 12), (348, 360)], 0.40, 0.30),
    "green":  ([(80, 165)], 0.30, 0.20),
    "blue":   ([(190, 260)], 0.30, 0.20),
    "orange": ([(13, 40)], 0.45, 0.40),
    "purple": ([(265, 320)], 0.25, 0.20),
    "yellow": ([(45, 70)], 0.40, 0.55),
}


@dataclass
class Blob:
    """A detected connected region in image (pixel) coordinates."""

    x_px: float          # centroid x (column)
    y_px: float          # centroid y (row)
    size_px: int         # number of pixels
    width_px: int
    height_px: int


@dataclass
class DetectionResult:
    """Outcome of analysing an image: shots plus diagnostics."""

    shots: List[Shot]
    blobs: List[Blob]
    center_px: Tuple[float, float]
    mm_per_px: float
    image_size_px: Tuple[int, int]   # (width, height)
    mode: str

    def __len__(self) -> int:
        return len(self.shots)


# --------------------------------------------------------------------------- #
# Colour helpers
# --------------------------------------------------------------------------- #

def _rgb_to_hsv(r: int, g: int, b: int) -> Tuple[float, float, float]:
    """RGB(0-255) -> (H in [0,360), S in [0,1], V in [0,1])."""
    maxc = r if r >= g and r >= b else (g if g >= b else b)
    minc = r if r <= g and r <= b else (g if g <= b else b)
    v = maxc / 255.0
    delta = maxc - minc
    if delta == 0:
        return (0.0, 0.0, v)
    s = delta / maxc
    if maxc == r:
        h = ((g - b) / delta) % 6.0
    elif maxc == g:
        h = ((b - r) / delta) + 2.0
    else:
        h = ((r - g) / delta) + 4.0
    return (h * 60.0 % 360.0, s, v)


# --------------------------------------------------------------------------- #
# Masking  (mask is a bytearray of 0/1, length width*height, row-major)
# --------------------------------------------------------------------------- #

def color_mask(
    img: imageio.Image,
    color: str = "red",
    rgb_target: Optional[Tuple[int, int, int]] = None,
    rgb_tolerance: int = 60,
) -> bytearray:
    """Mask of pixels matching a colour.

    Use a named ``color`` preset (HSV-based, lighting tolerant) or pass an
    explicit ``rgb_target`` with a Euclidean ``rgb_tolerance``.
    """
    data = img.rgb
    n = img.width * img.height
    mask = bytearray(n)

    if rgb_target is not None:
        tr, tg, tb = rgb_target
        tol2 = rgb_tolerance * rgb_tolerance
        for i in range(n):
            j = i * 3
            dr = data[j] - tr
            dg = data[j + 1] - tg
            db = data[j + 2] - tb
            if dr * dr + dg * dg + db * db <= tol2:
                mask[i] = 1
        return mask

    preset = _COLOR_PRESETS.get(color.lower())
    if preset is None:
        raise ValueError(
            "Unknown colour %r. Known: %s (or pass rgb_target)."
            % (color, ", ".join(_COLOR_PRESETS))
        )
    hue_ranges, min_s, min_v = preset
    for i in range(n):
        j = i * 3
        h, s, v = _rgb_to_hsv(data[j], data[j + 1], data[j + 2])
        if s < min_s or v < min_v:
            continue
        for lo, hi in hue_ranges:
            if lo <= h <= hi:
                mask[i] = 1
                break
    return mask


def dark_mask(img: imageio.Image, threshold: int = 70) -> bytearray:
    """Mask of dark pixels (luma at or below ``threshold``, 0-255)."""
    data = img.rgb
    n = img.width * img.height
    mask = bytearray(n)
    for i in range(n):
        j = i * 3
        luma = 0.299 * data[j] + 0.587 * data[j + 1] + 0.114 * data[j + 2]
        if luma <= threshold:
            mask[i] = 1
    return mask


# --------------------------------------------------------------------------- #
# Connected components (hand-rolled, 8-connectivity)
# --------------------------------------------------------------------------- #

def find_blobs(
    mask: bytearray,
    width: int,
    height: int,
    min_size: int = 12,
    max_size: Optional[int] = None,
) -> List[Blob]:
    """Label connected '1' regions in ``mask`` and return them as blobs."""
    visited = bytearray(len(mask))
    blobs = []  # type: List[Blob]

    for start in range(len(mask)):
        if not mask[start] or visited[start]:
            continue
        stack = [start]
        visited[start] = 1
        sum_x = 0
        sum_y = 0
        size = 0
        minx = width
        maxx = -1
        miny = height
        maxy = -1
        while stack:
            cur = stack.pop()
            cy, cx = divmod(cur, width)
            sum_x += cx
            sum_y += cy
            size += 1
            if cx < minx:
                minx = cx
            if cx > maxx:
                maxx = cx
            if cy < miny:
                miny = cy
            if cy > maxy:
                maxy = cy
            # 8-connected neighbours
            for dy in (-1, 0, 1):
                ny = cy + dy
                if ny < 0 or ny >= height:
                    continue
                base = ny * width
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx = cx + dx
                    if nx < 0 or nx >= width:
                        continue
                    nb = base + nx
                    if mask[nb] and not visited[nb]:
                        visited[nb] = 1
                        stack.append(nb)

        if size < min_size:
            continue
        if max_size is not None and size > max_size:
            continue
        blobs.append(Blob(
            x_px=sum_x / size,
            y_px=sum_y / size,
            size_px=size,
            width_px=maxx - minx + 1,
            height_px=maxy - miny + 1,
        ))

    blobs.sort(key=lambda b: b.size_px, reverse=True)
    return blobs


# --------------------------------------------------------------------------- #
# Calibration helpers
# --------------------------------------------------------------------------- #

def detect_bull_center(img: imageio.Image, threshold: int = 70) -> Tuple[float, float]:
    """Estimate the point of aim as the centroid of the largest dark region."""
    blobs = find_blobs(dark_mask(img, threshold), img.width, img.height, min_size=50)
    if not blobs:
        return (img.width / 2.0, img.height / 2.0)
    return (blobs[0].x_px, blobs[0].y_px)


def mm_per_px_from_face(image_width_px: int, face_width_mm: float) -> float:
    """Scale assuming the image width spans the whole target face."""
    if image_width_px <= 0:
        raise ValueError("image_width_px must be positive")
    return face_width_mm / image_width_px


def mm_per_px_from_reference(
    p1_px: Tuple[float, float],
    p2_px: Tuple[float, float],
    known_distance_mm: float,
) -> float:
    """Scale from two reference points a known real distance apart."""
    d = math.hypot(p2_px[0] - p1_px[0], p2_px[1] - p1_px[1])
    if d <= 1e-9:
        raise ValueError("Reference points coincide.")
    return known_distance_mm / d


# --------------------------------------------------------------------------- #
# Pixel blobs -> millimetre shots
# --------------------------------------------------------------------------- #

def blobs_to_shots(
    blobs: Sequence[Blob],
    center_px: Tuple[float, float],
    mm_per_px: float,
) -> List[Shot]:
    """Convert pixel blobs to POA-centred millimetre shots (y up)."""
    cx, cy = center_px
    shots = []
    for b in blobs:
        x_mm = (b.x_px - cx) * mm_per_px
        # Image y grows downward; our convention is y up, so negate.
        y_mm = -(b.y_px - cy) * mm_per_px
        shots.append(Shot(x_mm=x_mm, y_mm=y_mm))
    return shots


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #

def analyze_image(
    path: str,
    mode: str = "marker",
    color: str = "red",
    rgb_target: Optional[Tuple[int, int, int]] = None,
    rgb_tolerance: int = 60,
    dark_threshold: int = 70,
    min_blob_size: int = 12,
    max_blob_size: Optional[int] = None,
    center_px: Optional[Tuple[float, float]] = None,
    auto_center: bool = False,
    mm_per_px: Optional[float] = None,
    face_width_mm: Optional[float] = None,
    reference: Optional[Tuple[Tuple[float, float], Tuple[float, float], float]] = None,
) -> DetectionResult:
    """Full pipeline: image file -> :class:`DetectionResult`.

    Calibration precedence (mm/px): ``mm_per_px`` > ``reference`` > ``face_width_mm``.
    Centre precedence: ``center_px`` > ``auto_center`` (dark bull) > image centre.

    Raises ``ValueError`` if no scale can be determined.
    """
    img = imageio.load(path)
    w, h = img.width, img.height

    # --- detect shot blobs ---
    if mode == "marker":
        mask = color_mask(img, color=color, rgb_target=rgb_target,
                          rgb_tolerance=rgb_tolerance)
    elif mode == "holes":
        mask = dark_mask(img, threshold=dark_threshold)
    else:
        raise ValueError("Unknown mode %r (use 'marker' or 'holes')." % mode)
    blobs = find_blobs(mask, w, h, min_size=min_blob_size, max_size=max_blob_size)

    # --- centre (point of aim) ---
    if center_px is not None:
        center = center_px
    elif auto_center:
        center = detect_bull_center(img, threshold=dark_threshold)
    else:
        center = (w / 2.0, h / 2.0)

    # --- scale ---
    if mm_per_px is not None:
        scale = mm_per_px
    elif reference is not None:
        scale = mm_per_px_from_reference(reference[0], reference[1], reference[2])
    elif face_width_mm is not None:
        scale = mm_per_px_from_face(w, face_width_mm)
    else:
        raise ValueError("No scale: provide mm_per_px, reference, or face_width_mm.")

    shots = blobs_to_shots(blobs, center, scale)
    return DetectionResult(
        shots=shots,
        blobs=blobs,
        center_px=center,
        mm_per_px=scale,
        image_size_px=(w, h),
        mode=mode,
    )
