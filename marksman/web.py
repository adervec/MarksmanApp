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

import hmac
import json
import math
import secrets
import socket
import threading
import uuid
from datetime import date, datetime
from http import cookies as http_cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from . import drills as drills_mod
from . import goals as goals_mod
from . import targets as targets_mod
from . import tracker
from .goals import METRICS
from .grouping import analyze_group
from .models import Session, Shot, Tool, TargetSpec, STANDARD_CATEGORIES
from .storage import Database, DEFAULT_DB_PATH

MAX_BODY = 1_000_000        # bytes; a session is a few KB
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
    return {
        "tools": [{"id": w.id, "name": w.name, "category": w.category}
                  for w in db.tools.values()],
        "targets": target_rows,
        "drills": drill_rows,
        "plan": drills_mod.plan(db, 3),
        "tierPoints": drills_mod.tier_points(db),
        "goals": goals_mod.summary(db),
        "sessions": [_sess_row(db, s) for s in sessions[:50]],
        "categories": list(STANDARD_CATEGORIES),
    }


def _stats(db: Database, body: Dict[str, Any]) -> Dict[str, Any]:
    shots = _shots(body)
    if not shots:
        return {"n": 0}
    tname = str(body.get("target") or "")
    spec = _target(db, tname) if tname else None
    st = analyze_group(shots, target=spec)
    return {
        "n": st.shot_count,
        "group_mm": st.extreme_spread_mm,
        "group_mrad": tracker.mm_to_mrad(st.extreme_spread_mm,
                                         _num(body.get("distance_m"), "distance_m")),
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
        stats = analyze_group(shots, target=spec, bb_mm=tool.bb_mm or 0.0)
        s = Session(
            id=uuid.uuid4().hex[:8], tool_id=tool.id, date=day, shots=shots,
            stats=stats, distance_m=_num(body.get("distance_m"), "distance_m"),
            target_name=spec.name if spec else "", drill_id=drill_id,
            bbs=str(body.get("bbs") or "")[:100],
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
        path = urlparse(self.path).path
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
        else:
            self._send(404, {"error": "not found"})

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
    httpd.token = token or secrets.token_urlsafe(8)
    httpd.lock = threading.Lock()
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
    print("The link carries a one-run access code; anyone on your network with")
    print("the full link can view and add sessions. Don't expose it to the internet.")
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
<meta name="theme-color" content="#12161a">
<title>Marksman</title>
<style>
:root{--bg:#12161a;--card:#1b2127;--line:#2a323b;--fg:#e8e6e1;--mut:#8a949e;
      --acc:#e8b34b;--good:#7fc97f;--bad:#e06c60;--field:#12171c}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--fg);padding-bottom:70px;
     font:16px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{position:sticky;top:0;z-index:2;background:var(--bg);padding:12px 16px;
       border-bottom:1px solid var(--line);display:flex;justify-content:space-between;
       align-items:baseline}
header b{color:var(--acc);letter-spacing:.04em}
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
.btn{width:100%;background:var(--acc);color:#14181c;font-weight:700}
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
  background:var(--acc);color:#14181c;font-weight:700;border-radius:8px;
  padding:10px 16px;display:none;max-width:90%}
ul{padding-left:20px;font-size:14px}li{margin:3px 0}
a{color:var(--acc)}
</style>
</head>
<body>
<div id="toast"></div>
<header><b>MARKSMAN</b><span id="points" class="muted"></span></header>
<main>

<section id="home" class="on">
  <div class="card"><span id="tp" class="big"></span><div id="tpsub" class="muted"></div></div>
  <h2>Today's plan</h2><div id="plan"></div>
  <div id="goalsWrap"><h2>Goals</h2><div id="goals" class="card"></div></div>
  <h2>Recent</h2><div id="recent" class="card"></div>
</section>

<section id="drills"><div id="drillList"></div></section>

<section id="log">
  <div class="card">
    <div class="row"><div>
      <label>Tool</label><select id="tool"></select></div>
      <button class="ghost small" id="addTool" title="add a tool">+</button>
    </div>
    <label>Drill (optional)</label><select id="drill"></select>
    <div id="dhint" class="muted"></div>
    <div class="row">
      <div><label>Target face</label><select id="target"></select></div>
      <div><label>Distance m</label><input id="dist" type="number" inputmode="decimal" min="1" max="1000"></div>
    </div>
    <label>Date</label><input id="date" type="date">
  </div>
  <div class="card">
    <canvas id="cv" height="420"></canvas>
    <p id="stats" class="muted" style="text-align:center;margin:8px 0">Tap the target to place shots.</p>
    <div class="row">
      <button class="ghost" id="undo">Undo</button>
      <button class="ghost" id="clear">Clear</button>
    </div>
  </div>
  <div class="card">
    <label>Notes</label><textarea id="notes" rows="2"></textarea>
    <div style="height:10px"></div>
    <button class="btn" id="save">Save session</button>
  </div>
</section>

<section id="sessions"><div id="sessList" class="card"></div></section>

</main>
<nav>
  <button data-t="home" class="on">Home</button>
  <button data-t="drills">Drills</button>
  <button data-t="log">Log</button>
  <button data-t="sessions">Sessions</button>
</nav>

<script>
"use strict";
const $ = id => document.getElementById(id);
const TIER_COLOR = {Rookie:"#8a949e", Steady:"#6fa8dc", Sharp:"#e8b34b", Marksman:"#7fc97f"};
let S = null, shots = [], lastStats = null, curTarget = null;

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
function sessLine(box, s){
  const row = el("div", "item");
  const left = el("div");
  left.appendChild(el("div", null, s.date + " · " + s.tool + (s.drill ? " · " + s.drill : "")));
  let sub = s.shots + " shots";
  if (s.group_mm != null) sub += " · " + fmt(s.group_mm) + " mm";
  if (s.group_mrad != null) sub += " (" + fmt(s.group_mrad, 2) + " mrad)";
  if (s.score_pct != null) sub += " · " + fmt(s.score_pct, 0) + "%";
  left.appendChild(el("div", "muted", sub));
  row.appendChild(left);
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

  $("goalsWrap").style.display = S.goals.length ? "" : "none";
  const g = $("goals"); g.textContent = "";
  S.goals.forEach(x => {
    const row = el("div", "item");
    row.appendChild(el("div", null, x.metric + " " + (x.lowerIsBetter ? "≤ " : "≥ ")
                       + x.target + " " + x.unit + " · " + x.scope));
    row.appendChild(el("div", x.met ? "ok" : "muted",
                       x.met == null ? "no data" : (x.met ? "met ✓" : "best " + fmt(x.best))));
    g.appendChild(row);
  });

  const rec = $("recent"); rec.textContent = "";
  if (!S.sessions.length) rec.appendChild(el("div", "muted", "No sessions yet."));
  S.sessions.slice(0, 5).forEach(s => sessLine(rec, s));
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

function renderSessions(){
  const box = $("sessList"); box.textContent = "";
  if (!S.sessions.length) box.appendChild(el("div", "muted", "No sessions yet."));
  S.sessions.forEach(s => sessLine(box, s));
}

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
  ctx.fillStyle = "#20272e";
  ctx.fillRect(0, 0, size, size);
  if (!curTarget) return;
  const k = size / curTarget.face_mm, cx = size / 2, cy = size / 2;
  const rings = curTarget.rings;
  for (let i = rings.length - 1; i >= 0; i--){
    ctx.beginPath();
    ctx.arc(cx, cy, rings[i] / 2 * k, 0, 7);
    ctx.fillStyle = i < 2 ? "#0e1114" : (i % 2 ? "#232b33" : "#1d242b");
    ctx.fill();
    ctx.strokeStyle = "#39434d";
    ctx.stroke();
  }
  ctx.strokeStyle = "#5b6771";
  ctx.beginPath(); ctx.moveTo(cx - 7, cy); ctx.lineTo(cx + 7, cy);
  ctx.moveTo(cx, cy - 7); ctx.lineTo(cx, cy + 7); ctx.stroke();

  if (lastStats && lastStats.n > 1){
    const gx = cx + lastStats.cx * k, gy = cy - lastStats.cy * k;
    ctx.strokeStyle = "#e8b34b";
    ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.arc(gx, gy, lastStats.mean_radius_mm * k, 0, 7); ctx.stroke();
    ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo(gx - 6, gy); ctx.lineTo(gx + 6, gy);
    ctx.moveTo(gx, gy - 6); ctx.lineTo(gx, gy + 6); ctx.stroke();
  }
  const r = Math.max(4, 3 * k);
  shots.forEach((s, i) => {
    const x = cx + s.x_mm * k, y = cy - s.y_mm * k;
    ctx.beginPath(); ctx.arc(x, y, r, 0, 7);
    ctx.fillStyle = "#e06c60"; ctx.fill();
    ctx.strokeStyle = "#12161a"; ctx.stroke();
    ctx.fillStyle = "#fff";
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
                                         distance_m: $("dist").value || null});
    let t = lastStats.n + " shots · group " + fmt(lastStats.group_mm) + " mm";
    if (lastStats.group_mrad != null) t += " (" + fmt(lastStats.group_mrad, 2) + " mrad)";
    t += " · mean r " + fmt(lastStats.mean_radius_mm) + " · zero off " + fmt(lastStats.zero_mm) + " mm";
    if (lastStats.score_pct != null) t += " · " + fmt(lastStats.score_pct, 0) + "%";
    line.textContent = t;
    drawTarget();
  } catch (e) {
    line.textContent = e.message;
  }
}

$("cv").addEventListener("pointerdown", e => {
  if (!curTarget) return;
  e.preventDefault();
  const r = e.currentTarget.getBoundingClientRect();
  const k = curTarget.face_mm / r.width;
  const x = (e.clientX - r.left - r.width / 2) * k;
  const y = (r.height / 2 - (e.clientY - r.top)) * k;
  shots.push({x_mm: +x.toFixed(1), y_mm: +y.toFixed(1)});
  refreshShots();
});
$("undo").onclick = () => { shots.pop(); refreshShots(); };
$("clear").onclick = () => { shots = []; refreshShots(); };
$("drill").onchange = () => applyDrill($("drill").value);
$("target").onchange = () => {
  curTarget = S.targets.find(t => t.name === $("target").value);
  refreshShots();
};
$("dist").onchange = refreshShots;
window.addEventListener("resize", drawTarget);

$("addTool").onclick = async () => {
  const name = prompt("Tool name (e.g. 'Training AEG'):");
  if (!name) return;
  const category = prompt("Category (" + S.categories.join(", ") + "):", "AEG") || "Other";
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
    });
    if (r.drill) {
      toast(r.drill + ": " + fmt(r.value) + " " + r.unit + " — "
            + (r.attemptTier || "below Rookie") + (r.tier ? " (standing: " + r.tier + ")" : ""));
    } else {
      toast("Saved · group " + fmt(r.group_mm) + " mm");
    }
    shots = []; lastStats = null; $("notes").value = "";
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
  if (keepTool) $("tool").value = keepTool;
  if (keepTarget) { $("target").value = keepTarget; curTarget = S.targets.find(t => t.name === keepTarget) || curTarget; }
  if (keepDrill) $("drill").value = keepDrill;
}

function showFromHash(){
  const t = location.hash.slice(1);
  if (["home", "drills", "log", "sessions"].indexOf(t) >= 0) show(t);
}
window.addEventListener("hashchange", showFromHash);
showFromHash();
load().then(() => { drawTarget(); }).catch(e => toast("Load failed: " + e.message));
</script>
</body>
</html>
"""
