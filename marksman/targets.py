"""Target face specifications: concentric scoring rings.

The app itself ships **one** neutral practice face so it is usable with no
content installed.  Every other face -- sized for a particular discipline and
distance -- comes from an equipment pack (:mod:`marksman.packs`), which
registers it here at startup.

Ring sizes are reasonable defaults for personal progress tracking, not an
official standard.  Print whatever face you like and register a custom one
with :func:`uniform_target` + :func:`register`.
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
# The one built-in face
# --------------------------------------------------------------------------- #

def _practice_face() -> TargetSpec:
    # Mid-sized concentric bullseye that suits most things at a few metres.
    # Anything discipline-specific belongs in a pack, not here.
    t = uniform_target("Practice Face", ten_ring_diameter_mm=50.0,
                       ring_step_mm=25.0, face_size_mm=500.0)
    t.notes = ("Neutral 10-ring practice face. Install an equipment pack for "
               "faces sized to your discipline.")
    return t


_BUILTINS = {}  # type: dict

_BUILTINS[_practice_face().name.lower()] = _practice_face()


def register(spec: TargetSpec) -> None:
    """Register a custom target so it can be looked up by name."""
    _BUILTINS[spec.name.lower()] = spec


_packs_tried = False


def _ensure_packs() -> None:
    """Load the installed packs' faces on first lookup.

    The app's entry points load packs explicitly (they know which database's
    settings apply), but importing :mod:`marksman.targets` on its own should
    still see the faces a pack provides -- otherwise using this as a library
    means knowing to call :func:`marksman.packs.load` first.

    Imported here rather than at module scope because packs imports us.
    """
    global _packs_tried
    if _packs_tried:
        return
    _packs_tried = True
    try:
        from . import packs
        packs.active()
    except Exception:            # a broken pack must not break target lookup
        pass


def get_target(name: str) -> TargetSpec:
    """Look up a target by name (case-insensitive). Raises KeyError if absent."""
    _ensure_packs()
    key = name.strip().lower()
    if key not in _BUILTINS:
        raise KeyError(
            "Unknown target %r. Known: %s" % (name, ", ".join(list_targets()))
        )
    return _BUILTINS[key]


def list_targets() -> List[str]:
    """Names of all registered targets, sorted."""
    _ensure_packs()
    return sorted(spec.name for spec in _BUILTINS.values())
