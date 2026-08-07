"""Human-readable text rendering of analysis and progress results.

Kept separate from the CLI so the same formatting can be reused (tests, a future
GUI, exports).  Pure standard library.

Every formatter accepts an optional :class:`~marksman.theme.Painter` so the
output can wear a *skin* (colours, sparkline ramp).  When no painter is given --
or when the painter is disabled (non-terminal output, ``NO_COLOR``, the plain
``mono`` skin) -- the text is byte-for-byte identical to the uncoloured report.
"""

from __future__ import annotations

from typing import List, Optional

from .models import GroupStats
from .theme import DEFAULT_SPARK, Painter, plain_painter
from . import tracker
from .tracker import MetricTrend, ProgressReport

_SPARK = DEFAULT_SPARK   # ascii-safe ramp, low -> high

_ARROWS = {
    "improving": "improving",
    "declining": "declining",
    "flat": "flat",
    "n/a": "n/a",
}


def sparkline(values: List[Optional[float]], ramp: Optional[str] = None) -> str:
    """Tiny ascii sparkline. ``None`` values render as a gap.

    ``ramp`` selects the glyph set (low -> high); it defaults to the historical
    ascii ramp so callers that don't theme their output are unaffected.
    """
    ramp = ramp or _SPARK
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
            out.append(ramp[len(ramp) // 2])
        else:
            idx = int((v - lo) / span * (len(ramp) - 1) + 0.5)
            out.append(ramp[idx])
    return "".join(out)


def _fmt(value: Optional[float], digits: int = 1) -> str:
    return "-" if value is None else ("%.*f" % (digits, value))


def format_group_stats(stats: GroupStats, target_name: str = "",
                       distance_m: Optional[float] = None,
                       painter: Optional[Painter] = None,
                       click_mrad: Optional[float] = None) -> str:
    """One analysed target, as a readable block.

    ``click_mrad`` is the sight's click value, if the tool declares one; it
    turns the zero error into a number of clicks to dial.
    """
    p = painter or plain_painter()
    lines = []
    lines.append(p.label("Shots analysed : ") + p.value("%d" % stats.shot_count))
    lines.append("")
    lines.append(p.accent("Precision (how tight the group is):"))
    lines.append("  " + p.label("Group size (extreme spread) : ")
                 + p.value(_fmt(stats.extreme_spread_mm)) + " " + p.unit("mm"))
    if distance_m:
        mrad = stats.extreme_spread_mm / distance_m
        lines.append("                              = "
                     + p.value(_fmt(mrad, 2)) + " " + p.unit("mrad") + " / "
                     + p.value(_fmt(mrad * 3.43775, 2)) + " " + p.unit("MOA")
                     + "  @ " + p.value(_fmt(distance_m, 0)) + " " + p.unit("m"))
    lines.append("  " + p.label("Mean radius                 : ")
                 + p.value(_fmt(stats.mean_radius_mm)) + " " + p.unit("mm"))
    lines.append("  " + p.label("RMS radius                  : ")
                 + p.value(_fmt(stats.rms_radius_mm)) + " " + p.unit("mm"))
    lines.append("  " + p.label("CEP (50%)                   : ")
                 + p.value(_fmt(stats.cep_mm)) + " " + p.unit("mm"))
    lines.append("  " + p.label("Spread x / y (sigma)        : ")
                 + p.value(_fmt(stats.std_x_mm)) + " / "
                 + p.value(_fmt(stats.std_y_mm)) + " " + p.unit("mm"))
    lines.append("  " + p.label("Bounding box                : ")
                 + p.value(_fmt(stats.bounding_width_mm)) + " x "
                 + p.value(_fmt(stats.bounding_height_mm)) + " " + p.unit("mm"))
    lines.append("")
    lines.append(p.accent("Accuracy (where the group sits vs point of aim):"))
    lines.append("  " + p.label("Group centre                : ")
                 + p.muted("(") + p.value(_fmt(stats.center_x_mm)) + p.muted(", ")
                 + p.value(_fmt(stats.center_y_mm)) + p.muted(") ") + p.unit("mm"))
    lines.append("  " + p.label("Zero error (POA-POI)        : ")
                 + p.value(_fmt(stats.poa_offset_mm)) + " " + p.unit("mm")
                 + " at " + p.value(_fmt(stats.poa_offset_angle_deg, 0))
                 + " " + p.unit("deg"))
    advice = tracker.format_correction(
        tracker.sight_correction(stats, distance_m, click_mrad), distance_m)
    if advice:
        lines.append("  " + p.label("Sight correction            : ")
                     + p.accent(advice))
    if stats.total_score is not None:
        pct = (stats.total_score / stats.max_possible_score * 100.0
               if stats.max_possible_score else 0.0)
        lines.append("")
        lines.append(p.accent("Score:"))
        tname = (" (%s)" % target_name) if target_name else ""
        lines.append("  " + p.label("Total%-27s: " % tname)
                     + p.value(_fmt(stats.total_score)) + p.muted(" / ")
                     + p.value(_fmt(stats.max_possible_score))
                     + p.muted(" (") + p.value(_fmt(pct)) + p.muted("%)"))
        lines.append("  " + p.label("Average per shot            : ")
                     + p.value(_fmt(stats.average_score, 2)))
    return "\n".join(lines)


def _metric_row(m: MetricTrend, p: Painter) -> str:
    name = "%s (%s)" % (m.name, m.unit)
    best = _fmt(m.best, 2)
    avg = _fmt(m.average, 2)
    latest = _fmt(m.latest, 2)
    direction = _ARROWS.get(m.direction, m.direction)
    return ("  " + p.label("%-34s" % name)
            + p.muted(" best ") + p.value("%8s" % best)
            + p.muted("  avg ") + p.value("%8s" % avg)
            + p.muted("  latest ") + p.value("%8s" % latest)
            + "   " + p.trend(m.direction, direction))


def format_progress(report: ProgressReport, show_sessions: bool = False,
                    painter: Optional[Painter] = None) -> str:
    """A progress report for one scope."""
    p = painter or plain_painter()
    lines = []
    header = "%s: %s" % (report.scope_type.upper(), report.scope_name)
    lines.append(p.title(header))
    lines.append(p.rule(p.rule_char * len(header)))
    if report.session_count == 0:
        lines.append(p.muted("  (no analysed sessions yet)"))
        return "\n".join(lines)

    lines.append("  " + p.label("Sessions: ") + p.value("%d" % report.session_count)
                 + p.label("   Shots: ") + p.value("%d" % report.shot_count)
                 + "   " + p.muted("%s -> %s" % (report.first_date, report.last_date)))
    lines.append("")
    # Show the most useful metrics in a stable order.
    order = ["extreme_spread_mm", "group_size_moa", "mean_radius_mm",
             "poa_offset_mm", "score_pct"]
    for key in order:
        m = report.metrics.get(key)
        if m and m.count > 0:
            lines.append(_metric_row(m, p))

    # Sparklines for the headline metrics over time.
    if report.session_count >= 2:
        lines.append("")
        es = [row["extreme_spread_mm"] for row in report.series]
        sc = [row["score_pct"] for row in report.series]
        if any(v is not None for v in es):
            lines.append("  " + p.label("Group size over time : ")
                         + p.accent(sparkline(es, p.spark_ramp))
                         + p.muted("  (lower is better)"))
        if any(v is not None for v in sc):
            lines.append("  " + p.label("Score % over time    : ")
                         + p.accent(sparkline(sc, p.spark_ramp))
                         + p.muted("  (higher is better)"))

    if show_sessions:
        lines.append("")
        lines.append(p.accent("  Sessions:"))
        for row in report.series:
            lines.append(
                "    " + p.value("%s" % row["date"])
                + p.muted("  ES ") + p.value("%7s" % _fmt(row["extreme_spread_mm"]))
                + p.muted(" mm  MR ") + p.value("%6s" % _fmt(row["mean_radius_mm"]))
                + p.muted(" mm  score ") + p.value("%5s" % _fmt(row["score_pct"]))
                + p.muted("%  (") + p.value("%d" % row["shots"]) + p.muted(" shots)")
            )
    return "\n".join(lines)
