"""The web app's API, with no transport attached.

Every endpoint the app calls lives here as a plain function of
``(store, body) -> dict``.  ``store`` is anything with a ``db_path`` and a
``lock``: the local HTTP server in :mod:`marksman.web` satisfies it, and so
does the browser shim when the same package runs client-side under Pyodide.

Keeping it separate is what lets one UI run against two backends without a
second implementation of the maths.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
import os
import tempfile
import threading
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from . import drills as drills_mod
from . import exporter as exporter_mod
from . import goals as goals_mod
from . import logo as logo_mod
from . import packs as packs_mod
from . import render as render_mod
from . import sheets as sheets_mod
from . import targets as targets_mod
from . import tracker
from .goals import METRICS
from .grouping import analyze_group
from .models import Session, Shot, Tool, TargetSpec
from .storage import Database, DEFAULT_DB_PATH


class Store:
    """The little that the handlers need from whatever is hosting them."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self.lock = threading.Lock()


MAX_BODY = 30_000_000       # bytes; big enough for a phone photo, base64'd
MAX_IMAGE = 20_000_000      # decoded image bytes
MAX_SHOTS = 500
DEFAULT_PORT = 8317


class _Bad(Exception):
    """A client error worth a 400 with its message."""


# --------------------------------------------------------------------------- #
# Request payload validation (trust boundary: anything on the LAN can POST)
# --------------------------------------------------------------------------- #

def _num(value: Any, name: str, lo: float = 0.0, hi: float = 1000.0) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise _Bad("bad %s" % name)
    if not math.isfinite(v) or not (lo < v <= hi):
        raise _Bad("%s out of range" % name)
    return v


def _shots(body: Dict[str, Any]) -> List[Shot]:
    raw = body.get("shots")
    if not isinstance(raw, list):
        raise _Bad("shots must be a list")
    if len(raw) > MAX_SHOTS:
        raise _Bad("too many shots (max %d)" % MAX_SHOTS)
    out = []
    for item in raw:
        try:
            x, y = float(item["x_mm"]), float(item["y_mm"])
        except (TypeError, KeyError, ValueError):
            raise _Bad("bad shot entry")
        if not (math.isfinite(x) and math.isfinite(y)) or abs(x) > 10000 or abs(y) > 10000:
            raise _Bad("shot out of range")
        out.append(Shot(x, y))
    return out


def _target(db: Database, name: str) -> TargetSpec:
    key = name.strip().lower()
    if key in db.custom_targets:
        return db.custom_targets[key]
    try:
        return targets_mod.get_target(name)
    except KeyError:
        raise _Bad("unknown target %r" % name)


def _session_value(s: Session, metric: str) -> Optional[float]:
    """The value a session earns on a drill's metric (mirrors the tracker keys)."""
    key = METRICS[metric][0]
    st = s.stats
    if st is None:
        return None
    if key == "extreme_spread_mm":
        return st.extreme_spread_mm
    if key == "mean_radius_mm":
        return st.mean_radius_mm
    if key == "poa_offset_mm":
        return st.poa_offset_mm
    if key == "group_size_mrad":
        return tracker.mm_to_mrad(st.extreme_spread_mm, s.distance_m)
    if key == "score_pct":
        if not st.max_possible_score:
            return None
        return (st.total_score or 0.0) / st.max_possible_score * 100.0
    return None


# --------------------------------------------------------------------------- #
# API payload builders
# --------------------------------------------------------------------------- #

