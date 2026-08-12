"""Mobile web app served on your LAN (standard library only).

``marksman web`` starts a small HTTP server so a phone on the same Wi-Fi can
log sessions (tap shots onto the target face), follow the drill plan and see
progress.  Desktop browsers work too -- it is the same data file the CLI and
the tkinter app use, so everything stays in sync.

Security: every request must carry a per-run random token, printed as part
of the URL and then kept in a cookie.  That keeps casual LAN neighbours out;
it is NOT hardened for the open internet -- don't port-forward it.

ponytail: one HTML page inline in this file, no framework, no build step.
If the page ever outgrows a single string, move it to a package data file.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import math
import os
import secrets
import socket
import tempfile
import threading
import uuid
from datetime import date, datetime
from http import cookies as http_cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

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


def _save_session(server: ThreadingHTTPServer, body: Dict[str, Any]) -> Dict[str, Any]:
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


def _sync(server: ThreadingHTTPServer, body: Dict[str, Any]) -> Dict[str, Any]:
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


def _goal(server: ThreadingHTTPServer, body: Dict[str, Any]) -> Dict[str, Any]:
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


def _delete_session(server: ThreadingHTTPServer, body: Dict[str, Any]) -> Dict[str, Any]:
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


def _add_tool(server: ThreadingHTTPServer, body: Dict[str, Any]) -> Dict[str, Any]:
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
# HTTP plumbing
# --------------------------------------------------------------------------- #

class Handler(BaseHTTPRequestHandler):

    def log_message(self, *args):            # keep the terminal quiet
        pass

    def _authed(self) -> bool:
        token = self.server.token
        q = parse_qs(urlparse(self.path).query).get("k", [""])[0]
        if q and hmac.compare_digest(q, token):
            return True
        jar = http_cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return "k" in jar and hmac.compare_digest(jar["k"].value, token)

    def _send(self, code: int, body, ctype: str = "application/json",
              extra=None) -> None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/icon.png":
            # No user data in a logo, and the manifest fetches it without
            # credentials -- so this one is open.
            self._send(200, _icon_png(), "image/png",
                       [("Cache-Control", "max-age=86400")])
            return
        if not self._authed():
            self._send(403, b"Missing or bad access code. Open the full URL "
                            b"printed by 'marksman web'.", "text/plain; charset=utf-8")
            return
        if path == "/":
            cookie = "k=%s; Path=/; HttpOnly; SameSite=Lax" % self.server.token
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8",
                       [("Set-Cookie", cookie)])
        elif path == "/api/state":
            self._send(200, _state(Database.load(self.server.db_path)))
        elif path == "/manifest.webmanifest":
            self._send(200, json.dumps(self._manifest()).encode("utf-8"),
                       "application/manifest+json")
        elif path in ("/target.html", "/export.csv", "/export.json"):
            db = Database.load(self.server.db_path)
            try:
                if path == "/target.html":
                    self._send(200, _printable(db, parse_qs(parsed.query)),
                               "text/html; charset=utf-8")
                    return
                csv = path.endswith(".csv")
                data = (exporter_mod.to_csv(db) if csv
                        else exporter_mod.to_json(db)).encode("utf-8")
                name = "marksman-sessions." + ("csv" if csv else "json")
                self._send(200, data,
                           ("text/csv" if csv else "application/json")
                           + "; charset=utf-8",
                           [("Content-Disposition",
                             'attachment; filename="%s"' % name)])
            except _Bad as e:
                self._send(400, str(e).encode("utf-8"),
                           "text/plain; charset=utf-8")
        else:
            self._send(404, {"error": "not found"})

    def _manifest(self) -> Dict[str, Any]:
        """Enough for "add to home screen" to give a real app icon."""
        return {
            "name": "Marksman", "short_name": "Marksman",
            "description": "Foam dart drills and progress tracking.",
            # The key rides along so an installed shortcut keeps working.
            "start_url": "/?k=" + self.server.token,
            "scope": "/", "display": "standalone", "orientation": "any",
            "background_color": "#141821", "theme_color": "#141821",
            "icons": [{"src": "/icon.png", "sizes": "512x512",
                       "type": "image/png", "purpose": "any maskable"}],
        }

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not self._authed():
            self._send(403, {"error": "bad access code"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self._send(413, {"error": "body too large"})
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise _Bad("expected a JSON object")
            if path == "/api/stats":
                resp = _stats(Database.load(self.server.db_path), body)
            elif path == "/api/session":
                resp = _save_session(self.server, body)
            elif path == "/api/tool":
                resp = _add_tool(self.server, body)
            elif path == "/api/analyze-image":
                resp = _analyze_image(Database.load(self.server.db_path), body)
            elif path == "/api/sync":
                resp = _sync(self.server, body)
            elif path == "/api/goal":
                resp = _goal(self.server, body)
            elif path == "/api/session/delete":
                resp = _delete_session(self.server, body)
            else:
                self._send(404, {"error": "not found"})
                return
        except _Bad as e:
            self._send(400, {"error": str(e)})
            return
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send(400, {"error": "invalid JSON"})
            return
        self._send(200, resp)


def _lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:            # UDP connect sends nothing; it just picks the outbound iface
        s.connect(("192.0.2.1", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def make_server(db_path: str = DEFAULT_DB_PATH, host: str = "",
                port: int = DEFAULT_PORT, token: Optional[str] = None
                ) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.db_path = db_path
    httpd.lock = threading.Lock()
    db = Database.load(db_path)
    if not token:
        # Keep the key across runs, or every restart breaks the phone's
        # bookmark and its installed icon. Rotate it with --new-key.
        token = db.settings.get("web_key")
        if not token:
            token = secrets.token_urlsafe(8)
            db.settings["web_key"] = token
            db.save()
    httpd.token = token
    packs_mod.load(db)                         # content: drills, faces, terms
    return httpd


def serve(db_path: str = DEFAULT_DB_PATH, host: str = "",
          port: int = DEFAULT_PORT) -> int:
    """Run the server until Ctrl+C.  Returns a process exit code."""
    try:
        httpd = make_server(db_path, host, port)
    except OSError as e:
        print("error: can't listen on port %d (%s). Try --port." % (port, e))
        return 1
    bound = httpd.server_address[1]
    print("Marksman web is running (Ctrl+C to stop).")
    print("  this machine:  http://127.0.0.1:%d/?k=%s" % (bound, httpd.token))
    print("  your phone:    http://%s:%d/?k=%s   (same Wi-Fi)"
          % (_lan_ip(), bound, httpd.token))
    print("The link carries an access code that stays the same across runs, so")
    print("you can bookmark it or add it to your phone's home screen. Anyone on")
    print("your network with the full link can view and add sessions -- don't")
    print("expose it to the internet. Rotate it with: marksman web --new-key")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0


# --------------------------------------------------------------------------- #
# The page (inline: no build step, no framework, no external requests)
# --------------------------------------------------------------------------- #

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#141821">
<!-- use-credentials: the manifest is behind the same access code as the app. -->
<link rel="manifest" href="/manifest.webmanifest" crossorigin="use-credentials">
<link rel="apple-touch-icon" href="/icon.png">
<link rel="icon" href="/icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Marksman">
<title>Marksman</title>
<style>
:root{--bg:#141821;--card:#1d2430;--line:#2f3a49;--fg:#f2ece1;--mut:#95a0af;
      --acc:#ff7a29;--acc2:#2fc4b2;--foam:#f5ebda;--tip:#ffd24a;
      --good:#2fc4b2;--bad:#ff5c5c;--field:#151b24}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--fg);
     font:16px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
#scroll{padding-bottom:70px}
/* Orientation forced by the user's setting, not the accelerometer: when the
   device disagrees, the whole app is rotated 90 degrees via CSS. */
body.rotcw,body.rotccw{overflow:hidden}
body.rotcw #rot,body.rotccw #rot{position:fixed;width:100vh;height:100vw;
    transform-origin:top left;background:var(--bg)}
body.rotcw #rot{top:0;left:100vw;transform:rotate(90deg)}
body.rotccw #rot{top:100vh;left:0;transform:rotate(-90deg)}
body.rotcw #scroll,body.rotccw #scroll{height:100%;overflow-y:auto}
header{position:sticky;top:0;z-index:2;background:var(--bg);padding:12px 16px;
       border-bottom:1px solid var(--line);display:flex;justify-content:space-between;
       align-items:baseline}
header b{color:var(--acc);letter-spacing:.06em}
/* A foam dart, in CSS: cream body, orange tip, lying beside the wordmark. */
.dart{display:inline-block;width:26px;height:9px;border-radius:5px;
  background:linear-gradient(90deg,var(--acc) 0 36%,var(--foam) 36% 100%);
  box-shadow:inset 0 -2px 0 rgba(0,0,0,.18);vertical-align:-1px;margin-right:8px}
.card{border-radius:14px}
.btn,.ghost,.x{border-radius:10px}
main{max-width:560px;margin:0 auto;padding:12px}
section{display:none}section.on{display:block}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
      padding:12px;margin-bottom:10px}
h2{font-size:13px;color:var(--mut);text-transform:uppercase;letter-spacing:.08em;
   margin:16px 4px 8px}
.muted{color:var(--mut);font-size:13px}
.big{font-size:26px;font-weight:700}
select,input,textarea{width:100%;background:var(--field);color:var(--fg);
  border:1px solid var(--line);border-radius:8px;padding:10px;font-size:16px}
label{font-size:13px;color:var(--mut);display:block;margin:10px 0 4px}
.row{display:flex;gap:8px;align-items:flex-end}.row>*{flex:1}
#addTool{flex:0 0 56px}
button{border:0;border-radius:8px;padding:12px;font-size:16px;cursor:pointer}
.btn{width:100%;background:var(--acc);color:#1a1206;font-weight:700}
.ghost{background:var(--field);color:var(--fg);border:1px solid var(--line)}
.small{width:auto;padding:8px 12px;font-size:14px}
canvas{width:100%;max-width:420px;display:block;margin:0 auto;touch-action:none;
       border-radius:10px}
nav{position:fixed;bottom:0;left:0;right:0;display:flex;background:var(--card);
    border-top:1px solid var(--line);z-index:3}
nav button{flex:1;background:none;color:var(--mut);border-radius:0;font-size:14px;
           padding:12px 0 calc(10px + env(safe-area-inset-bottom))}
nav button.on{color:var(--acc);font-weight:700}
.badge{display:inline-block;border-radius:99px;padding:1px 9px;font-size:12px;
       font-weight:700;color:#14181c;background:var(--mut)}
.item{display:flex;justify-content:space-between;gap:8px;align-items:center;
      padding:9px 2px;border-bottom:1px solid var(--line)}
.item:last-child{border-bottom:0}
.ok{color:var(--good)}.no{color:var(--bad)}
#toast{position:fixed;left:50%;transform:translateX(-50%);top:14px;z-index:9;
  background:var(--acc);color:#1a1206;font-weight:700;border-radius:10px;
  padding:10px 16px;display:none;max-width:90%}
#orient{background:none;border:1px solid var(--line);color:var(--mut);
  border-radius:6px;padding:4px 9px;font-size:12px}
.hright{display:flex;gap:10px;align-items:center}
ul{padding-left:20px;font-size:14px}li{margin:3px 0}
a{color:var(--acc)}
.hide{display:none}
code{background:var(--field);padding:1px 5px;border-radius:4px;font-size:12px}
summary{cursor:pointer}
.filerow{width:100%;text-align:left;background:var(--card);color:var(--fg);
  border:1px solid var(--line);margin-bottom:6px;display:flex;
  justify-content:space-between;gap:10px;align-items:center}
.filerow.done{color:var(--mut)}
nav button{font-size:13px}
.rowbtns{display:flex;gap:6px;align-items:center;flex-shrink:0}
.x{background:none;border:1px solid var(--line);color:var(--mut);width:auto;
   border-radius:6px;padding:4px 9px;font-size:13px}
.x:hover{color:var(--bad)}
.linkrow{display:flex;gap:14px;flex-wrap:wrap;margin-top:10px;font-size:14px}
</style>
</head>
<body>
<div id="rot">
<div id="scroll">
<header><b><i class="dart"></i>MARKSMAN</b><span class="hright">
  <button id="orient" aria-label="Screen orientation"
    title="lock the layout portrait or landscape">auto</button>
  <span id="points" class="muted"></span></span></header>
<main>

<section id="home" class="on">
  <div class="card"><span id="tp" class="big"></span><div id="tpsub" class="muted"></div></div>
  <h2>Today's plan</h2><div id="plan"></div>
  <div class="item" style="border:0;padding:0"><h2>Goals</h2>
    <button class="ghost small" id="addGoal">+ goal</button></div>
  <div id="goals" class="card"></div>
  <h2>Recent</h2><div id="recent" class="card"></div>
  <h2>Equipment packs</h2><div id="packs" class="card"></div>
  <div id="safety"></div>
  <p class="muted" style="margin:14px 4px 4px">Marksman is a hobby project by a
    software developer — not a coach, instructor, doctor or lawyer. It computes
    metrics for personal progress tracking only. Tiers are practice standards
    set by whoever wrote the pack, not an official classification. Obey your
    local laws and handle whatever you own safely.</p>
</section>

<section id="drills"><div id="drillList"></div></section>

<section id="log">
  <div class="card">
    <div class="row"><div>
      <label for="tool">Tool</label><select id="tool"></select></div>
      <button class="ghost small" id="addTool" title="add a tool" aria-label="Add a tool">+</button>
    </div>
    <label for="drill">Drill (optional)</label><select id="drill"></select>
    <div id="dhint" class="muted"></div>
    <div class="row">
      <div><label for="target">Target face</label><select id="target"></select></div>
      <div><label for="dist">Distance m</label><input id="dist" type="number" inputmode="decimal" min="1" max="1000"></div>
    </div>
    <div class="row">
      <div><label for="design">Print</label><select id="design"></select></div>
      <div><label for="paper">Paper</label>
        <select id="paper"><option value="a4">A4</option>
          <option value="letter">Letter</option><option value="a3">A3</option>
          <option value="a5">A5</option></select></div>
    </div>
    <a class="btn ghost small" id="printFace" target="_blank" rel="noopener"
       style="display:block;text-align:center;text-decoration:none;padding:10px 12px;margin-bottom:10px"
       >Print at true size</a>
    <label for="date">Date</label><input id="date" type="date">
  </div>
  <div class="card">
    <canvas id="cv" height="420" role="img" aria-label="Target face. Tap to place a shot, or type coordinates below."></canvas>
    <p id="stats" class="muted" style="text-align:center;margin:8px 0;white-space:pre-line">Tap the target to place shots.</p>
    <div class="row">
      <button class="ghost" id="undo">Undo</button>
      <button class="ghost" id="clear">Clear</button>
    </div>
  </div>
  <div class="card">
    <div class="row">
      <div><label for="sx">Shot x (mm)</label>
        <input id="sx" type="number" inputmode="decimal" step="0.1"></div>
      <div><label for="sy">Shot y (mm)</label>
        <input id="sy" type="number" inputmode="decimal" step="0.1"></div>
    </div>
    <button class="ghost small" id="addShot" style="width:100%;margin-bottom:10px"
      >Add that shot</button>
    <label for="notes">Notes</label><textarea id="notes" rows="2"></textarea>
    <div style="height:10px"></div>
    <button class="btn" id="save">Save session</button>
  </div>
</section>

<section id="sessions">
  <div class="card">
    <div class="row">
      <div><label for="metric">Trend</label><select id="metric">
        <option value="group_mm">Group size (mm)</option>
        <option value="group_mrad">Group size (mrad)</option>
        <option value="mean_radius_mm">Mean radius (mm)</option>
        <option value="zero_mm">Zero error (mm)</option>
        <option value="score_pct">Score (%)</option>
      </select></div>
      <div><label for="chartTool">Tool</label><select id="chartTool"></select></div>
    </div>
    <div id="chart"></div>
    <div id="chartSub" class="muted" style="text-align:center"></div>
  </div>
  <div id="sessList" class="card"></div>
  <div class="linkrow"><a href="/export.csv">Export CSV</a>
    <a href="/export.json">Export JSON</a></div>
  <p class="muted" style="margin-top:8px">Tap a session's &times; to delete it.</p>
</section>

<section id="drive">
  <h2>Sessions across devices</h2>
  <div class="card">
    <p class="muted">Your log syncs through a private folder only this app can
      see. Nothing else in your Drive is touched.</p>
    <div style="height:8px"></div>
    <button class="btn" id="dConnect">Connect Google Drive</button>
    <button class="btn hide" id="dSync">Sync now</button>
    <div style="height:6px"></div>
    <button class="ghost hide" id="dOut" style="width:100%">Sign out</button>
    <p class="muted" id="dAcct"></p>
    <p class="muted" id="dSyncMsg"></p>
  </div>

  <h2>Target photos</h2>
  <div class="card">
    <p class="muted">Photograph your targets into a Drive folder, then pull them
      in here and let Marksman find the hits.</p>
    <label>Folder link</label>
    <input type="text" id="dFolder" placeholder="paste the Drive folder link">
    <div style="height:8px"></div>
    <div class="row">
      <div><label for="dMode">Marked with</label><select id="dMode">
        <option value="marker">Red marker dots</option>
        <option value="holes">Dark holes in paper</option>
      </select></div>
      <div><label for="dTarget">Face</label><select id="dTarget"></select></div>
    </div>
    <div style="height:10px"></div>
    <button class="btn" id="dScan">Scan folder</button>
    <p class="muted" id="dMsg"></p>
  </div>
  <div id="dList"></div>

  <details class="card">
    <summary class="muted">Not working? Use your own Google client</summary>
    <p class="muted">Google only allows sign-in from web addresses registered
      against a client ID. This app reuses the one from Tachyread, which is
      registered for <code>adervec.github.io</code> and a couple of local
      ports. If your address isn't one of them, create an OAuth client ID
      (type: <em>Web application</em>) in the Google Cloud console, add this
      page's address under <em>Authorized JavaScript origins</em>, and paste
      the ID here.</p>
    <p class="muted">This page's address is <code id="dOrigin"></code></p>
    <input type="text" id="dClient" placeholder="....apps.googleusercontent.com">
    <div style="height:8px"></div>
    <button class="ghost" id="dSaveClient" style="width:100%">Save client ID</button>
  </details>
</section>

</main>
</div>
<div id="toast"></div>
<nav>
  <button data-t="home" class="on">Home</button>
  <button data-t="drills">Drills</button>
  <button data-t="log">Log</button>
  <button data-t="sessions">Sessions</button>
  <button data-t="drive">Drive</button>
</nav>
</div>

<script>
"use strict";
const $ = id => document.getElementById(id);
const TIER_COLOR = {Rookie:"#8a949e", Steady:"#6fa8dc", Sharp:"#e8b34b", Marksman:"#7fc97f"};
let S = null, shots = [], lastStats = null, curTarget = null;
// Set when the shots on the canvas came from a Drive photo, so the saved
// session remembers which photo it was and the picker can grey it out.
let driveSource = null;

function el(tag, cls, text){
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}
function fmt(v, d){ return v == null ? "–" : (+v).toFixed(d == null ? 1 : d); }
function toast(msg){
  const t = $("toast"); t.textContent = msg; t.style.display = "block";
  clearTimeout(t._h); t._h = setTimeout(() => t.style.display = "none", 3500);
}
async function api(path, body){
  const opt = body ? {method:"POST", headers:{"Content-Type":"application/json"},
                      body: JSON.stringify(body)} : {};
  const r = await fetch(path, opt);
  let data = {};
  try { data = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));
  return data;
}
function show(tab){
  document.querySelectorAll("section").forEach(s => s.classList.toggle("on", s.id === tab));
  document.querySelectorAll("nav button").forEach(b => b.classList.toggle("on", b.dataset.t === tab));
  history.replaceState(null, "", "#" + tab);
  if (tab === "log") drawTarget();
}
document.querySelectorAll("nav button").forEach(b => b.onclick = () => show(b.dataset.t));

function tierBadge(tier){
  const b = el("span", "badge", tier || "–");
  if (tier) b.style.background = TIER_COLOR[tier];
  return b;
}
function sessLine(box, s, canDelete){
  const row = el("div", "item");
  const left = el("div");
  left.appendChild(el("div", null, s.date + " · " + s.tool + (s.drill ? " · " + s.drill : "")));
  let sub = s.shots + " shots";
  if (s.group_mm != null) sub += " · " + fmt(s.group_mm) + " mm";
  if (s.group_mrad != null) sub += " (" + fmt(s.group_mrad, 2) + " mrad)";
  if (s.score_pct != null) sub += " · " + fmt(s.score_pct, 0) + "%";
  left.appendChild(el("div", "muted", sub));
  row.appendChild(left);
  if (canDelete){
    const btns = el("div", "rowbtns");
    const del = el("button", "x", "×");
    del.title = "delete this session";
    del.setAttribute("aria-label", "Delete the session from " + s.date);
    del.onclick = async () => {
      if (!confirm("Delete the session from " + s.date + "? This cannot be undone.")) return;
      try { await api("/api/session/delete", {id: s.id}); await load(); toast("Deleted"); }
      catch (e) { toast(e.message); }
    };
    btns.appendChild(del);
    row.appendChild(btns);
  }
  box.appendChild(row);
}

function renderHome(){
  const tp = S.tierPoints;
  $("tp").textContent = tp.earned + " / " + tp.possible + " tiers";
  $("tpsub").textContent = tp.drillsAttempted + " of " + tp.drillsTotal + " drills attempted";
  $("points").textContent = tp.earned + "/" + tp.possible;

  const plan = $("plan"); plan.textContent = "";
  S.plan.forEach(d => {
    const card = el("div", "card");
    const top = el("div", "item");
    const left = el("div");
    left.appendChild(el("div", null, d.name));
    left.appendChild(el("div", "muted", d.reason));
    top.appendChild(left);
    top.appendChild(tierBadge(d.tier));
    card.appendChild(top);
    const go = el("button", "btn", "Log it");
    go.onclick = () => { show("log"); applyDrill(d.id); };
    card.appendChild(go);
    plan.appendChild(card);
  });

  const g = $("goals"); g.textContent = "";
  if (!S.goals.length) g.appendChild(el("div", "muted",
    "No goals yet — set one with + goal."));
  S.goals.forEach(x => {
    const row = el("div", "item");
    row.appendChild(el("div", null, x.metric + " " + (x.lowerIsBetter ? "≤ " : "≥ ")
                       + x.target + " " + x.unit + " · " + x.scope));
    const right = el("div", "rowbtns");
    right.appendChild(el("div", x.met ? "ok" : "muted",
                       x.met == null ? "no data" : (x.met ? "met ✓" : "best " + fmt(x.best))));
    const del = el("button", "x", "×");
    del.title = "remove this goal";
    del.setAttribute("aria-label", "Remove the " + x.metric + " goal");
    del.onclick = async () => {
      try { await api("/api/goal", {action: "rm", id: x.id}); await load(); }
      catch (e) { toast(e.message); }
    };
    right.appendChild(del);
    row.appendChild(right);
    g.appendChild(row);
  });

  const rec = $("recent"); rec.textContent = "";
  if (!S.sessions.length) rec.appendChild(el("div", "muted", "No sessions yet."));
  S.sessions.slice(0, 5).forEach(s => sessLine(rec, s));

  const pk = $("packs"); pk.textContent = "";
  const SENS = {1: "Toy", 2: "Sport", 3: "Regulated", 4: "Restricted", 5: "Unclassified"};
  if (!S.packs.length) pk.appendChild(el("div", "muted",
    "None installed — the app has no drills without one."));
  S.packs.forEach(p => {
    const row = el("div", "item");
    const left = el("div");
    left.appendChild(el("div", null, p.name + (p.bundled ? "" : " (yours)")));
    left.appendChild(el("div", "muted",
      p.drills + " drills · " + p.sensitivity + " " + SENS[p.sensitivity]
      + (p.active ? "" : " · " + p.reason)));
    row.appendChild(left);
    row.appendChild(el("div", p.active ? "ok" : "muted", p.active ? "on" : "off"));
    pk.appendChild(row);
  });
  pk.appendChild(el("div", "muted",
    "Manage them from the terminal: marksman pack list"));

  // Safety text belongs to whoever wrote the pack, so show theirs verbatim.
  const sf = $("safety"); sf.textContent = "";
  S.packs.filter(p => p.active && p.safety).forEach(p => {
    const card = el("div", "card");
    card.style.borderColor = "var(--bad)";
    card.appendChild(el("div", null, "⚠️ " + p.name));
    card.appendChild(el("div", "muted", p.safety));
    sf.appendChild(card);
  });
}

function renderDrills(){
  const box = $("drillList"); box.textContent = "";
  const fams = [];
  S.drills.forEach(d => { if (fams.indexOf(d.family) < 0) fams.push(d.family); });
  fams.forEach(family => {
    box.appendChild(el("h2", null, family));
    S.drills.filter(d => d.family === family).forEach(d => {
    const card = el("div", "card");
    const head = el("div", "item");
    const left = el("div");
    left.appendChild(el("div", null, d.name));
    left.appendChild(el("div", "muted",
      d.distance_m + " m · " + d.shotsNeeded + " shots · best " + fmt(d.best)
      + " " + d.unit + (d.nextCutoff != null ? " · next " + (d.lowerIsBetter ? "≤ " : "≥ ") + d.nextCutoff : "")));
    head.appendChild(left);
    head.appendChild(tierBadge(d.tier));
    card.appendChild(head);

    const detail = el("div"); detail.style.display = "none";
    detail.appendChild(el("p", "muted", d.why));
    detail.appendChild(el("h2", null, "How"));
    const how = el("ul"); d.how.forEach(x => how.appendChild(el("li", null, x)));
    detail.appendChild(how);
    detail.appendChild(el("h2", null, "Cues"));
    const cues = el("ul"); d.cues.forEach(x => cues.appendChild(el("li", null, x)));
    detail.appendChild(cues);
    detail.appendChild(el("h2", null, "Standards"));
    const tiers = ["Rookie","Steady","Sharp","Marksman"];
    const earned = d.tier ? tiers.indexOf(d.tier) : -1;
    tiers.forEach((t, i) => {
      const row = el("div", "item");
      row.appendChild(el("div", null, t + "  " + (d.lowerIsBetter ? "≤ " : "≥ ") + d.cutoffs[i] + " " + d.unit));
      row.appendChild(el("div", i <= earned ? "ok" : "muted", i <= earned ? "✓" : ""));
      detail.appendChild(row);
    });
    const go = el("button", "btn", "Log this drill");
    go.onclick = () => { show("log"); applyDrill(d.id); };
    detail.appendChild(go);
    card.appendChild(detail);
    head.style.cursor = "pointer";
    head.onclick = () => { detail.style.display = detail.style.display === "none" ? "" : "none"; };
    box.appendChild(card);
    });
  });
}

// ponytail: charts the sessions the state call already sent (the most recent
// 50). Ask the server for a longer series only if anyone wants more history.
function renderChart(){
  const metric = $("metric").value, who = $("chartTool").value;
  const lower = metric !== "score_pct";
  const rows = S.sessions
    .filter(s => (!who || s.tool === who) && s[metric] != null)
    .slice().reverse();                       // oldest first
  const box = $("chart"); box.textContent = "";
  const sub = $("chartSub");
  if (rows.length < 2){
    sub.textContent = rows.length ? "One session — log another to see a trend."
                                  : "Nothing logged for this yet.";
    return;
  }
  const vals = rows.map(r => r[metric]);
  const lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
  const span = (hi - lo) || Math.abs(hi) || 1;
  const W = 300, H = 110, pad = 6;
  const x = i => pad + i * (W - 2 * pad) / (rows.length - 1);
  const y = v => H - pad - (v - lo + span * 0.08) / (span * 1.16) * (H - 2 * pad);
  const pts = vals.map((v, i) => x(i).toFixed(1) + "," + y(v).toFixed(1)).join(" ");
  const best = lower ? lo : hi;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  svg.setAttribute("style", "width:100%;height:auto;display:block");
  svg.innerHTML =
    "<polyline fill='none' stroke='#e8b34b' stroke-width='2' " +
      "stroke-linejoin='round' points='" + pts + "'/>" +
    "<line x1='" + pad + "' x2='" + (W - pad) + "' y1='" + y(best).toFixed(1) +
      "' y2='" + y(best).toFixed(1) + "' stroke='#7fc97f' stroke-width='1' " +
      "stroke-dasharray='3 3'/>" +
    vals.map((v, i) => "<circle cx='" + x(i).toFixed(1) + "' cy='" +
      y(v).toFixed(1) + "' r='2.5' fill='#e8b34b'/>").join("");
  box.appendChild(svg);
  const first = vals[0], last = vals[vals.length - 1];
  const better = lower ? last < first : last > first;
  sub.textContent = rows.length + " sessions · best " + fmt(best)
    + " · latest " + fmt(last)
    + " · " + (last === first ? "flat" : (better ? "improving" : "slipping"));
}

function renderSessions(){
  const box = $("sessList"); box.textContent = "";
  if (!S.sessions.length) box.appendChild(el("div", "muted", "No sessions yet."));
  S.sessions.forEach(s => sessLine(box, s, true));
  const keep = $("chartTool").value;
  fillSelect($("chartTool"), [{name: "All tools"}].concat(S.tools),
             t => t.name === "All tools" ? "" : t.name, t => t.name);
  if (keep) $("chartTool").value = keep;
  renderChart();
}
$("metric").onchange = renderChart;
$("chartTool").onchange = renderChart;

function fillSelect(sel, items, value, label){
  sel.textContent = "";
  items.forEach(it => {
    const o = document.createElement("option");
    o.value = value(it); o.textContent = label(it);
    sel.appendChild(o);
  });
}
function renderLogControls(){
  fillSelect($("tool"), S.tools, t => t.id, t => t.name + " (" + t.category + ")");
  const opts = [{id:"", name:"— free session —"}].concat(S.drills);
  fillSelect($("drill"), opts, d => d.id, d => d.name);
  fillSelect($("target"), S.targets, t => t.name, t => t.name);
  if (S.defaultTarget) $("target").value = S.defaultTarget;
  const keepDesign = $("design").value;
  fillSelect($("design"), [["", "This target face"]].concat(S.sheets),
             r => r[0], r => r[0] ? r[0] + " — " + r[1].split(";")[0] : r[1]);
  if (keepDesign) $("design").value = keepDesign;
  if (!$("date").value) $("date").value = new Date().toLocaleDateString("en-CA");
  curTarget = S.targets.find(t => t.name === $("target").value) || S.targets[0];
}

function applyDrill(id){
  $("drill").value = id || "";
  const d = S.drills.find(x => x.id === id);
  if (d){
    $("target").value = d.target;
    $("dist").value = d.distance_m;
    $("dhint").textContent = d.shotsNeeded + " shots · judged on " + d.metric
      + (d.nextTier ? " · " + d.nextTier + " at " + (d.lowerIsBetter ? "≤ " : "≥ ") + d.nextCutoff + " " + d.unit
                    : " · topped out – maintenance");
  } else {
    $("dhint").textContent = "";
  }
  curTarget = S.targets.find(t => t.name === $("target").value) || curTarget;
  refreshShots();
}

function drawTarget(){
  const c = $("cv");
  const size = c.clientWidth || 320;
  const dpr = window.devicePixelRatio || 1;
  c.width = c.height = Math.round(size * dpr);
  c.style.height = size + "px";
  const ctx = c.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.fillStyle = "#232b36";
  ctx.fillRect(0, 0, size, size);
  if (!curTarget) return;
  const k = size / curTarget.face_mm, cx = size / 2, cy = size / 2;
  const rings = curTarget.rings;
  for (let i = rings.length - 1; i >= 0; i--){
    ctx.beginPath();
    ctx.arc(cx, cy, rings[i] / 2 * k, 0, 7);
    ctx.fillStyle = i < 2 ? "#12181f" : (i % 2 ? "#2b3542" : "#242d38");
    ctx.fill();
    ctx.strokeStyle = "#3d4a59";
    ctx.stroke();
  }
  ctx.strokeStyle = "#5b6771";
  ctx.beginPath(); ctx.moveTo(cx - 7, cy); ctx.lineTo(cx + 7, cy);
  ctx.moveTo(cx, cy - 7); ctx.lineTo(cx, cy + 7); ctx.stroke();

  if (lastStats && lastStats.n > 1){
    const gx = cx + lastStats.cx * k, gy = cy - lastStats.cy * k;
    ctx.strokeStyle = "#2fc4b2";
    ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.arc(gx, gy, lastStats.mean_radius_mm * k, 0, 7); ctx.stroke();
    ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo(gx - 6, gy); ctx.lineTo(gx + 6, gy);
    ctx.moveTo(gx, gy - 6); ctx.lineTo(gx, gy + 6); ctx.stroke();
  }
  const r = Math.max(5, 3 * k);
  shots.forEach((s, i) => {
    const x = cx + s.x_mm * k, y = cy - s.y_mm * k;
    // A dart end-on: foam collar, orange tip, numbered.
    ctx.beginPath(); ctx.arc(x, y, r, 0, 7);
    ctx.fillStyle = "#f5ebda"; ctx.fill();
    ctx.strokeStyle = "#141821"; ctx.lineWidth = 1.5; ctx.stroke();
    ctx.beginPath(); ctx.arc(x, y, r * 0.62, 0, 7);
    ctx.fillStyle = "#ff7a29"; ctx.fill();
    ctx.lineWidth = 1;
    ctx.fillStyle = "#1a1206";
    ctx.font = "bold " + Math.max(9, r) + "px system-ui";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(String(i + 1), x, y);
  });
}

async function refreshShots(){
  drawTarget();
  const line = $("stats");
  if (!shots.length){ lastStats = null; line.textContent = "Tap the target to place shots."; return; }
  try {
    lastStats = await api("/api/stats", {shots: shots, target: $("target").value,
                                         distance_m: $("dist").value || null,
                                         tool_id: $("tool").value || ""});
    let t = lastStats.n + (lastStats.n === 1 ? " shot" : " shots")
            + " · group " + fmt(lastStats.group_mm) + " mm";
    if (lastStats.group_mrad != null) t += " (" + fmt(lastStats.group_mrad, 2) + " mrad)";
    t += " · mean r " + fmt(lastStats.mean_radius_mm) + " · zero off " + fmt(lastStats.zero_mm) + " mm";
    if (lastStats.score_pct != null) t += " · " + fmt(lastStats.score_pct, 0) + "%";
    if (lastStats.correction) t += "\n" + lastStats.correction;
    line.textContent = t;
    drawTarget();
  } catch (e) {
    line.textContent = e.message;
  }
}

$("cv").addEventListener("pointerdown", e => {
  if (!curTarget) return;
  e.preventDefault();
  // offsetX/Y are in the element's own coordinate space, so they stay
  // correct when the whole app is CSS-rotated by the orientation setting.
  const el = e.currentTarget;
  const k = curTarget.face_mm / el.clientWidth;
  const x = (e.offsetX - el.clientWidth / 2) * k;
  const y = (el.clientHeight / 2 - e.offsetY) * k;
  shots.push({x_mm: +x.toFixed(1), y_mm: +y.toFixed(1)});
  refreshShots();
});
function updatePrintLink(){
  const d = $("design").value;   // "" = the face being shot at
  const q = (d ? "design=" + encodeURIComponent(d)
               : "face=" + encodeURIComponent($("target").value)
                 + ($("dist").value ? "&distance=" + encodeURIComponent($("dist").value) : ""))
          + "&paper=" + encodeURIComponent($("paper").value);
  $("printFace").href = "/target.html?" + q;
}
$("paper").onchange = updatePrintLink;
$("design").onchange = updatePrintLink;

$("addGoal").onclick = async () => {
  const metric = prompt("Goal metric — one of: " + S.goalMetrics.join(", "), "group_size");
  if (!metric) return;
  const target = prompt("Target value for " + metric + " (lower is better for "
                        + "everything except score):");
  if (!target) return;
  try {
    await api("/api/goal", {action: "add", metric: metric.trim(),
                            target: parseFloat(target),
                            tool_id: $("tool").value || ""});
    await load();
    toast("Goal added");
  } catch (e) { toast(e.message); }
};

// Typed entry: the keyboard route onto the target, and exact coordinates
// when you already have them (from a scan, or measured off the paper).
$("addShot").onclick = () => {
  const x = parseFloat($("sx").value), y = parseFloat($("sy").value);
  if (!isFinite(x) || !isFinite(y)){ toast("Enter both x and y in mm."); return; }
  shots.push({x_mm: +x.toFixed(1), y_mm: +y.toFixed(1)});
  $("sx").value = ""; $("sy").value = ""; $("sx").focus();
  refreshShots();
};
$("undo").onclick = () => { shots.pop(); refreshShots(); };
$("clear").onclick = () => { shots = []; refreshShots(); };
$("drill").onchange = () => applyDrill($("drill").value);
$("target").onchange = () => {
  curTarget = S.targets.find(t => t.name === $("target").value);
  updatePrintLink();
  refreshShots();
};
$("dist").onchange = () => { updatePrintLink(); refreshShots(); };

// Orientation: a saved setting decides the layout; the accelerometer doesn't.
// "auto" follows the device; "portrait"/"landscape" hold that layout and
// counter-rotate the app when the device is turned the other way.
const ORIENTS = ["auto", "portrait", "landscape"];
let orient = localStorage.getItem("mk_orient") || "auto";
if (ORIENTS.indexOf(orient) < 0) orient = "auto";
function applyOrient(){
  const deviceLandscape = window.innerWidth > window.innerHeight;
  document.body.classList.remove("rotcw", "rotccw");
  if (orient === "landscape" && !deviceLandscape) document.body.classList.add("rotcw");
  else if (orient === "portrait" && deviceLandscape) document.body.classList.add("rotccw");
  $("orient").textContent = orient;
  drawTarget();
}
$("orient").onclick = () => {
  orient = ORIENTS[(ORIENTS.indexOf(orient) + 1) % ORIENTS.length];
  localStorage.setItem("mk_orient", orient);
  applyOrient();
  toast("Orientation: " + orient);
};
window.addEventListener("resize", applyOrient);
applyOrient();

$("addTool").onclick = async () => {
  const name = prompt("Name for this " + S.terms.tool + ":");
  if (!name) return;
  const category = prompt("Category (" + S.categories.join(", ") + "):",
                          S.categories[0] || "Other") || "Other";
  try {
    const t = await api("/api/tool", {name: name, category: category});
    await load();
    $("tool").value = t.id;
    toast("Added " + t.name);
  } catch (e) { toast(e.message); }
};

$("save").onclick = async () => {
  const btn = $("save");
  if (!$("tool").value) { toast("Add a tool first (the + button)."); return; }
  if (!shots.length) { toast("Tap your shots onto the target first."); return; }
  btn.disabled = true;
  try {
    const r = await api("/api/session", {
      tool_id: $("tool").value, drill_id: $("drill").value,
      target: $("target").value, distance_m: $("dist").value || null,
      date: $("date").value, notes: $("notes").value, shots: shots,
      source_ref: driveSource ? "drive:" + driveSource.id : "",
    });
    if (r.drill) {
      toast(r.drill + ": " + fmt(r.value) + " " + r.unit + " — "
            + (r.attemptTier || "below Rookie") + (r.tier ? " (standing: " + r.tier + ")" : ""));
    } else {
      toast("Saved · group " + fmt(r.group_mm) + " mm");
    }
    shots = []; lastStats = null; driveSource = null; $("notes").value = "";
    await load();
    refreshShots();
  } catch (e) {
    toast(e.message);
  } finally {
    btn.disabled = false;
  }
};

async function load(){
  const keepTool = $("tool").value, keepDrill = $("drill").value, keepTarget = $("target").value;
  S = await api("/api/state");
  renderHome(); renderDrills(); renderSessions(); renderLogControls();
  fillSelect($("dTarget"), S.targets, t => t.name, t => t.name);
  renderDriveList();
  if (keepTool) $("tool").value = keepTool;
  if (keepTarget) { $("target").value = keepTarget; curTarget = S.targets.find(t => t.name === keepTarget) || curTarget; }
  if (keepDrill) $("drill").value = keepDrill;
  updatePrintLink();
}

/* ---------------- Google Drive ----------------
   Mirrors Tachyread's sync provider: a public client id (an identifier, not a
   secret) that only works from the JavaScript origins registered with Google,
   plus an app-side origin gate so anyone hosting this elsewhere must supply
   their own. Tokens live in memory only and are never written down. */
const BUILTIN_CLIENT_ID = "547617739897-br6dj2facmsc34qnkjb5u4dbfhju39pu.apps.googleusercontent.com";
const OAUTH_ORIGINS = ["https://adervec.github.io"];
const originAllowed = () =>
  ["localhost", "127.0.0.1", "[::1]"].indexOf(location.hostname) >= 0 ||
  OAUTH_ORIGINS.indexOf(location.origin) >= 0;

// The app-data folder is per OAuth client, so reusing Tachyread's id means
// sharing that space with its files. Keep this name distinct from theirs.
const SYNC_FILE = "marksman-sessions.json";
// Two grants, one sign-in: syncing needs only our own private folder; reading
// a folder of your photos is a wider ask, so it's only requested when you scan.
const SCOPE_SYNC = "openid email profile https://www.googleapis.com/auth/drive.appdata";
const SCOPE_SCAN = SCOPE_SYNC + " https://www.googleapis.com/auth/drive.readonly";

const DRIVE = Object.assign({clientId: "", folder: "", linked: false, syncedAt: 0},
  JSON.parse(localStorage.getItem("mk-drive") || "{}"));
const saveDrive = () => localStorage.setItem("mk-drive", JSON.stringify(DRIVE));
const clientId = () => DRIVE.clientId.trim() || (originAllowed() ? BUILTIN_CLIENT_ID : "");
// Takes a pasted folder link or a bare id — the id is the long token in the URL.
const folderId = s => (String(s || "").match(/[-\w]{20,}/) || [""])[0];

let tok = null, acct = null, dFiles = [], dBuf = null;
const tokenCovers = need => !!tok && tok.exp > Date.now() + 60000 &&
  need.split(" ").every(s => tok.scope.indexOf(s) >= 0);

let gis = null;
function loadGis(){
  if (gis) return gis;
  gis = new Promise((ok, no) => {
    if (window.google && window.google.accounts && window.google.accounts.oauth2) return ok();
    const s = document.createElement("script");
    s.src = "https://accounts.google.com/gsi/client";
    s.async = true;
    s.onload = () => ok();
    s.onerror = () => no(new Error("Couldn't load Google sign-in (no internet?)."));
    document.head.appendChild(s);
  });
  return gis;
}

function requestToken(scope, prompt){
  return new Promise((ok, no) => {
    const client = google.accounts.oauth2.initTokenClient({
      client_id: clientId(), scope: scope,
      callback: r => {
        if (r && r.access_token) {
          tok = {value: r.access_token, scope: r.scope || scope,
                 exp: Date.now() + ((r.expires_in || 3600) - 60) * 1000};
          ok(tok.value);
        } else no(new Error((r && r.error) || "Sign-in failed."));
      },
      // Without this a dismissed popup would leave the promise hanging forever.
      error_callback: e => no(new Error(
        e && e.type === "popup_closed" ? "Sign-in was dismissed." : "Sign-in failed.")),
    });
    client.requestAccessToken({prompt: prompt || ""});
  });
}

async function auth(scope, opt){
  const silent = opt && opt.silent;
  if (!clientId())
    throw new Error("Google sign-in isn't set up for this address — see " +
                    "“Use your own Google client” below.");
  if (tokenCovers(scope)) return tok.value;
  await loadGis();
  try {
    return await requestToken(scope, "");       // silent, if already granted
  } catch (e) {
    if (silent) throw e;                        // boot/auto: never ambush
    return await requestToken(scope, "consent");
  }
}

async function gdrive(path, params, scope){
  const t = await auth(scope || SCOPE_SYNC);
  const r = await fetch("https://www.googleapis.com/drive/v3/" + path + "?" +
                        new URLSearchParams(params),
                        {headers: {Authorization: "Bearer " + t}});
  if (r.status === 401 || r.status === 403) {
    tok = null;
    throw new Error("Drive refused access. Check the folder is in the account " +
                    "you signed in with, then try again.");
  }
  if (r.status === 429) throw new Error("Google is rate-limiting; wait a moment.");
  if (!r.ok) throw new Error("Drive returned error " + r.status + ".");
  return r;
}

async function fetchAcct(){
  try {
    const r = await fetch("https://www.googleapis.com/oauth2/v3/userinfo",
                          {headers: {Authorization: "Bearer " + tok.value}});
    if (r.ok) { const j = await r.json(); acct = j.email || j.name || ""; }
  } catch (e) { /* cosmetic only */ }
}

async function syncFind(){
  const r = await gdrive("files", {
    spaces: "appDataFolder", q: "name='" + SYNC_FILE + "' and trashed=false",
    fields: "files(id,name,modifiedTime)",
  });
  const j = await r.json();
  return (j.files && j.files[0]) || null;
}

async function syncUpload(id, bundle){
  const meta = id ? {} : {name: SYNC_FILE, parents: ["appDataFolder"]};
  const form = new FormData();
  form.append("metadata", new Blob([JSON.stringify(meta)], {type: "application/json"}));
  form.append("file", new Blob([JSON.stringify(bundle)], {type: "application/json"}));
  const url = "https://www.googleapis.com/upload/drive/v3/files" +
              (id ? "/" + id : "") + "?uploadType=multipart";
  const r = await fetch(url, {method: id ? "PATCH" : "POST",
    headers: {Authorization: "Bearer " + tok.value}, body: form});
  if (!r.ok) throw new Error("Drive upload failed (" + r.status + ").");
}

async function driveSync(opt){
  await auth(SCOPE_SYNC, opt);
  if (!acct) await fetchAcct();
  const found = await syncFind();
  let remote = null;
  if (found) {
    const r = await gdrive("files/" + found.id, {alt: "media"});
    try { remote = await r.json(); }
    catch (e) { throw new Error("The sync file is corrupt — back up from a device that has your data."); }
  }
  // Python owns the merge: it holds the database and knows the record shapes.
  const res = await api("/api/sync", {remote: remote});
  await syncUpload(found && found.id, res.bundle);
  DRIVE.linked = true; DRIVE.syncedAt = Date.now(); saveDrive();
  await load();
  return res;
}

function driveUi(msg){
  const on = DRIVE.linked;
  $("dConnect").classList.toggle("hide", on);
  $("dSync").classList.toggle("hide", !on);
  $("dOut").classList.toggle("hide", !on);
  $("dAcct").textContent = on && acct ? "Signed in as " + acct : "";
  $("dSyncMsg").textContent = typeof msg === "string" ? msg
    : DRIVE.syncedAt ? "Last synced " + new Date(DRIVE.syncedAt).toLocaleString() : "";
}

async function runSync(label){
  $("dSyncMsg").textContent = label;
  try {
    const r = await driveSync();
    driveUi("Synced — " + r.sessions + " sessions" +
            (r.addedSessions ? ", " + r.addedSessions + " pulled in" : "") + ".");
  } catch (e) { driveUi(e.message); }
}

$("dConnect").onclick = () => runSync("Connecting…");
$("dSync").onclick = () => runSync("Syncing…");
$("dOut").onclick = () => {
  tok = null; acct = null; DRIVE.linked = false; saveDrive();
  driveUi("Signed out on this device. Your sessions stay here.");
};
$("dSaveClient").onclick = () => {
  DRIVE.clientId = $("dClient").value.trim(); saveDrive(); tok = null;
  $("dMsg").textContent = DRIVE.clientId ? "Client ID saved." : "Using the built-in client ID.";
};

function renderDriveList(){
  const box = $("dList"); box.textContent = "";
  const done = new Set(S ? S.imported : []);
  dFiles.forEach((f, i) => {
    const b = el("button", "filerow" + (done.has(f.id) ? " done" : ""));
    b.appendChild(el("span", null, f.name));
    b.appendChild(el("span", "muted",
      new Date(f.createdTime).toLocaleDateString() + (done.has(f.id) ? " · logged ✓" : "")));
    b.onclick = () => driveOpen(f);
    box.appendChild(b);
  });
}

$("dScan").onclick = async () => {
  const id = folderId($("dFolder").value);
  if (!id) { $("dMsg").textContent = "Paste the folder's link from Drive first."; return; }
  DRIVE.folder = $("dFolder").value.trim(); saveDrive();
  $("dMsg").textContent = "Reading folder…";
  try {
    await auth(SCOPE_SCAN);
    if (tok.scope.indexOf("drive.readonly") < 0)
      throw new Error("Google didn't grant folder access. Allow the Drive " +
                      "permission when signing in, or use your own client ID below.");
    const r = await gdrive("files", {
      q: "'" + id + "' in parents and trashed=false and mimeType contains 'image/'",
      fields: "files(id,name,mimeType,createdTime)",
      orderBy: "createdTime desc", pageSize: "100",
    }, SCOPE_SCAN);
    const j = await r.json();
    dFiles = j.files || [];
    renderDriveList();
    $("dMsg").textContent = dFiles.length
      ? dFiles.length + " photo" + (dFiles.length > 1 ? "s" : "") + " — tap one to read it"
      : "That folder has no photos in it.";
  } catch (e) { $("dMsg").textContent = e.message; }
};

async function driveOpen(f){
  $("dMsg").textContent = "Reading " + f.name + "…";
  try {
    if (!dBuf || dBuf.id !== f.id) {
      const r = await gdrive("files/" + f.id, {alt: "media"}, SCOPE_SCAN);
      dBuf = {id: f.id, buf: await r.arrayBuffer()};
    }
    let bin = "";
    const bytes = new Uint8Array(dBuf.buf);
    for (let i = 0; i < bytes.length; i += 8192)
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 8192));
    const face = S.targets.find(t => t.name === $("dTarget").value);
    const res = await api("/api/analyze-image", {
      image_b64: btoa(bin), target: $("dTarget").value,
      mode: $("dMode").value, color: "red",
    });
    if (!res.shots.length) {
      $("dMsg").textContent = "No hits found. Try the other marking style, or " +
        "place them by hand on the Log tab.";
      return;
    }
    // Straight into the normal log flow: the hits land on the target face and
    // you correct them before saving, exactly like a hand-tapped session.
    show("log");
    $("target").value = $("dTarget").value;
    curTarget = face || curTarget;
    driveSource = {id: f.id, name: f.name};
    const d = new Date(f.createdTime);
    if (!isNaN(d)) $("date").value = d.toLocaleDateString("en-CA");
    shots = res.shots;
    await refreshShots();
    toast("Found " + res.shots.length + " hits in " + f.name + " — check them, then save.");
  } catch (e) { $("dMsg").textContent = e.message; }
}

function showFromHash(){
  const t = location.hash.slice(1);
  if (["home", "drills", "log", "sessions", "drive"].indexOf(t) >= 0) show(t);
}
window.addEventListener("hashchange", showFromHash);
showFromHash();

$("dOrigin").textContent = location.origin;
$("dFolder").value = DRIVE.folder;
$("dClient").value = DRIVE.clientId;
driveUi();
// Reconnect quietly if this device was linked before — never pops a sign-in.
if (DRIVE.linked) driveSync({silent: true}).then(() => driveUi(),
                                                 () => driveUi("Sign in again to resume syncing."));
load().then(() => { drawTarget(); }).catch(e => toast("Load failed: " + e.message));
</script>
</body>
</html>
"""
