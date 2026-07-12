"""User-set practice goals: a target value for one metric, overall or per tool.

A goal is small and declarative -- "group_size <= 30 mm overall", "score >= 80%
for aeg1". Goals live in ``db.settings['goals']`` (no schema bump). Evaluation is
a pure read over the same progress reports the tracker already builds, so a goal
is "met" the moment your BEST session for that scope crosses the target.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from . import tracker
from .storage import Database

# friendly name -> (ProgressReport.metrics key, unit, lower_is_better)
METRICS = {
    "group_size":  ("extreme_spread_mm", "mm", True),
    "group_mrad":  ("group_size_mrad", "mrad", True),
    "mean_radius": ("mean_radius_mm", "mm", True),
    "zero_error":  ("poa_offset_mm", "mm", True),
    "score":       ("score_pct", "% of max", False),
}


def new_goal(metric: str, target: float, tool_id: Optional[str] = None,
             note: str = "") -> Dict[str, Any]:
    if metric not in METRICS:
        raise ValueError("unknown metric %r (choose from %s)"
                         % (metric, ", ".join(METRICS)))
    goal = {"id": uuid.uuid4().hex[:6], "metric": metric, "target": float(target)}
    if tool_id:
        goal["tool_id"] = tool_id
    if note:
        goal["note"] = note
    return goal


def _sessions_for(db: Database, goal: Dict[str, Any]):
    tid = goal.get("tool_id")
    return db.sessions_for_tool(tid) if tid else db.all_sessions()


def evaluate(db: Database, goal: Dict[str, Any]) -> Dict[str, Any]:
    """Current standing of ``goal``: best/latest vs target, and whether it's met."""
    key, unit, lower = METRICS[goal["metric"]]
    tid = goal.get("tool_id")
    tool = db.get_tool(tid) if tid else None
    scope = tool.name if tool else ("(unknown tool)" if tid else "Overall")
    rep = tracker.build_report(_sessions_for(db, goal), "goal", scope)
    m = rep.metrics.get(key)
    out = {
        "id": goal["id"], "metric": goal["metric"], "target": goal["target"],
        "unit": unit, "lowerIsBetter": lower, "scope": scope,
        "best": None, "latest": None, "met": None, "count": 0,
        **({"note": goal["note"]} if goal.get("note") else {}),
    }
    if m and m.count:
        out.update(best=m.best, latest=m.latest, count=m.count)
        out["met"] = (m.best <= goal["target"]) if lower else (m.best >= goal["target"])
    return out


def summary(db: Database) -> List[Dict[str, Any]]:
    """Evaluate every stored goal (for the coach dataset / listings)."""
    return [evaluate(db, g) for g in db.settings.get("goals", [])]
