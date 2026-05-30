"""Built-in target face specifications.

Ring diameters are the **outer** diameter of each scoring zone, in millimetres,
and follow the published ISSF / NRA nominal dimensions.  They are good enough
for tracking personal progress; for official scoring always defer to the actual
printed target.

Add your own with :func:`uniform_target` or by constructing a
:class:`~marksman.models.TargetSpec` directly, then register it via
:func:`register`.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from .models import Ring, TargetSpec

_MM_PER_IN = 25.4


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


def _rings(pairs: List[Tuple[int, float]]) -> List[Ring]:
    return [Ring(value=v, diameter_mm=d) for v, d in pairs]


# --------------------------------------------------------------------------- #
# Standard faces
# --------------------------------------------------------------------------- #

def _issf_air_pistol() -> TargetSpec:
    # ISSF 10 m Air Pistol. Inner-ten 5.0 mm; rings step 8 mm radial.
    pairs = [
        (10, 11.5), (9, 27.5), (8, 43.5), (7, 59.5), (6, 75.5),
        (5, 91.5), (4, 107.5), (3, 123.5), (2, 139.5), (1, 155.5),
    ]
    return TargetSpec(
        name="ISSF 10m Air Pistol",
        rings=_rings(pairs),
        decimal_scoring=True,
        face_width_mm=170.0,
        face_height_mm=170.0,
        notes="10 m air pistol; pellet 4.5 mm.",
    )


def _issf_air_rifle() -> TargetSpec:
    # ISSF 10 m Air Rifle. Tiny rings; 10-ring 0.5 mm, step 2.5 mm radial.
    pairs = [
        (10, 0.5), (9, 5.5), (8, 10.5), (7, 15.5), (6, 20.5),
        (5, 25.5), (4, 30.5), (3, 35.5), (2, 40.5), (1, 45.5),
    ]
    return TargetSpec(
        name="ISSF 10m Air Rifle",
        rings=_rings(pairs),
        decimal_scoring=True,
        face_width_mm=80.0,
        face_height_mm=80.0,
        notes="10 m air rifle; pellet 4.5 mm (pellet is far larger than the 10-ring -- edge scoring matters).",
    )


def _issf_50m_rifle() -> TargetSpec:
    pairs = [
        (10, 10.4), (9, 26.4), (8, 42.4), (7, 58.4), (6, 74.4),
        (5, 90.4), (4, 106.4), (3, 122.4), (2, 138.4), (1, 154.4),
    ]
    return TargetSpec(
        name="ISSF 50m Rifle",
        rings=_rings(pairs),
        decimal_scoring=True,
        face_width_mm=250.0,
        face_height_mm=250.0,
        notes="50 m smallbore rifle prone/3P.",
    )


def _issf_25m_precision_pistol() -> TargetSpec:
    # Also the 50 m pistol face. 10-ring 50 mm, step 25 mm radial.
    pairs = [
        (10, 50.0), (9, 100.0), (8, 150.0), (7, 200.0), (6, 250.0),
        (5, 300.0), (4, 350.0), (3, 400.0), (2, 450.0), (1, 500.0),
    ]
    return TargetSpec(
        name="ISSF 25m/50m Precision Pistol",
        rings=_rings(pairs),
        decimal_scoring=False,
        face_width_mm=550.0,
        face_height_mm=550.0,
        notes="25 m precision / 50 m pistol face.",
    )


def _nra_b8() -> TargetSpec:
    # NRA B-8 (25 yd timed/rapid). Diameters in inches -> mm.
    in_pairs = [
        (10, 3.36), (9, 5.54), (8, 8.00), (7, 11.00), (6, 14.80), (5, 19.68),
    ]
    pairs = [(v, d * _MM_PER_IN) for v, d in in_pairs]
    return TargetSpec(
        name="NRA B-8",
        rings=_rings(pairs),
        decimal_scoring=False,
        face_width_mm=21.0 * _MM_PER_IN,
        face_height_mm=21.0 * _MM_PER_IN,
        notes="NRA B-8 25-yard pistol; X-ring 1.695 in.",
    )


_BUILTINS = {}  # type: dict


def _register_builtin(spec: TargetSpec) -> None:
    _BUILTINS[spec.name.lower()] = spec


for _factory in (
    _issf_air_pistol,
    _issf_air_rifle,
    _issf_50m_rifle,
    _issf_25m_precision_pistol,
    _nra_b8,
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
