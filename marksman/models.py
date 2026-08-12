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
# Tool categories
# --------------------------------------------------------------------------- #

# Neutral fallback categories. Real ones come from whatever equipment pack is
# installed (see :mod:`marksman.packs`); these only exist so the app can group
# tools sensibly with no pack at all. Categories are plain strings throughout,
# so a user or a pack can add any of their own.
STANDARD_CATEGORIES = (
    "Manual",
    "Powered",
    "Other",
)


# Categories the installed packs offer, filled in by :func:`packs.load`. They
# join STANDARD_CATEGORIES as the names that get canonical capitalisation.
_KNOWN_CATEGORIES = []  # type: List[str]


def register_categories(names) -> None:
    """Tell the normaliser about a pack's categories (called when packs load)."""
    for n in names:
        if n and n not in _KNOWN_CATEGORIES:
            _KNOWN_CATEGORIES.append(n)


def normalize_category(name: str) -> str:
    """Return a canonical category name (case/space tolerant).

    A name a pack knows about gets that pack's capitalisation, so "aeg" and
    "AEG" aggregate together.  Anything else is kept exactly as typed -- the
    app never rejects a user's discipline, and never mangles an acronym by
    title-casing it either.
    """
    cleaned = " ".join(name.strip().split())
    if not cleaned:
        return "Other"
    for known in list(STANDARD_CATEGORIES) + _KNOWN_CATEGORIES:
        if cleaned.lower() == known.lower():
            return known
    return cleaned


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

    ``decimal_scoring`` enables tenth-of-a-point scoring (precision style) for
    fine practice faces.
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
# Tools
# --------------------------------------------------------------------------- #

@dataclass
class Tool:
    """Something that launches a projectile at a target.

    Deliberately generic: a foam dart blaster, an airsoft replica, a bow --
    the app only needs a name, a grouping category, and the projectile's size.
    What any of it is *called* comes from the installed pack's terminology.
    """

    id: str
    name: str
    category: str = "Other"
    projectile: str = ""       # free text, e.g. "13mm foam dart", "6mm 0.25g"
    is_powered: bool = False   # gas / battery / air driven rather than manual
    # Projectile diameter in mm; used to give shots their physical size when
    # scoring "edge breaks the line" and when detecting impacts.
    projectile_mm: Optional[float] = None
    # One click of this tool's sight, in milliradians -- lets the app turn a
    # zero error into "4 clicks left". None for anything without an adjustable
    # sight, which is most things.
    sight_click_mrad: Optional[float] = None
    notes: str = ""

    def __post_init__(self) -> None:
        self.category = normalize_category(self.category)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Tool":
        # The bb/bb_mm/is_gas names date from when this app was airsoft-only;
        # databases written then still load.
        return cls(
            id=d["id"],
            name=d["name"],
            category=d.get("category", "Other"),
            projectile=d.get("projectile", d.get("bb", "")),
            is_powered=d.get("is_powered", d.get("is_gas", False)),
            projectile_mm=d.get("projectile_mm", d.get("bb_mm")),
            sight_click_mrad=d.get("sight_click_mrad"),
            notes=d.get("notes", ""),
        )


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #

@dataclass
class Session:
    """One analysed target: shots fired with a tool on a given day.

    A session is the unit of progress tracking.  It stores the raw shots, the
    computed :class:`GroupStats`, and the context (tool, date, distance) used
    to aggregate progress later.
    """

    id: str
    tool_id: str
    date: str                       # ISO date, e.g. "2026-05-29"
    shots: List[Shot] = field(default_factory=list)
    stats: Optional[GroupStats] = None
    distance_m: Optional[float] = None
    target_name: str = ""
    projectiles: str = ""           # free text: what was loaded that day
    drill_id: str = ""              # the named drill this session was, if any
    # Where this came from, when it wasn't typed in by hand -- currently
    # "drive:<fileId>" for a photo pulled from Google Drive. Kept on the
    # session (rather than in a side list) so it survives sync and export,
    # and so "have I already logged this photo?" is answered by the data.
    source_ref: str = ""
    image_path: str = ""            # source still image (may be bulky)
    video_path: str = ""            # source video (may be bulky)
    notes: str = ""
    # A lightweight diagram redrawn from the shot coordinates.  Set once the
    # source media has been cleaned up so the visual result is preserved.
    recreation_path: str = ""
    # True when the bulky source media has been deleted to reclaim space.  The
    # shots (and therefore a recreation) are always retained.
    media_cleaned: bool = False

    @staticmethod
    def today_iso() -> str:
        return date.today().isoformat()

    @property
    def source_media_paths(self) -> List[str]:
        """The bulky *source* media this session references (image, video)."""
        return [p for p in (self.image_path, self.video_path) if p]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tool_id": self.tool_id,
            "date": self.date,
            "shots": [s.to_dict() for s in self.shots],
            "stats": self.stats.to_dict() if self.stats else None,
            "distance_m": self.distance_m,
            "target_name": self.target_name,
            "projectiles": self.projectiles,
            "drill_id": self.drill_id,
            "source_ref": self.source_ref,
            "image_path": self.image_path,
            "video_path": self.video_path,
            "notes": self.notes,
            "recreation_path": self.recreation_path,
            "media_cleaned": self.media_cleaned,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        return cls(
            id=d["id"],
            tool_id=d["tool_id"],
            date=d["date"],
            shots=[Shot.from_dict(s) for s in d.get("shots", [])],
            stats=GroupStats.from_dict(d["stats"]) if d.get("stats") else None,
            distance_m=d.get("distance_m"),
            target_name=d.get("target_name", ""),
            projectiles=d.get("projectiles", d.get("bbs", "")),
            drill_id=d.get("drill_id", ""),
            source_ref=d.get("source_ref", ""),
            image_path=d.get("image_path", ""),
            video_path=d.get("video_path", ""),
            notes=d.get("notes", ""),
            recreation_path=d.get("recreation_path", ""),
            media_cleaned=d.get("media_cleaned", False),
        )

    @property
    def date_obj(self) -> date:
        return datetime.fromisoformat(self.date).date()
