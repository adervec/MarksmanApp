"""Built-in target face specifications for airsoft practice.

Airsoft has no single universal scoring face, so these are generic concentric
ring targets sized for common practice distances with 6 mm BBs.  Ring sizes are
reasonable defaults for personal progress tracking, not an official standard --
print whatever face you like and register a custom one with
:func:`uniform_target` + :func:`register`.
"""

from __future__ import annotations

from typing import List, Optional

from .models import Ring, TargetSpec


def uniform_target(
    name: str,
    ten_ring_diameter_mm: float,
    ring_step_mm: float,
    lowest_value: int = 1,
    highest_value: int = 10,
    decimal_scoring: bool = False,
    face_size_mm: Optional[float] = None,
) -> TargetSpec:
    """Build a target whose rings are evenly spaced.

    ``ring_step_mm`` is the *radial* distance added between consecutive rings.
    The innermost ring (``highest_value``) has diameter ``ten_ring_diameter_mm``.
    """
    rings = []
    r10 = ten_ring_diameter_mm / 2.0
    for i, value in enumerate(range(highest_value, lowest_value - 1, -1)):
        radius = r10 + i * ring_step_mm
        rings.append(Ring(value=value, diameter_mm=radius * 2.0))
    return TargetSpec(
        name=name,
        rings=rings,
        decimal_scoring=decimal_scoring,
        face_width_mm=face_size_mm,
        face_height_mm=face_size_mm,
    )


# --------------------------------------------------------------------------- #
# Standard airsoft practice faces (generic concentric bullseyes)
# --------------------------------------------------------------------------- #

def _airsoft_practice_10m() -> TargetSpec:
    # General-purpose 10 m bullseye: 40 mm ten-ring, 20 mm radial step.
    t = uniform_target("Airsoft Practice 10m", ten_ring_diameter_mm=40.0,
                       ring_step_mm=20.0, face_size_mm=400.0)
    t.notes = "Generic 10 m practice bullseye for 6 mm BBs."
    return t


def _airsoft_cqb_7m() -> TargetSpec:
    # Larger rings for close, fast shooting.
    t = uniform_target("Airsoft CQB 7m", ten_ring_diameter_mm=70.0,
                       ring_step_mm=30.0, face_size_mm=600.0)
    t.notes = "Close-range practice face (CQB distances)."
    return t


def _airsoft_precision_20m() -> TargetSpec:
    # Tighter rings for longer-range DMR / sniper practice.
    t = uniform_target("Airsoft Precision 20m", ten_ring_diameter_mm=25.0,
                       ring_step_mm=15.0, face_size_mm=350.0)
    t.notes = "Tighter face for longer-range (DMR / sniper) practice."
    return t


_BUILTINS = {}  # type: dict


def _register_builtin(spec: TargetSpec) -> None:
    _BUILTINS[spec.name.lower()] = spec


for _factory in (
    _airsoft_practice_10m,
    _airsoft_cqb_7m,
    _airsoft_precision_20m,
):
    _register_builtin(_factory())


def register(spec: TargetSpec) -> None:
    """Register a custom target so it can be looked up by name."""
    _BUILTINS[spec.name.lower()] = spec


def get_target(name: str) -> TargetSpec:
    """Look up a target by name (case-insensitive). Raises KeyError if absent."""
    key = name.strip().lower()
    if key not in _BUILTINS:
        raise KeyError(
            "Unknown target %r. Known: %s" % (name, ", ".join(list_targets()))
        )
    return _BUILTINS[key]


def list_targets() -> List[str]:
    """Names of all registered targets, sorted."""
    return sorted(spec.name for spec in _BUILTINS.values())
