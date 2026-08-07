"""Turn a pile of sessions into progress reports.

Progress is reported at three scopes, as the goal requires:

* **overall** -- every session,
* **by category** -- every session with tools of one category,
* **by tool** -- a single specific replica.

For each scope we summarise the key metrics with their best value, average,
latest value and a *trend* (is the shooter improving?).

Comparability
-------------
Two normalisations make sessions shot under different conditions comparable:

* Group size and mean radius are also expressed as an **angle** (milliradians
  and MOA) using the session distance, so a tight group at 10 m and a tight
  group at 50 m can be compared on equal terms.
* Score is expressed as a **percentage of the maximum** for that target, so
  results on different target faces sit on one 0-100 scale.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, asdict, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import Session, Tool

# 1 milliradian subtends 1 mm at 1 m. 1 mrad = 3.43775 MOA.
_MOA_PER_MRAD = 3.43774677


def mm_to_mrad(size_mm: float, distance_m: Optional[float]) -> Optional[float]:
    """Angular size in milliradians, or None when distance is unknown/zero."""
    if not distance_m or distance_m <= 0:
        return None
    return size_mm / distance_m


def mrad_to_moa(mrad: Optional[float]) -> Optional[float]:
    return None if mrad is None else mrad * _MOA_PER_MRAD


def parse_click(text: str) -> float:
    """Parse a sight's click value into milliradians.

    Accepts what is written on the turret: ``0.1mrad``, ``1/4 MOA``,
    ``0.25moa``, ``2cm@100m``.  Returns mrad.
    """
    raw = str(text or "").strip().lower().replace(" ", "")
    if not raw:
        raise ValueError("empty click value")
    match = re.match(r"^(\d+(?:\.\d+)?)(?:/(\d+(?:\.\d+)?))?"
                     r"(mrad|mil|moa|cm@100m|mm@100m)$", raw)
    if not match:
        raise ValueError(
            "Bad click value %r. Write it as it appears on the turret, e.g. "
            "'0.1mrad', '1/4moa' or '0.25moa'." % text)
    value = float(match.group(1))
    if match.group(2):
        divisor = float(match.group(2))
        if divisor <= 0:
            raise ValueError("Bad click value %r: divide by zero." % text)
        value /= divisor
    unit = match.group(3)
    if value <= 0:
        raise ValueError("A click must be greater than zero.")
    if unit in ("mrad", "mil"):
        mrad = value
    elif unit == "moa":
        mrad = value / _MOA_PER_MRAD
    elif unit == "cm@100m":
        mrad = value * 10.0 / 100.0          # 10 mm at 100 m = 0.1 mrad
    else:                                    # mm@100m
        mrad = value / 100.0
    if not (0.001 <= mrad <= 100.0):
        raise ValueError("A click of %g mrad is out of range." % mrad)
    return mrad


def sight_correction(stats: Any, distance_m: Optional[float] = None,
                     click_mrad: Optional[float] = None) -> Dict[str, Any]:
    """How to move the group onto the point of aim.

    The group sits at ``(center_x_mm, center_y_mm)`` relative to the point of
    aim, so the sight has to move it by the negative of that.  Angular values
    need the distance; clicks additionally need the sight's click value.

    Returns ``{}`` when the group is already centred closely enough to be noise.
    """
    dx_mm = -float(getattr(stats, "center_x_mm", 0.0) or 0.0)
    dy_mm = -float(getattr(stats, "center_y_mm", 0.0) or 0.0)
    out = {
        "dx_mm": dx_mm, "dy_mm": dy_mm,
        "horizontal": "left" if dx_mm < 0 else "right",
        "vertical": "down" if dy_mm < 0 else "up",
        "offset_mm": math.hypot(dx_mm, dy_mm),
    }
    for axis, mm in (("x", dx_mm), ("y", dy_mm)):
        mrad = mm_to_mrad(abs(mm), distance_m)
        out["mrad_" + axis] = mrad
        out["moa_" + axis] = mrad_to_moa(mrad)
        out["clicks_" + axis] = (
            int(round(mrad / click_mrad)) if mrad and click_mrad else None)
    return out


def format_correction(corr: Dict[str, Any], distance_m: Optional[float] = None,
                      dead_zone_mm: float = 1.0) -> str:
    """One human sentence for :func:`sight_correction` (empty if centred)."""
    if not corr or corr.get("offset_mm", 0.0) < dead_zone_mm:
        return ""
    parts = []
    for axis in ("x", "y"):
        mm = corr["d%s_mm" % axis]
        if abs(mm) < dead_zone_mm / 2.0:
            continue
        way = corr["horizontal" if axis == "x" else "vertical"]
        clicks = corr.get("clicks_" + axis)
        if clicks:
            parts.append("%d click%s %s" % (clicks, "" if clicks == 1 else "s", way))
        elif corr.get("mrad_" + axis):
            parts.append("%.2f mrad (%.1f MOA) %s"
                         % (corr["mrad_" + axis], corr["moa_" + axis], way))
        else:
            parts.append("%.1f mm %s" % (abs(mm), way))
    if not parts:
        return ""
    at = " at %g m" % distance_m if distance_m else ""
    return "Move the group " + " and ".join(parts) + at


# --------------------------------------------------------------------------- #
# Trend of a single metric over time
# --------------------------------------------------------------------------- #

@dataclass
class MetricTrend:
    """Summary of one metric across a set of sessions, ordered by date."""

    name: str
    unit: str
    lower_is_better: bool
    count: int
    best: Optional[float] = None
    worst: Optional[float] = None
    average: Optional[float] = None
    first: Optional[float] = None
    latest: Optional[float] = None
    # Least-squares slope of value vs. time, per day. Sign interpreted via
    # lower_is_better to yield ``direction``.
    slope_per_day: Optional[float] = None
    direction: str = "n/a"          # improving | declining | flat | n/a
    change_pct: Optional[float] = None  # latest vs first, % (signed, raw)

    def to_dict(self) -> dict:
        return asdict(self)


def _linreg_slope(points: Sequence[Tuple[float, float]]) -> Optional[float]:
    """Least-squares slope dy/dx, or None if undefined."""
    n = len(points)
    if n < 2:
        return None
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    mx = sx / n
    my = sy / n
    num = sum((p[0] - mx) * (p[1] - my) for p in points)
    den = sum((p[0] - mx) ** 2 for p in points)
    if den <= 1e-12:
        return None
    return num / den


def summarize_metric(
    name: str,
    unit: str,
    series: Sequence[Tuple[float, float]],  # (day_offset, value)
    lower_is_better: bool,
    flat_eps: float = 1e-9,
) -> MetricTrend:
    """Build a :class:`MetricTrend` from a time series of (day, value)."""
    values = [v for _, v in series]
    n = len(values)
    if n == 0:
        return MetricTrend(name=name, unit=unit, lower_is_better=lower_is_better, count=0)

    best = min(values) if lower_is_better else max(values)
    worst = max(values) if lower_is_better else min(values)
    avg = sum(values) / n
    first = series[0][1]
    latest = series[-1][1]

    slope = _linreg_slope(series)
    direction = "n/a"
    if slope is not None:
        if abs(slope) <= flat_eps:
            direction = "flat"
        elif (slope < 0) == lower_is_better:
            direction = "improving"
        else:
            direction = "declining"

    change_pct = None
    if first not in (None, 0):
        change_pct = (latest - first) / abs(first) * 100.0

    return MetricTrend(
        name=name,
        unit=unit,
        lower_is_better=lower_is_better,
        count=n,
        best=best,
        worst=worst,
        average=avg,
        first=first,
        latest=latest,
        slope_per_day=slope,
        direction=direction,
        change_pct=change_pct,
    )


# --------------------------------------------------------------------------- #
# Progress report for a scope
# --------------------------------------------------------------------------- #

@dataclass
class ProgressReport:
    scope_type: str                 # overall | category | tool
    scope_name: str
    session_count: int
    shot_count: int
    first_date: Optional[str]
    last_date: Optional[str]
    metrics: Dict[str, MetricTrend] = field(default_factory=dict)
    # Per-session rows, oldest first, for plotting / tables.
    series: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "scope_type": self.scope_type,
            "scope_name": self.scope_name,
            "session_count": self.session_count,
            "shot_count": self.shot_count,
            "first_date": self.first_date,
            "last_date": self.last_date,
            "metrics": {k: v.to_dict() for k, v in self.metrics.items()},
            "series": self.series,
        }


def build_report(
    sessions: Iterable[Session],
    scope_type: str,
    scope_name: str,
) -> ProgressReport:
    """Aggregate the given (already-filtered) sessions into a report."""
    # Only sessions that have been analysed contribute metrics.
    analysed = [s for s in sessions if s.stats is not None]
    analysed.sort(key=lambda s: (s.date, s.id))

    if not analysed:
        return ProgressReport(
            scope_type=scope_type,
            scope_name=scope_name,
            session_count=0,
            shot_count=0,
            first_date=None,
            last_date=None,
        )

    first_day = analysed[0].date_obj
    day_offsets = [(s.date_obj - first_day).days for s in analysed]

    def collect(extract: Callable[[Session], Optional[float]]) -> List[Tuple[float, float]]:
        out = []
        for off, s in zip(day_offsets, analysed):
            v = extract(s)
            if v is not None:
                out.append((float(off), float(v)))
        return out

    metrics = {}  # type: Dict[str, MetricTrend]

    metrics["extreme_spread_mm"] = summarize_metric(
        "Group size (extreme spread)", "mm",
        collect(lambda s: s.stats.extreme_spread_mm),
        lower_is_better=True,
    )
    metrics["mean_radius_mm"] = summarize_metric(
        "Mean radius", "mm",
        collect(lambda s: s.stats.mean_radius_mm),
        lower_is_better=True,
    )
    metrics["group_size_mrad"] = summarize_metric(
        "Group size (angular)", "mrad",
        collect(lambda s: mm_to_mrad(s.stats.extreme_spread_mm, s.distance_m)),
        lower_is_better=True,
    )
    metrics["group_size_moa"] = summarize_metric(
        "Group size (angular)", "MOA",
        collect(lambda s: mrad_to_moa(mm_to_mrad(s.stats.extreme_spread_mm, s.distance_m))),
        lower_is_better=True,
    )
    metrics["poa_offset_mm"] = summarize_metric(
        "Zero error (POA-POI)", "mm",
        collect(lambda s: s.stats.poa_offset_mm),
        lower_is_better=True,
    )
    metrics["score_pct"] = summarize_metric(
        "Score", "% of max",
        collect(_score_pct),
        lower_is_better=False,
    )

    series = []
    for off, s in zip(day_offsets, analysed):
        series.append({
            "session_id": s.id,
            "date": s.date,
            "day_offset": off,
            "tool_id": s.tool_id,
            "distance_m": s.distance_m,
            "shots": s.stats.shot_count,
            "extreme_spread_mm": s.stats.extreme_spread_mm,
            "mean_radius_mm": s.stats.mean_radius_mm,
            "group_size_mrad": mm_to_mrad(s.stats.extreme_spread_mm, s.distance_m),
            "poa_offset_mm": s.stats.poa_offset_mm,
            "score_pct": _score_pct(s),
            "total_score": s.stats.total_score,
        })

    return ProgressReport(
        scope_type=scope_type,
        scope_name=scope_name,
        session_count=len(analysed),
        shot_count=sum(s.stats.shot_count for s in analysed),
        first_date=analysed[0].date,
        last_date=analysed[-1].date,
        metrics=metrics,
        series=series,
    )


def _score_pct(s: Session) -> Optional[float]:
    st = s.stats
    if st is None or st.total_score is None or not st.max_possible_score:
        return None
    return st.total_score / st.max_possible_score * 100.0


# --------------------------------------------------------------------------- #
# Convenience scope builders
# --------------------------------------------------------------------------- #

def progress_overall(sessions: Sequence[Session]) -> ProgressReport:
    return build_report(sessions, "overall", "All tools")


def progress_by_category(
    sessions: Sequence[Session],
    tools: Dict[str, Tool],
) -> Dict[str, ProgressReport]:
    """One report per tool category present in the sessions."""
    buckets = {}  # type: Dict[str, List[Session]]
    for s in sessions:
        w = tools.get(s.tool_id)
        category = w.category if w else "Other"
        buckets.setdefault(category, []).append(s)
    return {
        cat: build_report(ss, "category", cat)
        for cat, ss in sorted(buckets.items())
    }


def progress_by_tool(
    sessions: Sequence[Session],
    tools: Dict[str, Tool],
) -> Dict[str, ProgressReport]:
    """One report per tool that has sessions."""
    buckets = {}  # type: Dict[str, List[Session]]
    for s in sessions:
        buckets.setdefault(s.tool_id, []).append(s)
    reports = {}
    for wid, ss in buckets.items():
        w = tools.get(wid)
        name = w.name if w else wid
        reports[wid] = build_report(ss, "tool", name)
    return reports