def _sess_row(db: Database, s: Session) -> Dict[str, Any]:
    st = s.stats
    tool = db.get_tool(s.tool_id)
    drill_name = ""
    if s.drill_id:
        try:
            drill_name = drills_mod.get_drill(s.drill_id)["name"]
        except KeyError:
            drill_name = s.drill_id
    score = None
    if st and st.max_possible_score:
        score = (st.total_score or 0.0) / st.max_possible_score * 100.0
    return {
        "id": s.id, "date": s.date,
        "tool": tool.name if tool else s.tool_id,
        "drill": drill_name, "distance_m": s.distance_m,
        "shots": st.shot_count if st else len(s.shots),
        "group_mm": st.extreme_spread_mm if st else None,
        "group_mrad": (tracker.mm_to_mrad(st.extreme_spread_mm, s.distance_m)
                       if st else None),
        "mean_radius_mm": st.mean_radius_mm if st else None,
        "zero_mm": st.poa_offset_mm if st else None,
        "score_pct": score,
    }


def _state(db: Database) -> Dict[str, Any]:
    catalog = {d["id"]: d for d in drills_mod.all_drills()}
    drill_rows = []
    for row in drills_mod.standings(db):
        d = catalog[row["id"]]
        merged = dict(row)
        merged.update(distance_m=d["distance_m"], shotsNeeded=d["shots"],
                      target=d["target"], cutoffs=d["cutoffs"], why=d["why"],
                      how=d["how"], cues=d["cues"])
        drill_rows.append(merged)

    specs = {}
    for name in targets_mod.list_targets():
        specs[name] = targets_mod.get_target(name)
    for t in db.custom_targets.values():
        specs[t.name] = t
    target_rows = [{
        "name": sp.name,
        "rings": [r.diameter_mm for r in sp.rings],       # ascending
        "face_mm": sp.face_width_mm or sp.outer_radius_mm * 2.4,
    } for sp in specs.values()]

    sessions = sorted(db.all_sessions(), key=lambda s: (s.date, s.id), reverse=True)
    pack_rows = [{"id": p["id"], "name": p["name"], "active": p["active"],
                  "sensitivity": p.get("sensitivity", 5), "reason": p["reason"],
                  "drills": len(p["drills"]), "safety": p.get("safety", ""),
                  "bundled": p.get("bundled", False)}
                 for p in packs_mod.status(db)]
    return {
        "tools": [{"id": w.id, "name": w.name, "category": w.category}
                  for w in db.tools.values()],
        "targets": target_rows,
        "drills": drill_rows,
        "plan": drills_mod.plan(db, 3),
        "tierPoints": drills_mod.tier_points(db),
        "goals": goals_mod.summary(db),
        "sessions": [_sess_row(db, s) for s in sessions[:50]],
        "categories": packs_mod.categories(db),
        "defaultTarget": packs_mod.default_target(db),
        "goalMetrics": sorted(METRICS),
        "sheets": sheets_mod.catalog(),
        "packs": pack_rows,
        "terms": {w: packs_mod.term(w, db) for w in ("tool", "tools",
                                                     "projectile", "projectiles")},
        # Photos already logged, so the Drive picker can grey them out. Derived
        # from the sessions themselves, so it survives sync and export.
        "imported": sorted(set(s.source_ref[6:] for s in db.all_sessions()
                               if s.source_ref.startswith("drive:"))),
    }


def _stats(db: Database, body: Dict[str, Any]) -> Dict[str, Any]:
    shots = _shots(body)
    if not shots:
        return {"n": 0}
    tname = str(body.get("target") or "")
    spec = _target(db, tname) if tname else None
    st = analyze_group(shots, target=spec)
    dist = _num(body.get("distance_m"), "distance_m")
    tool = db.get_tool(str(body.get("tool_id") or ""))
    correction = tracker.format_correction(
        tracker.sight_correction(st, dist, tool.sight_click_mrad if tool else None),
        dist)
    return {
        "correction": correction,
        "n": st.shot_count,
        "group_mm": st.extreme_spread_mm,
        "group_mrad": tracker.mm_to_mrad(st.extreme_spread_mm, dist),
        "mean_radius_mm": st.mean_radius_mm,
        "zero_mm": st.poa_offset_mm,
        "cx": st.center_x_mm, "cy": st.center_y_mm,
        "score_pct": ((st.total_score or 0.0) / st.max_possible_score * 100.0
                      if st.max_possible_score else None),
    }


