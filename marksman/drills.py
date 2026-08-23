"""Tiered standards, PRs and an adaptive plan over whatever drills you have.

A *drill* is a repeatable practice unit: a distance, a shot count, a target
face, and one metric it is judged on.  Each drill carries four ascending
**tiers** (Rookie -> Steady -> Sharp -> Marksman), so a session isn't just a
number -- it either holds your standard or it doesn't.

This module is the **engine**, not the content: the drills themselves come
from installed equipment packs (:mod:`marksman.packs`), so the same tiering,
PR tracking and planning work for airsoft, foam darts, or anything else
somebody writes a pack for.  With no packs installed there are simply no
drills, and the rest of the app carries on.

A session records which drill it was via ``Session.drill_id``.  Everything
here is a pure read over stored sessions; nothing is cached, so a drill's
standing is always current.

Tier thresholds are *practice standards set by the pack*, not an official
classification of any kind.  They are meant to be reachable: the point is a
visible next rung, not a ranking.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from . import packs, tracker
from .goals import METRICS
from .storage import Database

#: Tier names, easiest first.  Every drill defines exactly this many cutoffs.
TIERS = ("Rookie", "Steady", "Sharp", "Marksman")


def all_drills(db=None) -> List[Dict[str, Any]]:
    """Every drill from the active equipment packs."""
    return packs.drills(db)


def get_drill(drill_id: str, db=None) -> Dict[str, Any]:
    """Look up a drill by id (case-insensitive). Raises KeyError if absent."""
    key = (drill_id or "").strip().lower()
    for d in all_drills(db):
        if d["id"] == key:
            return d
    known = ", ".join(sorted(d["id"] for d in all_drills(db))) or "(no packs installed)"
    raise KeyError("Unknown drill %r. Known: %s" % (drill_id, known))


def families(db=None) -> List[str]:
    out = []  # type: List[str]
    for d in all_drills(db):
        if d["family"] not in out:
            out.append(d["family"])
    return out


# --------------------------------------------------------------------------- #
# Standing: where you are on a drill
# --------------------------------------------------------------------------- #

def tier_for(drill: Dict[str, Any], value: Optional[float]) -> Optional[str]:
    """Highest tier whose standard ``value`` meets, or None if below them all."""
    if value is None:
        return None
    lower = METRICS[drill["metric"]][2]
    earned = None
    for name, cutoff in zip(TIERS, drill["cutoffs"]):
        if (value <= cutoff) if lower else (value >= cutoff):
            earned = name
    return earned


def next_cutoff(drill: Dict[str, Any], tier: Optional[str]) -> Optional[float]:
    """The standard to beat for the next tier up, or None at the top."""
    idx = 0 if tier is None else TIERS.index(tier) + 1
    if idx >= len(TIERS):
        return None
    return drill["cutoffs"][idx]


def attempts(db: Database, drill_id: str) -> List:
    """Sessions logged against this drill, oldest first."""
    got = [s for s in db.all_sessions()
           if getattr(s, "drill_id", "") == drill_id and s.stats is not None]
    got.sort(key=lambda s: (s.date, s.id))
    return got


def standing(db: Database, drill: Dict[str, Any],
             tool_id: Optional[str] = None) -> Dict[str, Any]:
    """Current standing on one drill: PR, latest, tier and the next rung."""
    key, unit, lower = METRICS[drill["metric"]]
    got = attempts(db, drill["id"])
    if tool_id:
        got = [s for s in got if s.tool_id == tool_id]
    rep = tracker.build_report(got, "drill", drill["name"])
    m = rep.metrics.get(key)
    best = m.best if (m and m.count) else None
    latest = m.latest if (m and m.count) else None
    tier = tier_for(drill, best)
    return {
        "id": drill["id"], "name": drill["name"], "family": drill["family"],
        "metric": drill["metric"], "unit": unit, "lowerIsBetter": lower,
        "attempts": len(got), "best": best, "latest": latest,
        "tier": tier, "nextTier": (None if tier == TIERS[-1]
                                   else TIERS[0 if tier is None
                                              else TIERS.index(tier) + 1]),
        "nextCutoff": next_cutoff(drill, tier),
        "lastDate": got[-1].date if got else None,
        "direction": m.direction if (m and m.count) else "n/a",
    }


def standings(db: Database) -> List[Dict[str, Any]]:
    return [standing(db, d) for d in all_drills(db)]


def tier_points(db: Database) -> Dict[str, int]:
    """Overall progress: tiers earned out of the maximum available."""
    rows = standings(db)
    earned = sum(TIERS.index(r["tier"]) + 1 for r in rows if r["tier"])
    return {
        "earned": earned,
        "possible": len(rows) * len(TIERS),
        "drillsAttempted": sum(1 for r in rows if r["attempts"]),
        "drillsTotal": len(rows),
    }


# --------------------------------------------------------------------------- #
# Adaptive plan
# --------------------------------------------------------------------------- #

def _days_since(iso: Optional[str], today: date) -> Optional[int]:
    if not iso:
        return None
    try:
        return (today - datetime.fromisoformat(iso).date()).days
    except ValueError:
        return None


def _priority(row: Dict[str, Any], today: date) -> float:
    """How badly this drill wants doing next. Higher = sooner.

    ponytail: a hand-tuned linear score, not a model. It only has to order a
    catalogue of a few dozen sensibly; swap in something learned if the
    ordering ever feels wrong in practice.
    """
    score = 0.0
    if not row["attempts"]:
        return 100.0                       # never tried -- always surfaces first
    if row["tier"] == TIERS[-1]:
        score -= 40.0                      # topped out; keep it, deprioritise it
    stale = _days_since(row["lastDate"], today)
    if stale is not None:
        score += min(stale, 60) * 1.5      # neglected drills drift back up
    cutoff, best = row["nextCutoff"], row["best"]
    if cutoff and best:
        # Within reach of the next rung? Strike while it's close.
        gap = abs(best - cutoff) / max(abs(cutoff), 1e-9)
        if gap <= 0.25:
            score += 35.0 * (1.0 - gap / 0.25)
    if row["direction"] == "declining":
        score += 15.0                      # slipping -- worth revisiting
    return score


def plan(db: Database, count: int = 3, today: Optional[date] = None) -> List[Dict[str, Any]]:
    """Today's recommended drills, most useful first, each with a reason."""
    today = today or date.today()
    rows = standings(db)
    scored = sorted(rows, key=lambda r: (-_priority(r, today), r["id"]))
    out = []
    for row in scored[:max(0, count)]:
        out.append(dict(row, reason=_reason(row, today)))
    return out


