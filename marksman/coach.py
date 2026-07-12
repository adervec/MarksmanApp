"""AI coach 'cowork' folder handshake.

Inspired by Tachyread's Literary-Journey cowork: instead of calling an API from
inside the app, Marksman writes a **coach request** into a folder and a Claude
Code / desktop agent (or any Claude chat) reads it and writes back a reply.

The round trip:

1. ``marksman coach export`` writes a folder (default ``coach/`` beside the DB):
   * ``request.json``  -- versioned envelope + the full progress dataset,
   * ``request.md``    -- a paste-ready Markdown digest (schema + data), and
   * ``CLAUDE.md``     -- a brief telling an agent what to do with the folder.
2. The agent analyses the data and drops ``reply.json`` matching REPLY_SCHEMA.
3. ``marksman coach apply`` ingests it: analysis, a single focus, drills and
   per-tool tips land in ``db.settings['coaching']``.  An idempotency hash makes
   applying the same reply twice a no-op.

Everything here is pure standard library and there is **no network dependency** --
the model round-trip happens outside the app, so no API key is ever required.

The dataset builders are pure (data in, ``today`` injected) so they are testable;
only :func:`write_request` / :func:`read_reply` touch the filesystem.
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Set

from . import goals as goals_mod
from . import tracker
from .storage import Database

PROTOCOL = "marksman-coach"
PROTOCOL_VERSION = 1

DEFAULT_INSTRUCTION = (
    "Act as a supportive airsoft marksmanship coach. Read the progress dataset and "
    "return: a short analysis of the trend (what's improving, what's stalling), the "
    "single most important FOCUS for the next session, 3-6 concrete dry/live drills, "
    "and a per-tool tip where the data warrants one. If the dataset has a `goals` "
    "list, note progress toward each and let it steer the focus. Ground every claim in "
    "the numbers (group size in mm and mrad, score %, zero error, streak). Be encouraging "
    "and specific; this is practice feedback, not coaching certification or safety "
    "instruction -- never weaken the eye-protection / field-rules disclaimers."
)

# The exact shape the agent must reply with. Kept in one place so the digest, the
# CLAUDE.md brief and the validator never drift.
REPLY_SCHEMA = (
    '{\n'
    '  "analysis": "2-4 short paragraphs grounded in the numbers",\n'
    '  "focus": "the ONE thing to work on next",\n'
    '  "drills": [{"name": "", "why": "", "how": ""}],\n'
    '  "toolTips": [{"toolId": "<id from tools[].id>", "tip": ""}],\n'
    '  "notes": [{"toolId?": "", "sessionId?": "", "text": ""}]\n'
    '}'
)

# Only these top-level keys are read from a reply; everything else is ignored.
_REPLY_KEYS = ("analysis", "focus", "drills", "toolTips", "notes")
_MAX_NOTES = 50   # ponytail: hard cap on stored coaching notes; oldest fall off


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #

def current_streak(iso_dates: Set[str], today: date) -> int:
    """Consecutive days up to ``today`` that have at least one session.

    Today itself may still be empty without breaking the streak (you might shoot
    later), so we start the walk from today if present, else yesterday.
    """
    cursor = today
    if cursor.isoformat() not in iso_dates:
        cursor -= timedelta(days=1)
    streak = 0
    while cursor.isoformat() in iso_dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def content_hash(text: str) -> str:
    """djb2 hash for the idempotency ledger (don't apply the same reply twice)."""
    h = 5381
    for ch in text:
        h = ((h * 33) ^ ord(ch)) & 0xFFFFFFFF
    return "%08x" % h


def _tool_rows(db: Database) -> List[dict]:
    rows = []
    for w in sorted(db.tools.values(), key=lambda x: x.name.lower()):
        rows.append({
            "id": w.id, "name": w.name, "category": w.category,
            "bb": w.bb, "bbMm": w.bb_mm, "gas": w.is_gas,
            "sessions": len(db.sessions_for_tool(w.id)),
            **({"notes": w.notes} if w.notes else {}),
        })
    return rows


def _recent_rows(db: Database, limit: int = 25) -> List[dict]:
    analysed = [s for s in db.all_sessions() if s.stats is not None]
    analysed.sort(key=lambda s: (s.date, s.id))
    out = []
    for s in analysed[-limit:]:
        st = s.stats
        score_pct = None
        if st.total_score is not None and st.max_possible_score:
            score_pct = round(st.total_score / st.max_possible_score * 100.0, 1)
        out.append({
            "sessionId": s.id, "date": s.date, "toolId": s.tool_id,
            "target": s.target_name, "distanceM": s.distance_m,
            "shots": st.shot_count,
            "groupMm": round(st.extreme_spread_mm, 1),
            "meanRadiusMm": round(st.mean_radius_mm, 1),
            "groupMrad": _round(tracker.mm_to_mrad(st.extreme_spread_mm, s.distance_m)),
            "zeroErrorMm": round(st.poa_offset_mm, 1),
            "scorePct": score_pct,
            **({"notes": s.notes} if s.notes else {}),
        })
    return out


def _round(v: Optional[float], digits: int = 2) -> Optional[float]:
    return None if v is None else round(v, digits)


def build_dataset(db: Database, days: int = 120,
                  today: Optional[date] = None) -> Dict[str, Any]:
    """Everything a coach needs, as a compact JSON-able dict. Pure."""
    today = today or date.today()
    sessions = db.all_sessions()
    analysed = [s for s in sessions if s.stats is not None]
    all_dates = {s.date for s in analysed}
    goal_rows = goals_mod.summary(db)
    return {
        "note": ("Airsoft marksmanship progress. Group sizes are in mm (and mrad, "
                 "angular, so distances are comparable); score is % of the face max. "
                 "lower group/zero-error = better, higher score = better."),
        "windowDays": days,
        "totals": {
            "tools": len(db.tools),
            "sessions": len(analysed),
            "shots": sum(s.stats.shot_count for s in analysed),
            "firstDate": min(all_dates) if all_dates else None,
            "lastDate": max(all_dates) if all_dates else None,
        },
        "streakDays": current_streak(all_dates, today),
        "tools": _tool_rows(db),
        "overall": tracker.progress_overall(sessions).to_dict(),
        "byTool": {tid: r.to_dict()
                   for tid, r in tracker.progress_by_tool(sessions, db.tools).items()},
        "byCategory": {cat: r.to_dict()
                       for cat, r in tracker.progress_by_category(sessions, db.tools).items()},
        "recentSessions": _recent_rows(db),
        **({"goals": goal_rows} if goal_rows else {}),
    }


def build_request(dataset: Dict[str, Any], instruction: str,
                  generated_at: str) -> Dict[str, Any]:
    """The versioned envelope written as ``request.json``."""
    return {
        "protocol": PROTOCOL, "protocolVersion": PROTOCOL_VERSION,
        "kind": "coach-request", "generatedAt": generated_at,
        "instruction": instruction,
        "replySchema": REPLY_SCHEMA,
        "dataset": dataset,
    }


def build_digest(dataset: Dict[str, Any], instruction: str) -> str:
    """Human-readable Markdown you can paste into any Claude chat."""
    return "\n".join([
        "# Marksman - airsoft coaching request",
        "",
        "You are coaching an airsoft shooter from their tracked practice data.",
        "",
        "## Task",
        instruction,
        "",
        "## Reply with ONE ```json block, no prose, using the exact tool ids below",
        "```json",
        REPLY_SCHEMA,
        "```",
        "",
        "## Data",
        "```json",
        json.dumps(dataset, indent=2),
        "```",
    ])


CLAUDE_BRIEF = "\n".join([
    "# Marksman coach folder",
    "",
    "This folder is a coaching hand-off from the **Marksman** airsoft progress",
    "tracker. If you are an AI agent working in it:",
    "",
    "1. Read `request.json` (or the paste-ready `request.md`).",
    "2. Follow `request.json.instruction`; ground everything in `dataset` (group",
    "   sizes in mm/mrad, score %, zero error, streak).",
    "3. Write your answer to **`reply.json`** matching `request.json.replySchema`",
    "   exactly -- a single JSON object, no prose. Use tool ids from `dataset.tools[].id`.",
    "",
    "Then the user runs `marksman coach apply` to pull your reply back into the app.",
    "",
    "This is practice feedback, not professional coaching, medical, or safety advice.",
    "Do not weaken the eye-protection / field-rules disclaimers.",
])


# --------------------------------------------------------------------------- #
# Applying a reply (pure validation + merge)
# --------------------------------------------------------------------------- #

def parse_reply(text: str) -> dict:
    """Pull the JSON object out of a reply (a ```json fence or outermost braces)."""
    if not text or not text.strip():
        raise ValueError("Empty reply.")
    fence_start = text.find("```")
    if fence_start != -1:
        body = text[fence_start + 3:]
        if body[:4].lower() == "json":
            body = body[4:]
        end = body.find("```")
        raw = body if end == -1 else body[:end]
    else:
        raw = text[text.find("{"): text.rfind("}") + 1]
    return json.loads(raw)


def _clean_list(items: Any, keys: tuple) -> List[dict]:
    out = []
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        row = {k: it[k] for k in keys if isinstance(it.get(k), str) and it[k].strip()}
        if row:
            out.append(row)
    return out


def apply_reply(db: Database, reply: dict, now: str) -> dict:
    """Validate ``reply`` and merge it into ``db.settings['coaching']``. Pure w.r.t. IO.

    Returns a small summary (what was applied); the caller saves the DB. Applying
    the identical reply twice is a no-op (idempotency hash).
    """
    canonical = json.dumps({k: reply.get(k) for k in _REPLY_KEYS},
                           sort_keys=True, ensure_ascii=False)
    new_hash = content_hash(canonical)
    prev = db.settings.get("coaching") or {}
    if prev.get("appliedHash") == new_hash:
        return {"applied": False, "reason": "identical to the last applied reply"}

    drills = _clean_list(reply.get("drills"), ("name", "why", "how"))
    # Tool tips: keep only tips whose toolId exists.
    tips = [t for t in _clean_list(reply.get("toolTips"), ("toolId", "tip"))
            if t["toolId"] in db.tools]

    notes = list(prev.get("notes") or [])
    seen = {n.get("text") for n in notes}
    for n in _clean_list(reply.get("notes"), ("toolId", "sessionId", "text")):
        if n["text"] in seen:
            continue
        # Drop unknown ids but keep the note text.
        if n.get("toolId") and n["toolId"] not in db.tools:
            n.pop("toolId")
        if n.get("sessionId") and n["sessionId"] not in db.sessions:
            n.pop("sessionId")
        n["createdAt"] = now
        notes.append(n)
        seen.add(n["text"])
    notes = notes[-_MAX_NOTES:]

    analysis = reply.get("analysis") if isinstance(reply.get("analysis"), str) else ""
    focus = reply.get("focus") if isinstance(reply.get("focus"), str) else ""

    db.settings["coaching"] = {
        "analysis": analysis.strip(), "focus": focus.strip(),
        "drills": drills, "toolTips": tips, "notes": notes,
        "appliedHash": new_hash, "appliedAt": now,
    }
    return {
        "applied": True, "focus": focus.strip(),
        "drills": len(drills), "toolTips": len(tips), "notes": len(notes),
    }


# --------------------------------------------------------------------------- #
# Thin filesystem wrappers
# --------------------------------------------------------------------------- #

def coach_dir(db: Database) -> str:
    """Default coach folder: a ``coach/`` dir beside the database."""
    return os.path.join(os.path.dirname(os.path.abspath(db.path)), "coach")


def write_request(db: Database, out_dir: str, instruction: str,
                  now: str) -> Dict[str, str]:
    """Write request.json / request.md / CLAUDE.md into ``out_dir``. Returns paths."""
    os.makedirs(out_dir, exist_ok=True)
    dataset = build_dataset(db)
    request = build_request(dataset, instruction, now)
    paths = {
        "request": os.path.join(out_dir, "request.json"),
        "digest": os.path.join(out_dir, "request.md"),
        "brief": os.path.join(out_dir, "CLAUDE.md"),
    }
    with open(paths["request"], "w", encoding="utf-8") as fh:
        json.dump(request, fh, indent=2)
    with open(paths["digest"], "w", encoding="utf-8") as fh:
        fh.write(build_digest(dataset, instruction))
    with open(paths["brief"], "w", encoding="utf-8") as fh:
        fh.write(CLAUDE_BRIEF)
    return paths


def read_reply(path: str) -> dict:
    """Read and parse a reply file (JSON, or Markdown wrapping a ```json block)."""
    with open(path, "r", encoding="utf-8") as fh:
        return parse_reply(fh.read())
