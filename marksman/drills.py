"""Named practice drills with tiered standards, PRs and an adaptive plan.

A *drill* is a repeatable practice unit: a distance, a shot count, a target
face, and one metric it is judged on.  Each drill carries four ascending
**tiers** (Rookie -> Steady -> Sharp -> Marksman), so a session isn't just a
number -- it either holds your standard or it doesn't.

A session records which drill it was via ``Session.drill_id``.  Everything
here is a pure read over stored sessions; nothing is cached, so a drill's
standing is always current.

Tier thresholds are *practice standards for this app*, not an official
classification of any kind.  They are deliberately reachable: the point is a
visible next rung, not a ranking.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from . import tracker
from .goals import METRICS
from .storage import Database

#: Tier names, easiest first.  Every drill defines exactly this many cutoffs.
TIERS = ("Rookie", "Steady", "Sharp", "Marksman")


def _drill(id, name, family, distance_m, shots, target, metric, cutoffs,
           why, how, cues):
    assert metric in METRICS, metric
    assert len(cutoffs) == len(TIERS), (id, cutoffs)
    return {
        "id": id, "name": name, "family": family, "distance_m": distance_m,
        "shots": shots, "target": target, "metric": metric,
        "cutoffs": list(cutoffs), "why": why, "how": list(how),
        "cues": list(cues),
    }


# --------------------------------------------------------------------------- #
# The catalogue
# --------------------------------------------------------------------------- #
# ponytail: a module-level list, not a data file. It is content, not config --
# editing Python here is the same effort as editing JSON, with no loader. Move
# it to JSON if users need to add drills without touching the source.

_CATALOG = [
    _drill(
        "zero-10", "Zero Check", "Zeroing", 10.0, 5, "Airsoft Practice 10m",
        "zero_error", [40.0, 25.0, 15.0, 8.0],
        "Before anything else: is the replica actually pointing where the "
        "sights say? Zero error is how far the group centre sits from your "
        "point of aim.",
        ["Rest the replica on a bag or bench so the hold isn't the variable.",
         "Aim at the same point for all five shots -- do not chase the group.",
         "Analyse with the target's centre as the point of aim.",
         "Adjust sights, re-shoot, repeat until the group sits on the POA."],
        ["Same aiming point every shot, even when the group is off.",
         "Let the hop-up settle: fire a few BBs before the scored group.",
         "One adjustment at a time, then confirm."],
    ),
    _drill(
        "group-10", "Five-Shot Group", "Precision", 10.0, 5,
        "Airsoft Practice 10m", "group_size", [90.0, 60.0, 40.0, 25.0],
        "The headline number. Extreme spread at a fixed distance is the "
        "cleanest read on how repeatable your setup and your hold are.",
        ["Supported position, five shots, one aiming point.",
         "Fire at a relaxed, even pace -- no rushing, no long freezing.",
         "Measure the extreme spread (largest centre-to-centre distance)."],
        ["Break the shot without disturbing the sight picture.",
         "Consistent grip pressure beats a hard grip.",
         "If one flier ruins it, note why rather than deleting it."],
    ),
    _drill(
        "mean-radius-10", "Mean Radius Grind", "Precision", 10.0, 10,
        "Airsoft Practice 10m", "mean_radius", [45.0, 30.0, 20.0, 12.0],
        "Extreme spread is decided by your two worst shots. Mean radius "
        "counts all ten, so it rewards consistency instead of luck.",
        ["Ten shots, supported, same aiming point.",
         "Take a breath between shots; this is a patience drill.",
         "Track mean radius, not the group size."],
        ["Aim for boring. Every shot the same.",
         "Ten identical shots beat eight good ones and two heroic ones."],
    ),
    _drill(
        "precision-20", "Long Precision", "Precision", 20.0, 5,
        "Airsoft Precision 20m", "group_mrad", [9.0, 6.0, 4.0, 2.5],
        "Judged in milliradians, so the standard is the same angle at any "
        "distance -- this is the drill that exposes hop-up and BB quality.",
        ["Supported position at 20 m on the precision face.",
         "Use your best BBs; weight consistency dominates at this range.",
         "Five shots, one aiming point."],
        ["Wind matters now -- note the conditions with the session.",
         "A vertical string is usually hop-up, not you.",
         "Heavier BBs generally hold a tighter angle downrange."],
    ),
    _drill(
        "hopup-ladder", "Hop-Up Ladder", "Tuning", 20.0, 5,
        "Airsoft Precision 20m", "group_size", [140.0, 100.0, 70.0, 45.0],
        "Airsoft's own tuning problem: hop-up lift has to match BB weight "
        "and range. The ladder finds the setting, the group proves it.",
        ["Start with the hop-up backed fully off.",
         "Fire a group, add a small amount of hop, fire another.",
         "Continue until BBs start to climb, then back off half a step.",
         "Log the winning setting's group as the drill session."],
        ["Change one thing at a time: hop, then BB weight, never both.",
         "Vertical stringing = over-hopped. Dropping fast = under-hopped.",
         "Record the setting in the session notes -- it drifts over time."],
    ),
    _drill(
        "cqb-7", "Close Face Score", "Speed", 7.0, 10, "Airsoft CQB 7m",
        "score", [55.0, 70.0, 82.0, 92.0],
        "Scored as a percentage of the face maximum, so hits are judged on "
        "placement rather than group shape. Close range, standing, unsupported.",
        ["Standing, unsupported, at 7 m on the CQB face.",
         "Ten shots at a comfortable working pace.",
         "Score the face; the drill tracks score as % of maximum."],
        ["Sights up before the shot, every time.",
         "Speed comes from a settled position, not a faster trigger.",
         "Called a bad shot? Note it -- that's the skill being trained."],
    ),
    _drill(
        "slow-fire-10", "Slow Fire Singles", "Fundamentals", 10.0, 10,
        "Airsoft Practice 10m", "score", [60.0, 75.0, 85.0, 93.0],
        "One shot at a time, no time pressure. The purest test of the "
        "fundamentals with everything else stripped away.",
        ["Ten single shots, resetting your whole position between each.",
         "Take at least fifteen seconds per shot.",
         "Standing or kneeling -- keep it the same for every attempt."],
        ["A full reset between shots is the drill; don't shortcut it.",
         "Stop if you start to strain -- fatigue writes bad habits.",
         "Call each shot before you look at the target."],
    ),
    _drill(
        "support-side-10", "Support-Side Group", "Positional", 10.0, 5,
        "Airsoft Practice 10m", "group_size", [160.0, 110.0, 75.0, 50.0],
        "Your weak side has no bad habits yet -- and no good ones. It is the "
        "fastest way to find out which fundamentals you actually own.",
        ["Shoulder the replica on your non-dominant side.",
         "Five shots, supported if you need it at first.",
         "Expect it to be bad. Track the trend, not the number."],
        ["Head position is the hard part -- move the replica, not your neck.",
         "Standards are deliberately looser here.",
         "Little and often beats one long painful session."],
    ),
    _drill(
        "kneeling-10", "Kneeling Group", "Positional", 10.0, 5,
        "Airsoft Practice 10m", "group_size", [130.0, 90.0, 60.0, 40.0],
        "The first position where your own wobble, not the replica, sets the "
        "group size. Bridges bench accuracy to field accuracy.",
        ["Kneeling, elbow on or just forward of the knee -- never bone on bone.",
         "Five shots at your own pace.",
         "Rebuild the position between shots."],
        ["Bone support over muscle: settle into the position, don't hold it.",
         "Exhale, let the wobble shrink, then break the shot.",
         "If the wobble never settles, the position is wrong."],
    ),
    _drill(
        "prone-20", "Supported Long Group", "Positional", 20.0, 5,
        "Airsoft Precision 20m", "group_mrad", [11.0, 7.5, 5.0, 3.0],
        "Prone or fully rested at distance -- the best group your setup is "
        "capable of. Use it as the benchmark every other position is measured "
        "against.",
        ["Prone or fully supported, at 20 m.",
         "Five shots, maximum patience.",
         "Judged angularly (mrad) so it compares with any distance."],
        ["Support the handguard, never the barrel or the hop unit.",
         "Straight-back trigger press; this is where errors show.",
         "Note the BB weight -- this drill is sensitive to it."],
    ),
    _drill(
        "distance-ladder", "Distance Ladder", "Precision", 15.0, 5,
        "Airsoft Practice 10m", "group_mrad", [10.0, 7.0, 5.0, 3.0],
        "Shoot the same drill at 7, 10 and 15 m and log each. Because the "
        "metric is angular, the three should read the same -- where they "
        "diverge is where your hop-up stops holding.",
        ["Fire a five-shot group at 7 m, then 10 m, then 15 m.",
         "Log each as its own session with the correct distance.",
         "Compare the angular group sizes -- they should be close."],
        ["A number that balloons past 10 m means hop or BB weight, not aim.",
         "Set the distances once and keep them for every attempt.",
         "The mid distance (15 m) is the one that counts for the tier."],
    ),
    _drill(
        "cold-start", "Cold Start", "Consistency", 10.0, 5,
        "Airsoft Practice 10m", "group_size", [110.0, 75.0, 50.0, 32.0],
        "Your first group of the day, before any warm-up. It is the only "
        "honest measure of what you can do on demand.",
        ["First group of the session, before any sighters.",
         "Five shots, supported, one aiming point.",
         "Log it immediately -- no re-runs."],
        ["No warm-up, no do-overs. That's the whole drill.",
         "Expect a gap versus your warm groups; closing it is the goal.",
         "Cold gas replicas will read cold too -- note the temperature."],
    ),
]

_BY_ID = dict((d["id"], d) for d in _CATALOG)


def all_drills() -> List[Dict[str, Any]]:
    return list(_CATALOG)


def get_drill(drill_id: str) -> Dict[str, Any]:
    """Look up a drill by id (case-insensitive). Raises KeyError if absent."""
    key = (drill_id or "").strip().lower()
    if key not in _BY_ID:
        raise KeyError("Unknown drill %r. Known: %s"
                       % (drill_id, ", ".join(sorted(_BY_ID))))
    return _BY_ID[key]


def families() -> List[str]:
    out = []  # type: List[str]
    for d in _CATALOG:
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
    return [standing(db, d) for d in _CATALOG]


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
    12-item list sensibly; swap in something learned if the ordering ever
    feels wrong in practice.
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

    d = get_drill("group-10")
    assert tier_for(d, 200.0) is None
    assert tier_for(d, 85.0) == "Rookie"
    assert tier_for(d, 20.0) == "Marksman"
    assert next_cutoff(d, "Rookie") == 60.0
    assert next_cutoff(d, "Marksman") is None

    db = Database()
    db.add_tool(Tool("t", "Test", category="AEG"))
    shots = [Shot(-20.0, 0.0), Shot(20.0, 0.0)]        # 40 mm spread -> Sharp
    db.add_session(Session("s1", "t", "2026-01-01", shots=shots,
                           stats=analyze_group(shots), distance_m=10.0,
                           drill_id="group-10"))
    row = standing(db, d)
    assert row["attempts"] == 1 and row["tier"] == "Sharp", row
    assert plan(db, 3)[0]["attempts"] == 0                # untried drills first
    print("drills self-check OK: %d drills, %s" % (len(_CATALOG), tier_points(db)))