def _reason(row: Dict[str, Any], today: date) -> str:
    if not row["attempts"]:
        return "never attempted"
    if row["tier"] == TIERS[-1]:
        return "at %s -- maintenance" % TIERS[-1]
    cutoff, best = row["nextCutoff"], row["best"]
    if cutoff and best:
        gap = abs(best - cutoff) / max(abs(cutoff), 1e-9)
        if gap <= 0.25:
            return "%.1f from %s (%.1f)" % (abs(best - cutoff), row["nextTier"], cutoff)
    stale = _days_since(row["lastDate"], today)
    if stale is not None and stale >= 14:
        return "not practised in %d days" % stale
    if row["direction"] == "declining":
        return "trending the wrong way"
    return "keeps the rotation honest"


if __name__ == "__main__":  # pragma: no cover - self-check
    from .models import Session, Shot, Tool
    from .grouping import analyze_group

    # Derived from the drill's own cutoffs, so installing a different pack
    # can't break the check that the tier maths works.
    d = all_drills()[0]
    easiest, hardest = d["cutoffs"][0], d["cutoffs"][-1]
    assert tier_for(d, easiest * 2) is None                 # short of the ladder
    assert tier_for(d, easiest) == TIERS[0]
    assert tier_for(d, hardest / 2.0) == TIERS[-1]
    assert next_cutoff(d, TIERS[0]) == d["cutoffs"][1]
    assert next_cutoff(d, TIERS[-1]) is None

    db = Database()
    db.add_tool(Tool("t", "Test"))
    shots = [Shot(-20.0, 0.0), Shot(20.0, 0.0)]        # 40 mm spread -> Sharp
    db.add_session(Session("s1", "t", "2026-01-01", shots=shots,
                           stats=analyze_group(shots), distance_m=10.0,
                           drill_id=d["id"]))
    row = standing(db, d)
    assert row["attempts"] == 1 and row["tier"] == TIERS[-1], row
    assert plan(db, 3)[0]["attempts"] == 0                # untried drills first

    # The engine holds no content: every drill came from a pack.
    assert all(x["pack"] for x in all_drills())
    print("drills self-check OK: %d drills from %d packs, %s"
          % (len(all_drills()), len(packs.active()), tier_points(db)))
