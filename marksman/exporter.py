"""Export sessions to CSV or JSON for spreadsheets / backups.

One flat row per analysed session, with the tool joined in and group size given
both in mm and mrad so rows shot at different distances stay comparable. Pure
standard library.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Dict, List

from . import tracker
from .storage import Database

FIELDS = [
    "session_id", "date", "tool_id", "tool_name", "category", "target",
    "distance_m", "shots", "extreme_spread_mm", "mean_radius_mm",
    "group_mrad", "zero_error_mm", "total_score", "max_score", "score_pct",
    "bbs", "notes",
]


def session_rows(db: Database) -> List[Dict[str, object]]:
    """Flatten every analysed session into a list of dicts (date order)."""
    analysed = [s for s in db.all_sessions() if s.stats is not None]
    analysed.sort(key=lambda s: (s.date, s.id))
    rows = []
    for s in analysed:
        st = s.stats
        w = db.get_tool(s.tool_id)
        score_pct = None
        if st.total_score is not None and st.max_possible_score:
            score_pct = round(st.total_score / st.max_possible_score * 100.0, 1)
        mrad = tracker.mm_to_mrad(st.extreme_spread_mm, s.distance_m)
        rows.append({
            "session_id": s.id, "date": s.date, "tool_id": s.tool_id,
            "tool_name": w.name if w else "", "category": w.category if w else "",
            "target": s.target_name, "distance_m": s.distance_m,
            "shots": st.shot_count,
            "extreme_spread_mm": round(st.extreme_spread_mm, 2),
            "mean_radius_mm": round(st.mean_radius_mm, 2),
            "group_mrad": None if mrad is None else round(mrad, 3),
            "zero_error_mm": round(st.poa_offset_mm, 2),
            "total_score": st.total_score, "max_score": st.max_possible_score,
            "score_pct": score_pct, "bbs": s.bbs, "notes": s.notes,
        })
    return rows


def to_csv(db: Database) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, extrasaction="ignore")
    w.writeheader()
    for row in session_rows(db):
        w.writerow(row)
    return buf.getvalue()


def to_json(db: Database) -> str:
    return json.dumps(session_rows(db), indent=2)