def _save_session(server: Store, body: Dict[str, Any]) -> Dict[str, Any]:
    with server.lock:
        db = Database.load(server.db_path)
        tool = db.get_tool(str(body.get("tool_id") or ""))
        if tool is None:
            raise _Bad("unknown tool")
        shots = _shots(body)
        if not shots:
            raise _Bad("no shots -- tap them onto the target first")
        tname = str(body.get("target") or "")
        spec = _target(db, tname) if tname else None
        drill_id = str(body.get("drill_id") or "")
        drill = None
        if drill_id:
            try:
                drill = drills_mod.get_drill(drill_id)
            except KeyError:
                raise _Bad("unknown drill %r" % drill_id)
        day = str(body.get("date") or date.today().isoformat())
        try:
            datetime.fromisoformat(day)
        except ValueError:
            raise _Bad("bad date %r (use YYYY-MM-DD)" % day)
        source_ref = str(body.get("source_ref") or "")[:120]
        if source_ref and not source_ref.startswith("drive:"):
            raise _Bad("unknown source_ref")
        stats = analyze_group(shots, target=spec,
                              projectile_mm=tool.projectile_mm or 0.0)
        s = Session(
            id=uuid.uuid4().hex[:8], tool_id=tool.id, date=day, shots=shots,
            stats=stats, distance_m=_num(body.get("distance_m"), "distance_m"),
            target_name=spec.name if spec else "", drill_id=drill_id,
            projectiles=str(body.get("projectiles") or "")[:100],
            source_ref=source_ref,
            notes=str(body.get("notes") or "")[:2000],
        )
        db.add_session(s)
        db.save()

    resp = {"id": s.id, "group_mm": stats.extreme_spread_mm}   # type: Dict[str, Any]
    if drill:
        value = _session_value(s, drill["metric"])
        row = drills_mod.standing(db, drill)
        resp.update(drill=drill["name"], value=value, unit=row["unit"],
                    attemptTier=drills_mod.tier_for(drill, value),
                    tier=row["tier"], best=row["best"])
    return resp


