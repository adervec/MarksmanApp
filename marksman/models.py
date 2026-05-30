"""Plain data structures used across Marksman.

Everything here is standard-library only and JSON round-trippable via
``to_dict`` / ``from_dict`` so the storage layer stays trivial.

Coordinate convention
---------------------
Shot positions are stored in **millimetres** in a target-centred frame:

* the origin ``(0, 0)`` is the *point of aim* (POA) -- normally the centre of
  the target face,
* ``+x`` points to the shooter's right,
* ``+y`` points up.

Keeping a single physical convention means the grouping maths, scoring and
progress tracking never have to care where the numbers came from (a vision
analysis of a photo, a manual click, or a synthetic test).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Any, List, Optional


# --------------------------------------------------------------------------- #
# Weapon categories
# --------------------------------------------------------------------------- #

# A small, opinionated set of categories covering common range disciplines.
# Categories are stored as plain strings so users can add their own, but these
# are offered for autocomplete / validation and grouping.
STANDARD_CATEGORIES = (
    "Air Pistol",
    "Air Rifle",
    "Rimfire Pistol",
    "Rimfire Rifle",
    "Centerfire Pistol",
    "Centerfire Rifle",
    "Shotgun",
    "Other",
)


def normalize_category(name: str) -> str:
    """Return a canonical category name (case/space tolerant).

    Unknown categories are accepted verbatim (title-cased) so the app never
    rejects a user's discipline -- it just keeps aggregation consistent.
    """
    cleaned = " ".join(name.strip().split())
    for std in STANDARD_CATEGORIES:
        if cleaned.lower() == std.lower():
            return std
    return cleaned.title() if cleaned else "Other"


# --------------------------------------------------------------------------- #
# Target face specification
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Ring:
    """One scoring ring zone.

    ``value`` is the points awarded for a shot inside ``diameter_mm`` (and
    outside the next, smaller ring).  Rings are described by the **outer
    diameter** of their zone, in millimetres.
    """

    value: int
    diameter_mm: float

    @property
    def radius_mm(self) -> float:
        return self.diameter_mm / 2.0


@dataclass
class TargetSpec:
    """A target face: a stack of concentric scoring rings.

    ``rings`` need not be pre-sorted; they are normalised on construction so the
    highest value (innermost, smallest diameter) comes first.

    ``decimal_scoring`` enables tenth-of-a-point scoring (ISSF style) for the
    precision air disciplines.
    """

    name: str
    rings: List[Ring]
    decimal_scoring: bool = False
    # Real-world full size of the printed target face (used by the vision layer
    # to calibrate pixels -> mm when a scale isn't given explicitly).
    face_width_mm: Optional[float] = None
    face_height_mm: Optional[float] = None
    notes: str = ""

    def __post_init__(self) -> None:
        # Innermost (smallest diameter, highest value) first.
        self.rings = sorted(self.rings, key=lambda r: r.diameter_mm)

    @property
    def max_value(self) -> int:
        return max((r.value for r in self.rings), default=0)

    @property
    def outer_radius_mm(self) -> float:
        """Radius beyond which a shot scores zero (a miss)."""
        return max((r.radius_mm for r in self.rings), default=0.0)

    @property
    def ten_ring_radius_mm(self) -> float:
        """Radius of the highest-value ring (the '10' on most faces)."""
        if not self.rings:
            return 0.0
        return self.rings[0].radius_mm

    @property
    def ring_step_mm(self) -> float:
        """Median radial spacing between consecutive ring lines (for decimals)."""
        radii = sorted(r.radius_mm for r in self.rings)
        diffs = [b - a for a, b in zip(radii, radii[1:]) if b - a > 1e-9]
        if not diffs:
            return self.ten_ring_radius_mm or 1.0
        diffs.sort()
        mid = len(diffs) // 2
        if len(diffs) % 2:
            return diffs[mid]
        return (diffs[mid - 1] + diffs[mid]) / 2.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "rings": [{"value": r.value, "diameter_mm": r.diameter_mm} for r in self.rings],
            "decimal_scoring": self.decimal_scoring,
            "face_width_mm": self.face_width_mm,
            "face_height_mm": self.face_height_mm,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TargetSpec":
        return cls(
            name=d["name"],
            rings=[Ring(value=r["value"], diameter_mm=r["diameter_mm"]) for r in d["rings"]],
            decimal_scoring=d.get("decimal_scoring", False),
            face_width_mm=d.get("face_width_mm"),
            face_height_mm=d.get("face_height_mm"),
            notes=d.get("notes", ""),
        )


# --------------------------------------------------------------------------- #
# Shots
# --------------------------------------------------------------------------- #

@dataclass
class Shot:
    """A single shot, in millimetres relative to the point of aim."""

    x_mm: float
    y_mm: float
    # Populated by the scoring step; kept optional so a Shot can exist before
    # it is scored against a particular target.
    score: Optional[float] = None

    @property
    def radius_mm(self) -> float:
        """Distance from the point of aim (origin)."""
        return math.hypot(self.x_mm, self.y_mm)

    def to_dict(self) -> dict:
        return {"x_mm": self.x_mm, "y_mm": self.y_mm, "score": self.score}

    @classmethod
    def from_dict(cls, d: dict) -> "Shot":
        return cls(x_mm=d["x_mm"], y_mm=d["y_mm"], score=d.get("score"))


# --------------------------------------------------------------------------- #
# Computed grouping statistics
# --------------------------------------------------------------------------- #

@dataclass
class GroupStats:
    """Marksmanship metrics computed from a set of shots.

    All distances are millimetres.  See :func:`marksman.grouping.analyze_group`.
    """

    shot_count: int
    # Precision (how tight, independent of where it's centred):
    extreme_spread_mm: float        # largest centre-to-centre distance ("group size")
    mean_radius_mm: float           # average distance of shots from group centre
    rms_radius_mm: float            # root-mean-square radius about group centre
    cep_mm: float                   # circular error probable (median radius)
    std_x_mm: float
    std_y_mm: float
    bounding_width_mm: float
    bounding_height_mm: float
    # Accuracy (where the group sits relative to the point of aim):
    center_x_mm: float              # group centroid x
    center_y_mm: float              # group centroid y
    poa_offset_mm: float            # distance from POA to group centroid
    poa_offset_angle_deg: float     # bearing of that offset (0 = right, 90 = up)
    # Scoring (None when no target was supplied):
    total_score: Optional[float] = None
    max_possible_score: Optional[float] = None
    average_score: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GroupStats":
        return cls(**d)


# --------------------------------------------------------------------------- #
# Weapons
# --------------------------------------------------------------------------- #

@dataclass
class Weapon:
    """A specific firearm or airgun the shooter owns/uses."""

    id: str
    name: str
    category: str = "Other"
    caliber: str = ""               # free text, e.g. ".22 LR", "4.5mm", "9mm"
    is_airgun: bool = False
    # Projectile diameter in mm; used to give shots their physical size when
    # scoring "edge breaks the line" and when detecting holes in images.
    caliber_mm: Optional[float] = None
    notes: str = ""

    def __post_init__(self) -> None:
        self.category = normalize_category(self.category)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Weapon":
        return cls(
            id=d["id"],
            name=d["name"],
            category=d.get("category", "Other"),
            caliber=d.get("caliber", ""),
            is_airgun=d.get("is_airgun", False),
            caliber_mm=d.get("caliber_mm"),
            notes=d.get("notes", ""),
        )


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #

@dataclass
class Session:
    """One analysed target: shots fired with a weapon on a given day.

    A session is the unit of progress tracking.  It stores the raw shots, the
    computed :class:`GroupStats`, and the context (weapon, date, distance) used
    to aggregate progress later.
    """

    id: str
    weapon_id: str
    date: str                       # ISO date, e.g. "2026-05-29"
    shots: List[Shot] = field(default_factory=list)
    stats: Optional[GroupStats] = None
    distance_m: Optional[float] = None
    target_name: str = ""
    ammo: str = ""
    image_path: str = ""
    notes: str = ""

    @staticmethod
    def today_iso() -> str:
        return date.today().isoformat()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "weapon_id": self.weapon_id,
            "date": self.date,
            "shots": [s.to_dict() for s in self.shots],
            "stats": self.stats.to_dict() if self.stats else None,
            "distance_m": self.distance_m,
            "target_name": self.target_name,
            "ammo": self.ammo,
            "image_path": self.image_path,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        return cls(
            id=d["id"],
            weapon_id=d["weapon_id"],
            date=d["date"],
            shots=[Shot.from_dict(s) for s in d.get("shots", [])],
            stats=GroupStats.from_dict(d["stats"]) if d.get("stats") else None,
            distance_m=d.get("distance_m"),
            target_name=d.get("target_name", ""),
            ammo=d.get("ammo", ""),
            image_path=d.get("image_path", ""),
            notes=d.get("notes", ""),
        )

    @property
    def date_obj(self) -> date:
        return datetime.fromisoformat(self.date).date()
