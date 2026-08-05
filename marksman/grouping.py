"""The core analysis: turn a set of shots into marksmanship metrics.

These are the standard measures used to judge a group on a target:

* **Extreme spread** (a.k.a. *group size*): the largest centre-to-centre
  distance between any two shots.  The single most quoted number; sensitive to
  the worst flyer.
* **Mean radius**: the average distance of the shots from their own centre.  A
  more stable measure of precision than extreme spread.
* **RMS radius / CEP / std-dev**: further precision descriptors.
* **POA-POI offset**: how far the group's centre sits from the point of aim --
  i.e. how well the sights are zeroed (accuracy, as opposed to precision).
* **Score**: points from the target's scoring rings.

Everything is standard-library only and works on plain
:class:`~marksman.models.Shot` objects (millimetres, POA-centred).
"""

from __future__ import annotations

import math
from typing import Iterable, Optional, Sequence, Tuple

from .models import Shot, TargetSpec, GroupStats


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #

def score_shot(
    shot: Shot,
    target: TargetSpec,
    projectile_mm: float = 0.0,
) -> float:
    """Score a single shot against a target face.

    A shot scores the value of the smallest ring whose zone it touches.  When
    ``projectile_mm`` is given, the shot's *edge* is used (the classic "breaks
    the line scores the higher value" rule): a shot counts for a ring if its
    inner edge reaches into it.

    With ``decimal_scoring`` the score is interpolated to tenths the way
    precision targets are scored (e.g. 10.9 dead centre).
    """
    r = shot.radius_mm
    edge = max(0.0, r - projectile_mm / 2.0)   # closest approach to centre

    if target.decimal_scoring:
        return _decimal_score(edge, target)

    for ring in target.rings:  # innermost first
        if edge <= ring.radius_mm + 1e-9:
            return float(ring.value)
    return 0.0


def _decimal_score(edge_mm: float, target: TargetSpec) -> float:
    """Precision-style tenth-ring scoring.

    Inside the 10-ring the score runs from the ring value (at the ring line)
    up to value+0.9 (dead centre).  Outside it, each ring's worth of radius is
    one whole point, sub-divided into tenths.  Result is clamped to
    ``[0, max_value + 0.9]`` and truncated to 0.1.
    """
    ten_r = target.ten_ring_radius_mm
    step = target.ring_step_mm
    max_v = target.max_value
    if ten_r <= 0 or step <= 0:
        return 0.0

    if edge_mm <= ten_r:
        raw = max_v + 0.9 * (1.0 - edge_mm / ten_r)
    else:
        raw = max_v - (edge_mm - ten_r) / step

    raw = max(0.0, min(max_v + 0.9, raw))
    # Truncate to tenths (a shot must fully earn the tenth it sits on).
    return math.floor(raw * 10 + 1e-9) / 10.0


def score_shots(
    shots: Sequence[Shot],
    target: TargetSpec,
    projectile_mm: float = 0.0,
    annotate: bool = True,
) -> float:
    """Score every shot, optionally writing each ``shot.score``; return total."""
    total = 0.0
    for s in shots:
        v = score_shot(s, target, projectile_mm)
        if annotate:
            s.score = v
        total += v
    return total


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #

def centroid(shots: Sequence[Shot]) -> Tuple[float, float]:
    n = len(shots)
    if n == 0:
        return (0.0, 0.0)
    return (sum(s.x_mm for s in shots) / n, sum(s.y_mm for s in shots) / n)


def extreme_spread(shots: Sequence[Shot]) -> float:
    """Largest centre-to-centre distance between any two shots."""
    n = len(shots)
    if n < 2:
        return 0.0
    best = 0.0
    for i in range(n):
        xi, yi = shots[i].x_mm, shots[i].y_mm
        for j in range(i + 1, n):
            d = math.hypot(shots[j].x_mm - xi, shots[j].y_mm - yi)
            if d > best:
                best = d
    return best


# --------------------------------------------------------------------------- #
# Full analysis
# --------------------------------------------------------------------------- #

def analyze_group(
    shots: Sequence[Shot],
    target: Optional[TargetSpec] = None,
    projectile_mm: float = 0.0,
) -> GroupStats:
    """Compute the full :class:`GroupStats` for a set of shots.

    ``target`` is optional: omit it to get pure geometry (no score).  When
    given, every shot is scored (and its ``score`` field annotated).
    """
    n = len(shots)
    if n == 0:
        raise ValueError("Cannot analyse an empty group (no shots).")

    cx, cy = centroid(shots)

    # Radii about the group centre (precision) and about POA handled separately.
    radii = [math.hypot(s.x_mm - cx, s.y_mm - cy) for s in shots]
    mean_radius = sum(radii) / n
    rms_radius = math.sqrt(sum(r * r for r in radii) / n)

    # CEP: the median radius is the radius containing 50% of shots.
    cep = _median(radii)

    # Per-axis spread (population standard deviation).
    var_x = sum((s.x_mm - cx) ** 2 for s in shots) / n
    var_y = sum((s.y_mm - cy) ** 2 for s in shots) / n
    std_x = math.sqrt(var_x)
    std_y = math.sqrt(var_y)

    xs = [s.x_mm for s in shots]
    ys = [s.y_mm for s in shots]
    bbox_w = max(xs) - min(xs)
    bbox_h = max(ys) - min(ys)

    es = extreme_spread(shots)

    poa_offset = math.hypot(cx, cy)
    poa_angle = math.degrees(math.atan2(cy, cx)) if poa_offset > 1e-12 else 0.0

    total_score = None
    max_possible = None
    avg_score = None
    if target is not None:
        total_score = score_shots(shots, target, projectile_mm, annotate=True)
        max_possible = float(target.max_value) * n
        if target.decimal_scoring:
            max_possible = (target.max_value + 0.9) * n
        avg_score = total_score / n

    return GroupStats(
        shot_count=n,
        extreme_spread_mm=es,
        mean_radius_mm=mean_radius,
        rms_radius_mm=rms_radius,
        cep_mm=cep,
        std_x_mm=std_x,
        std_y_mm=std_y,
        bounding_width_mm=bbox_w,
        bounding_height_mm=bbox_h,
        center_x_mm=cx,
        center_y_mm=cy,
        poa_offset_mm=poa_offset,
        poa_offset_angle_deg=poa_angle,
        total_score=total_score,
        max_possible_score=max_possible,
        average_score=avg_score,
    )


def _median(values: Iterable[float]) -> float:
    vals = sorted(values)
    n = len(vals)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0