def _analyze_image(db: Database, body: Dict[str, Any]) -> Dict[str, Any]:
    """Find the hits in an uploaded photo and return them for review.

    Nothing is saved here: the shots go back to the page, land on the target
    canvas, and the user corrects them before saving like any other session.
    """
    from . import vision

    raw = body.get("image_b64")
    if not isinstance(raw, str) or not raw:
        raise _Bad("no image")
    try:
        data = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error):
        raise _Bad("image isn't valid base64")
    if len(data) > MAX_IMAGE:
        raise _Bad("image is too big (max %d MB)" % (MAX_IMAGE // 1_000_000))

    tname = str(body.get("target") or "")
    spec = _target(db, tname) if tname else None
    face = _num(body.get("face_mm"), "face_mm", 1.0, 20000.0)
    if face is None and spec is not None:
        face = spec.face_width_mm
    if face is None:
        raise _Bad("no scale: pick a target face, or give the photographed width")

    suffix = ".png" if data[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        try:
            found = vision.analyze_image(
                tmp, mode=str(body.get("mode") or "marker"),
                color=str(body.get("color") or "red"),
                face_width_mm=float(face), auto_center=True)
        except ValueError as e:
            raise _Bad(str(e))
        except Exception as e:                      # unreadable / unsupported file
            raise _Bad("couldn't read that image (%s). PNG always works; other "
                       "formats need Pillow installed." % e.__class__.__name__)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    return {
        "shots": [{"x_mm": round(s.x_mm, 2), "y_mm": round(s.y_mm, 2)}
                  for s in found.shots],
        "mm_per_px": found.mm_per_px,
        "size_px": list(found.image_size_px),
    }


def _bundle(db: Database) -> Dict[str, Any]:
    """The whole dataset in the shape that gets synced to Drive."""
    return {
        "app": "marksman", "version": 1,
        "tools": [t.to_dict() for t in db.tools.values()],
        "sessions": [s.to_dict() for s in db.sessions.values()],
        # The access key is this machine's, not part of the dataset.
        "settings": {k: v for k, v in db.settings.items() if k != "web_key"},
    }


def _sync(server: Store, body: Dict[str, Any]) -> Dict[str, Any]:
    """Merge a bundle pulled from Drive, and hand back the merged result.

    ponytail: union by id, local wins ties -- sessions are append-only in
    practice, so there is nothing to reconcile field by field. Add per-record
    timestamps and tombstones if editing on two devices ever becomes a thing.
    """
    remote = body.get("remote")
    if remote is not None and not isinstance(remote, dict):
        raise _Bad("remote bundle should be an object")
    if isinstance(remote, dict) and remote.get("app") not in (None, "marksman"):
        raise _Bad("that file isn't a Marksman backup")

    added_tools = added_sessions = 0
    with server.lock:
        db = Database.load(server.db_path)
        if isinstance(remote, dict):
            for td in remote.get("tools", []):
                try:
                    tool = Tool.from_dict(td)
                except (KeyError, TypeError):
                    continue
                if tool.id not in db.tools:
                    db.tools[tool.id] = tool
                    added_tools += 1
            for sd in remote.get("sessions", []):
                try:
                    sess = Session.from_dict(sd)
                except (KeyError, TypeError):
                    continue
                if sess.id not in db.sessions and sess.tool_id in db.tools:
                    db.sessions[sess.id] = sess
                    added_sessions += 1
            if added_tools or added_sessions:
                db.save()
        merged = _bundle(db)
    return {"bundle": merged, "addedTools": added_tools,
            "addedSessions": added_sessions,
            "sessions": len(merged["sessions"])}


def _printable(db: Database, query: Dict[str, List[str]]) -> bytes:
    """A true-scale printable target, ready for the browser's print dialog.

    ``?face=`` prints a scoring face; ``?design=`` prints a sheet from the
    parametric catalogue ('dots-15', 'bulls-40', ...) instead.
    """
    paper = (query.get("paper") or ["a4"])[0]
    design = (query.get("design") or [""])[0]
    try:
        if design:
            page = sheets_mod.make(design, paper=paper)
        else:
            spec = _target(db, (query.get("face") or [""])[0])
            raw = (query.get("distance") or [""])[0]
            distance = float(raw) if raw else None
            if distance is not None and not (0 < distance <= 1000):
                raise ValueError("distance out of range")
            page = render_mod.target_html(spec, distance_m=distance,
                                          paper=paper)
    except (KeyError, ValueError) as e:
        raise _Bad(str(e.args[0] if e.args else e))
    return page.encode("utf-8")


def _goal(server: Store, body: Dict[str, Any]) -> Dict[str, Any]:
    """Add or remove a practice goal (the desktop app's Goals tab, on a phone)."""
    action = str(body.get("action") or "")
    with server.lock:
        db = Database.load(server.db_path)
        stored = list(db.settings.get("goals") or [])
        if action == "add":
            metric = str(body.get("metric") or "")
            if metric not in METRICS:
                raise _Bad("unknown metric %r (choose from %s)"
                           % (metric, ", ".join(METRICS)))
            target = _num(body.get("target"), "target", 0.0, 100000.0)
            if target is None:
                raise _Bad("a goal needs a target value")
            tool_id = str(body.get("tool_id") or "")
            if tool_id and tool_id not in db.tools:
                raise _Bad("unknown tool")
            stored.append(goals_mod.new_goal(metric, target, tool_id or None,
                                             str(body.get("note") or "")[:200]))
        elif action == "rm":
            gid = str(body.get("id") or "")
            stored = [g for g in stored if g.get("id") != gid]
            if len(stored) == len(db.settings.get("goals") or []):
                raise _Bad("no such goal")
        else:
            raise _Bad("unknown action %r" % action)
        db.settings["goals"] = stored
        db.save()
        return {"goals": goals_mod.summary(db)}


def _delete_session(server: Store, body: Dict[str, Any]) -> Dict[str, Any]:
    """Drop one session. Mistyped shots at the field shouldn't need a laptop."""
    sid = str(body.get("id") or "")
    with server.lock:
        db = Database.load(server.db_path)
        if sid not in db.sessions:
            raise _Bad("no such session")
        del db.sessions[sid]
        db.save()
    return {"deleted": sid}


_ICON = []          # generated once, then reused


def _icon_png() -> bytes:
    """The app icon, drawn by the logo module -- no asset file to ship."""
    if not _ICON:
        from . import imageio
        _ICON.append(imageio.encode_png(logo_mod.make_logo(512)))
    return _ICON[0]


def _add_tool(server: Store, body: Dict[str, Any]) -> Dict[str, Any]:
    name = " ".join(str(body.get("name") or "").split())[:60]
    if not name:
        raise _Bad("tool needs a name")
    category = str(body.get("category") or "Other")[:30]
    with server.lock:
        db = Database.load(server.db_path)
        base = "".join(ch for ch in name.lower() if ch.isalnum()) or uuid.uuid4().hex[:6]
        tid, n = base[:16], 2
        while tid in db.tools:
            tid, n = base[:14] + str(n), n + 1
        db.add_tool(Tool(tid, name, category=category))
        db.save()
    return {"id": tid, "name": name}


# --------------------------------------------------------------------------- #
# Routing -- shared, so the HTTP server and the in-browser build cannot drift
# --------------------------------------------------------------------------- #

# Handlers that only read take a loaded Database; handlers that write take the
# store and do their own load/lock/save.
_READS = {"/api/state": lambda db, body: _state(db),
          "/api/stats": _stats,
          "/api/analyze-image": _analyze_image}
_WRITES = {"/api/session": _save_session,
           "/api/tool": _add_tool,
           "/api/sync": _sync,
           "/api/goal": _goal,
           "/api/session/delete": _delete_session}


def dispatch(store: Store, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Answer one API call. Raises :class:`_Bad` for anything a client got wrong."""
    if path in _READS:
        return _READS[path](Database.load(store.db_path), body or {})
    if path in _WRITES:
        return _WRITES[path](store, body or {})
    raise _Bad("no such endpoint: %s" % path)


def download(store: Store, path: str,
             query: Optional[Dict[str, List[str]]] = None) -> "tuple[str, bytes]":
    """The app's non-JSON GETs: a printable face, or an export. -> (mime, bytes)"""
    db = Database.load(store.db_path)
    if path == "target.html":
        return "text/html; charset=utf-8", _printable(db, query or {})
    if path == "export.csv":
        return "text/csv; charset=utf-8", exporter_mod.to_csv(db).encode("utf-8")
    if path == "export.json":
        return "application/json; charset=utf-8", exporter_mod.to_json(db).encode("utf-8")
    raise _Bad("no such file: %s" % path)


def self_check() -> None:
    """Every route the page calls is one the router knows."""
    import os as _os
    page = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                         "webapp", "index.html")
    with open(page, encoding="utf-8") as fh:
        text = fh.read()
    called = set(re.findall(r'api\("(/api/[a-z/-]+)"', text))
    known = set(_READS) | set(_WRITES)
    assert called, "found no api() calls in the page -- did the seam move?"
    assert called <= known, "page calls unrouted endpoints: %s" % sorted(called - known)
    for name in ("export.csv", "export.json", "target.html"):
        assert name in text, "the page stopped offering %s" % name
    print("webapi self-check OK: %d endpoints, %d used by the page"
          % (len(known), len(called)))


if __name__ == "__main__":
    self_check()
