"""Human-readable text rendering of analysis and progress results.

Kept separate from the CLI so the same formatting can be reused (tests, a future
GUI, exports).  Pure standard library.
"""

from __future__ import annotations

from typing import List, Optional

from .models import GroupStats
from .tracker import MetricTrend, ProgressReport

_SPARK = "_.-~=*#@"   # ascii-safe ramp, low -> high

_ARROWS = {
    "improving": "improving",
    "declining": "declining",
    "flat": "flat",
    "n/a": "n/a",
}


def sparkline(values: List[Optional[float]]) -> str:
    """Tiny ascii sparkline. ``None`` values render as a gap."""
    nums = [v for v in values if v is not None]
    if not nums:
        return ""
    lo, hi = min(nums), max(nums)
    span = hi - lo
    out = []
    for v in values:
        if v is None:
            out.append(" ")
        elif span <= 1e-12:
            out.append(_SPARK[len(_SPARK) // 2])
        else:
            idx = int((v - lo) / span * (len(_SPARK) - 1) + 0.5)
            out.append(_SPARK[idx])
    return "".join(out)


def _fmt(value: Optional[float], digits: int = 1) -> str:
    return "-" if value is None else ("%.*f" % (digits, value))


def format_group_stats(stats: GroupStats, target_name: str = "",
                       distance_m: Optional[float] = None) -> str:
    """One analysed target, as a readable block."""
    lines = []
    lines.append("Shots analysed : %d" % stats.shot_count)
    lines.append("")
    lines.append("Precision (how tight the group is):")
    lines.append("  Group size (extreme spread) : %s mm" % _fmt(stats.extreme_spread_mm))
    if distance_m:
        mrad = stats.extreme_spread_mm / distance_m
        lines.append("                              = %s mrad / %s MOA  @ %s m"
                     % (_fmt(mrad, 2), _fmt(mrad * 3.43775, 2), _fmt(distance_m, 0)))
    lines.append("  Mean radius                 : %s mm" % _fmt(stats.mean_radius_mm))
    lines.append("  RMS radius                  : %s mm" % _fmt(stats.rms_radius_mm))
    lines.append("  CEP (50%%)                   : %s mm" % _fmt(stats.cep_mm))
    lines.append("  Spread x / y (sigma)        : %s / %s mm"
                 % (_fmt(stats.std_x_mm), _fmt(stats.std_y_mm)))
    lines.append("  Bounding box                : %s x %s mm"
                 % (_fmt(stats.bounding_width_mm), _fmt(stats.bounding_height_mm)))
    lines.append("")
    lines.append("Accuracy (where the group sits vs point of aim):")
    lines.append("  Group centre                : (%s, %s) mm"
                 % (_fmt(stats.center_x_mm), _fmt(stats.center_y_mm)))
    lines.append("  Zero error (POA-POI)        : %s mm at %s deg"
                 % (_fmt(stats.poa_offset_mm), _fmt(stats.poa_offset_angle_deg, 0)))
    if stats.total_score is not None:
        pct = (stats.total_score / stats.max_possible_score * 100.0
               if stats.max_possible_score else 0.0)
        lines.append("")
        lines.append("Score:")
        tname = (" (%s)" % target_name) if target_name else ""
        lines.append("  Total%-27s: %s / %s (%s%%)"
                     % (tname, _fmt(stats.total_score),
                        _fmt(stats.max_possible_score), _fmt(pct)))
        lines.append("  Average per shot            : %s" % _fmt(stats.average_score, 2))
    return "\n".join(lines)


def _metric_row(m: MetricTrend) -> str:
    name = "%s (%s)" % (m.name, m.unit)
    best = _fmt(m.best, 2)
    avg = _fmt(m.average, 2)
    latest = _fmt(m.latest, 2)
    direction = _ARROWS.get(m.direction, m.direction)
    return ("  %-34s best %8s  avg %8s  latest %8s   %s"
            % (name, best, avg, latest, direction))


def format_progress(report: ProgressReport, show_sessions: bool = False) -> str:
    """A progress report for one scope."""
    lines = []
    header = "%s: %s" % (report.scope_type.upper(), report.scope_name)
    lines.append(header)
    lines.append("-" * len(header))
    if report.session_count == 0:
        lines.append("  (no analysed sessions yet)")
        return "\n".join(lines)

    lines.append("  Sessions: %d   Shots: %d   %s -> %s"
                 % (report.session_count, report.shot_count,
                    report.first_date, report.last_date))
    lines.append("")
    # Show the most useful metrics in a stable order.
    order = ["extreme_spread_mm", "group_size_moa", "mean_radius_mm",
             "poa_offset_mm", "score_pct"]
    for key in order:
        m = report.metrics.get(key)
        if m and m.count > 0:
            lines.append(_metric_row(m))

    # Sparklines for the headline metrics over time.
    if report.session_count >= 2:
        lines.append("")
        es = [row["extreme_spread_mm"] for row in report.series]
        sc = [row["score_pct"] for row in report.series]
        if any(v is not None for v in es):
            lines.append("  Group size over time : %s  (lower is better)" % sparkline(es))
        if any(v is not None for v in sc):
            lines.append("  Score %% over time    : %s  (higher is better)" % sparkline(sc))

    if show_sessions:
        lines.append("")
        lines.append("  Sessions:")
        for row in report.series:
            lines.append(
                "    %s  ES %7s mm  MR %6s mm  score %5s%%  (%d shots)"
                % (row["date"], _fmt(row["extreme_spread_mm"]),
                   _fmt(row["mean_radius_mm"]), _fmt(row["score_pct"]),
                   row["shots"])
            )
    return "\n".join(lines)
