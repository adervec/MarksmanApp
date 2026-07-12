"""Command-line interface for Marksman.

Examples
--------
    # Register a tool (an airsoft replica)
    marksman tool add --id aeg1 --name "Training AEG" \
        --category "AEG" --bb 6mm --bb-mm 6.0

    # Analyse a marked-up target image (red marker dots), score it, save it
    marksman analyze --tool aeg1 --target "Airsoft Practice 10m" --distance 10 \
        --image shots.png --color red --auto-center

    # Or enter shot coordinates by hand (millimetres from point of aim)
    marksman analyze --tool aeg1 --target "Airsoft Practice 10m" --distance 10 \
        --shots "1.2,3.4  -2.0,5.1  0.5,-1.0"

    # Track progress
    marksman progress                 # overall
    marksman progress --by-category
    marksman progress --by-tool
    marksman progress --tool ap1 --sessions
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from typing import List, Optional, Tuple

from .models import Shot, Tool, Session, TargetSpec
from .grouping import analyze_group
from . import targets as targets_mod
from .storage import Database, DEFAULT_DB_PATH
from . import tracker
from . import report
from . import render as render_mod
from . import theme as theme_mod
from . import coach as coach_mod
from . import exporter
from . import logo as logo_mod
from . import goals as goals_mod
from .theme import Painter


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #

def parse_shots(text: str) -> List[Shot]:
    """Parse "x,y x,y; x,y" (millimetres) into shots. Separators: space/;/newline."""
    out = []  # type: List[Shot]
    chunks = text.replace(";", " ").replace("\n", " ").split()
    for chunk in chunks:
        if "," not in chunk:
            raise ValueError("Bad shot %r; expected 'x,y'." % chunk)
        xs, ys = chunk.split(",", 1)
        out.append(Shot(x_mm=float(xs), y_mm=float(ys)))
    return out


def _parse_xy(text: str) -> Tuple[float, float]:
    a, b = text.split(",", 1)
    return float(a), float(b)


def _parse_rgb(text: str) -> Tuple[int, int, int]:
    parts = text.split(",")
    if len(parts) != 3:
        raise ValueError("--rgb expects 'r,g,b'")
    r, g, b = (int(p) for p in parts)
    return (r, g, b)


def resolve_target(name: str, db: Database) -> TargetSpec:
    """Look up a target by name in custom (db) then built-ins."""
    key = name.strip().lower()
    if key in db.custom_targets:
        return db.custom_targets[key]
    return targets_mod.get_target(name)


def _new_session_id(date: str) -> str:
    return "%s-%s" % (date, uuid.uuid4().hex[:6])


def _make_painter(args: argparse.Namespace, db: Database,
                  force: bool = False) -> Painter:
    """Build the active skin's painter for this invocation.

    Resolution order for the skin: ``--theme`` flag, then the persisted
    preference, then the plain ``mono`` default.  Colour is emitted only when
    the destination supports it (a TTY, ``NO_COLOR`` unset, not ``--no-color``);
    ``force`` is used by ``theme`` preview/list so swatches still render.
    """
    key = getattr(args, "theme", None) or db.settings.get("theme")
    th = theme_mod.get_theme(key)
    enabled = theme_mod.color_enabled(
        sys.stdout, no_color=getattr(args, "no_color", False), force=force)
    return Painter(th, enabled)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_tool_add(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    wid = args.id or uuid.uuid4().hex[:8]
    tool = Tool(
        id=wid,
        name=args.name,
        category=args.category,
        bb=args.bb or "",
        is_gas=args.gas,
        bb_mm=args.bb_mm,
        notes=args.notes or "",
    )
    db.add_tool(tool)
    db.save()
    print("Added tool %s: %s [%s]" % (tool.id, tool.name, tool.category))
    return 0


def cmd_tool_list(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)
    if not db.tools:
        print("No tools yet. Add one with: marksman tool add --name ...")
        return 0
    print(p.title("%-12s %-28s %-18s %-10s SESSIONS"
                  % ("ID", "NAME", "CATEGORY", "BB")))
    for w in sorted(db.tools.values(), key=lambda x: x.name.lower()):
        n = len(db.sessions_for_tool(w.id))
        print(p.value("%-12s" % w.id) + " " + p.label("%-28s" % w.name)
              + " " + p.muted("%-18s %-10s" % (w.category, w.bb))
              + " " + p.value("%d" % n))
    return 0


def cmd_targets(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)
    names = set(targets_mod.list_targets()) | set(t.name for t in db.custom_targets.values())
    print(p.title("Available targets:"))
    for name in sorted(names):
        try:
            spec = resolve_target(name, db)
        except KeyError:
            continue
        deci = " (decimal)" if spec.decimal_scoring else ""
        print("  " + p.label("%s%s" % (spec.name, deci)) + p.muted(": ")
              + p.muted("10-ring ") + p.value("%.1f" % (spec.ten_ring_radius_mm * 2))
              + p.muted(" mm, outer ") + p.value("%.0f" % (spec.outer_radius_mm * 2))
              + p.muted(" mm, max ") + p.value("%d" % spec.max_value))
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)

    tool = db.find_tool(args.tool)
    if tool is None:
        print("error: no tool matching %r. List with 'marksman tool list'."
              % args.tool, file=sys.stderr)
        return 2

    target = None  # type: Optional[TargetSpec]
    if args.target:
        try:
            target = resolve_target(args.target, db)
        except KeyError as e:
            print("error: %s" % e, file=sys.stderr)
            return 2

    # --- gather shots ---
    image_path = ""
    if args.shots:
        shots = parse_shots(args.shots)
        detection_note = "%d shots (manual entry)" % len(shots)
    elif args.image:
        from . import vision
        image_path = args.image

        face_width = args.face_width
        if face_width is None and target is not None:
            face_width = target.face_width_mm

        center_px = _parse_xy(args.center) if args.center else None
        reference = None
        if args.reference:
            parts = args.reference.split()
            if len(parts) != 3:
                print("error: --reference needs 'x1,y1 x2,y2 mm'", file=sys.stderr)
                return 2
            reference = (_parse_xy(parts[0]), _parse_xy(parts[1]), float(parts[2]))

        rgb_target = _parse_rgb(args.rgb) if args.rgb else None
        try:
            result = vision.analyze_image(
                image_path,
                mode=args.mode,
                color=args.color,
                rgb_target=rgb_target,
                rgb_tolerance=args.rgb_tol,
                dark_threshold=args.dark_threshold,
                min_blob_size=args.min_blob,
                max_blob_size=args.max_blob,
                center_px=center_px,
                auto_center=args.auto_center,
                mm_per_px=args.mm_per_px,
                face_width_mm=face_width,
                reference=reference,
            )
        except Exception as e:
            print("error: image analysis failed: %s" % e, file=sys.stderr)
            return 2
        shots = result.shots
        detection_note = ("%d shots detected from image (mode=%s, %.4f mm/px)"
                          % (len(shots), result.mode, result.mm_per_px))
    else:
        print("error: provide --image PATH or --shots 'x,y ...'", file=sys.stderr)
        return 2

    if not shots:
        print("error: no shots found to analyse.", file=sys.stderr)
        return 1

    bb_mm = tool.bb_mm or 0.0
    stats = analyze_group(shots, target=target, bb_mm=bb_mm)

    date = args.date or Session.today_iso()
    session = Session(
        id=args.id or _new_session_id(date),
        tool_id=tool.id,
        date=date,
        shots=shots,
        stats=stats,
        distance_m=args.distance,
        target_name=target.name if target else "",
        bbs=args.bbs or "",
        image_path=image_path,
        video_path=args.video or "",
        notes=args.notes or "",
    )

    print(p.label("Tool : ") + p.value(tool.name)
          + p.muted(" [%s]" % tool.category))
    print(p.muted(detection_note))
    print()
    print(report.format_group_stats(stats, session.target_name, args.distance,
                                    painter=p))

    if not args.no_save:
        db.add_session(session)
        db.save()
        print()
        print(p.good("Saved session ") + p.value(session.id)
              + p.muted(" (%d for this tool)."
                        % len(db.sessions_for_tool(tool.id))))
    return 0


def cmd_progress(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)
    sessions = db.all_sessions()
    if not sessions:
        print("No sessions yet. Analyse a target with 'marksman analyze'.")
        return 0

    if args.tool:
        tool = db.find_tool(args.tool)
        if tool is None:
            print("error: no tool matching %r." % args.tool, file=sys.stderr)
            return 2
        rep = tracker.build_report(
            db.sessions_for_tool(tool.id), "tool", tool.name)
        print(report.format_progress(rep, show_sessions=args.sessions, painter=p))
        return 0

    if args.category:
        rep = tracker.build_report(
            db.sessions_for_category(args.category), "category", args.category)
        print(report.format_progress(rep, show_sessions=args.sessions, painter=p))
        return 0

    show_overall = args.all or not (args.by_category or args.by_tool)

    if show_overall:
        rep = tracker.progress_overall(sessions)
        print(report.format_progress(rep, show_sessions=args.sessions, painter=p))

    if args.by_category or args.all:
        for rep in tracker.progress_by_category(sessions, db.tools).values():
            print()
            print(report.format_progress(rep, show_sessions=args.sessions, painter=p))

    if args.by_tool or args.all:
        for rep in tracker.progress_by_tool(sessions, db.tools).values():
            print()
            print(report.format_progress(rep, show_sessions=args.sessions, painter=p))
    return 0


def cmd_sessions(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)
    sessions = sorted(db.all_sessions(), key=lambda s: (s.date, s.id))
    if not sessions:
        print("No sessions yet.")
        return 0
    print(p.title("%-12s %-22s %-24s %5s %8s %8s"
                  % ("DATE", "TOOL", "TARGET", "SHOTS", "ES(mm)", "SCORE")))
    for s in sessions:
        w = db.get_tool(s.tool_id)
        wname = w.name if w else s.tool_id
        es = ("%.1f" % s.stats.extreme_spread_mm) if s.stats else "-"
        score = ("%.0f" % s.stats.total_score) if (s.stats and s.stats.total_score is not None) else "-"
        print(p.value("%-12s" % s.date) + " " + p.label("%-22.22s" % wname)
              + " " + p.muted("%-24.24s" % s.target_name)
              + " " + p.value("%5d" % len(s.shots))
              + " " + p.value("%8s" % es) + " " + p.value("%8s" % score))
    return 0


# --------------------------------------------------------------------------- #
# Skins / themes
# --------------------------------------------------------------------------- #

def cmd_theme(args: argparse.Namespace) -> int:
    """Dispatch ``theme`` with no sub-action to the listing."""
    action = getattr(args, "theme_action", None)
    if action == "set":
        return cmd_theme_set(args)
    if action == "preview":
        return cmd_theme_preview(args)
    return cmd_theme_list(args)


def cmd_theme_list(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    current = db.settings.get("theme") or theme_mod.DEFAULT_THEME
    enabled = theme_mod.color_enabled(
        sys.stdout, no_color=getattr(args, "no_color", False))
    head = _make_painter(args, db)
    print(head.title("Marksman skins")
          + head.muted("  (each an original palette and texture)"))
    print()
    demo = [9.0, 8.0, 8.0, 6.0, 5.0, 4.0, 4.0, 3.0]
    for th in theme_mod.list_themes():
        pt = Painter(th, enabled)
        mark = "  * " if th.key == current else "    "
        swatch = (pt.accent(report.sparkline(demo, pt.spark_ramp)) + "  "
                  + pt.good("good") + pt.muted("/") + pt.bad("bad"))
        print(head.accent(mark) + pt.title("%-11s" % th.key)
              + " " + swatch + "  " + pt.muted(th.inspired_by))
    print()
    print(head.muted("Set one with:  ") + head.value("marksman theme set <name>")
          + head.muted("   preview:  ") + head.value("marksman theme preview <name>"))
    print(head.muted("Active skin: ") + head.value(current))
    return 0


def cmd_theme_set(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    name = (args.name or "").strip().lower()
    if not theme_mod.is_theme(name):
        print("error: unknown skin %r. List them with 'marksman theme'."
              % args.name, file=sys.stderr)
        return 2
    db.settings["theme"] = name
    db.save()
    th = theme_mod.get_theme(name)
    enabled = theme_mod.color_enabled(
        sys.stdout, no_color=getattr(args, "no_color", False), force=True)
    p = Painter(th, enabled)
    print(p.good("Skin set to ") + p.title(th.title)
          + p.muted(" (%s)" % name))
    print(p.muted(th.inspired_by))
    return 0


def cmd_theme_preview(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    name = (getattr(args, "name", None) or getattr(args, "theme", None)
            or db.settings.get("theme") or theme_mod.DEFAULT_THEME)
    if not theme_mod.is_theme(name):
        print("error: unknown skin %r. List them with 'marksman theme'."
              % name, file=sys.stderr)
        return 2
    th = theme_mod.get_theme(name)
    enabled = theme_mod.color_enabled(
        sys.stdout, no_color=getattr(args, "no_color", False), force=True)
    p = Painter(th, enabled)
    print(theme_mod.sample_report(p))
    return 0


# --------------------------------------------------------------------------- #
# Media: recreations and cleanup
# --------------------------------------------------------------------------- #

def _session_target(session: Session, db: Database) -> Optional[TargetSpec]:
    """Resolve a session's target spec by name, if it is still known."""
    if not session.target_name:
        return None
    try:
        return resolve_target(session.target_name, db)
    except KeyError:
        return None


def _human_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return ("%d %s" % (int(size), unit) if unit == "B"
                    else "%.1f %s" % (size, unit))
        size /= 1024.0
    return "%d B" % n


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _select_sessions(args: argparse.Namespace, db: Database) -> List[Session]:
    """Sessions matching --tool / --before / --session filters (date order)."""
    sessions = db.all_sessions()
    if getattr(args, "session", None):
        sessions = [s for s in sessions if s.id == args.session]
    if getattr(args, "tool", None):
        tool = db.find_tool(args.tool)
        if tool is None:
            return []
        sessions = [s for s in sessions if s.tool_id == tool.id]
    if getattr(args, "before", None):
        sessions = [s for s in sessions if s.date < args.before]
    return sorted(sessions, key=lambda s: (s.date, s.id))


def _recreation_path(db: Database, session: Session) -> str:
    return os.path.join(db.recreations_dir, "%s.png" % session.id)


def _render_one(db: Database, session: Session, path: str) -> str:
    tool = db.get_tool(session.tool_id)
    shot_r = (tool.bb_mm / 2.0) if (tool and tool.bb_mm) else None
    target = _session_target(session, db)
    return render_mod.save_recreation(session, path, target=target,
                                      shot_radius_mm=shot_r)


def cmd_render(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)
    sessions = _select_sessions(args, db)
    if not sessions:
        print("No matching sessions to render.")
        return 0

    out = args.out
    # A path ending in .png (single session) writes that exact file; otherwise
    # 'out' is treated as a directory of per-session PNGs.
    single_file = bool(out) and out.lower().endswith(".png")
    if single_file and len(sessions) > 1:
        print("error: --out FILE.png needs exactly one session; got %d. "
              "Pass a directory instead." % len(sessions), file=sys.stderr)
        return 2

    written = []
    for s in sessions:
        if single_file:
            path = out
        else:
            path = os.path.join(out, "%s.png" % s.id) if out \
                else _recreation_path(db, s)
        try:
            _render_one(db, s, path)
        except Exception as e:
            print(p.bad("error: could not render %s: %s" % (s.id, e)),
                  file=sys.stderr)
            continue
        written.append((s, path))

    for s, path in written:
        print(p.good("Recreated ") + p.value(s.id)
              + p.muted("  %d shots  -> " % len(s.shots)) + p.value(path))
    print()
    print(p.label("Rendered ") + p.value("%d" % len(written))
          + p.label(" recreation(s)."))
    return 0


def cmd_cleanup(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)
    sessions = _select_sessions(args, db)

    # Only sessions that actually have source media on disk are candidates.
    candidates = []   # (session, [(path, size), ...], total)
    for s in sessions:
        files = [(path, _file_size(path)) for path in s.source_media_paths
                 if os.path.isfile(path)]
        if files:
            candidates.append((s, files, sum(sz for _, sz in files)))

    if not candidates:
        print("Nothing to clean up: no stored source media found"
              + (" for that filter." if (args.tool or args.before
                                         or args.session) else "."))
        return 0

    total = sum(t for _, _, t in candidates)
    recreate = not args.no_recreate

    print(p.title("Cleanup %s" % ("(applying)" if args.apply else "(dry run)")))
    print(p.rule(p.rule_char * 18))
    for s, files, sub in candidates:
        w = db.get_tool(s.tool_id)
        wname = w.name if w else s.tool_id
        print("  " + p.value(s.id) + p.muted("  %s  " % wname)
              + p.label("%s" % _human_bytes(sub)))
        for path, sz in files:
            print("      " + p.muted("%-9s " % _human_bytes(sz)) + path)

    if not args.apply:
        print()
        print(p.accent("Would free ") + p.value(_human_bytes(total))
              + p.accent(" across ") + p.value("%d" % len(candidates))
              + p.accent(" session(s)."))
        if recreate:
            print(p.muted("A recreation diagram will be saved for each before "
                          "its media is deleted."))
        print(p.muted("Re-run with ") + p.value("--apply")
              + p.muted(" to delete. Shot data (and recreations) are kept."))
        return 0

    # --- apply ---------------------------------------------------------- #
    freed = 0
    cleaned = 0
    recreated = 0
    for s, files, _sub in candidates:
        # Recreate FIRST so a failure never costs us the source.
        if recreate and not s.recreation_path:
            path = _recreation_path(db, s)
            try:
                _render_one(db, s, path)
                s.recreation_path = path
                recreated += 1
            except Exception as e:
                print(p.bad("warning: skipping %s (recreation failed: %s)"
                            % (s.id, e)), file=sys.stderr)
                continue
        for path, sz in files:
            try:
                os.remove(path)
                freed += sz
            except OSError as e:
                print(p.bad("warning: could not delete %s: %s" % (path, e)),
                      file=sys.stderr)
        s.image_path = ""
        s.video_path = ""
        s.media_cleaned = True
        cleaned += 1

    db.save()
    print()
    print(p.good("Freed ") + p.value(_human_bytes(freed))
          + p.good(" from ") + p.value("%d" % cleaned) + p.good(" session(s)."))
    if recreated:
        print(p.label("Saved ") + p.value("%d" % recreated)
              + p.label(" recreation(s) in ") + p.value(db.recreations_dir))
    return 0


# --------------------------------------------------------------------------- #
# AI coach (cowork folder handshake)
# --------------------------------------------------------------------------- #

def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def cmd_coach(args: argparse.Namespace) -> int:
    action = getattr(args, "coach_action", None)
    if action == "export":
        return cmd_coach_export(args)
    if action == "apply":
        return cmd_coach_apply(args)
    return cmd_coach_show(args)


def cmd_coach_export(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)
    if not any(s.stats for s in db.all_sessions()):
        print("No analysed sessions yet - run 'marksman analyze' first.",
              file=sys.stderr)
        return 1
    out_dir = args.out or coach_mod.coach_dir(db)
    instruction = args.instruction or coach_mod.DEFAULT_INSTRUCTION
    paths = coach_mod.write_request(db, out_dir, instruction, _now_iso())
    print(p.good("Wrote coach request to ") + p.value(out_dir))
    for label in ("request", "digest", "brief"):
        print(p.muted("  - ") + p.value(paths[label]))
    print()
    print(p.label("Next: ") + p.muted("point a Claude Code agent at that folder "
          "(it reads ") + p.value("CLAUDE.md")
          + p.muted("), or paste ") + p.value("request.md")
          + p.muted(" into any Claude chat."))
    print(p.muted("Then drop its ") + p.value("reply.json")
          + p.muted(" in the folder and run ") + p.value("marksman coach apply")
          + p.muted("."))
    return 0


def cmd_coach_apply(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)
    path = args.file or os.path.join(args.out or coach_mod.coach_dir(db), "reply.json")
    if not os.path.isfile(path):
        print("error: no reply at %s. Export first, let an agent answer, then "
              "apply." % path, file=sys.stderr)
        return 2
    try:
        reply = coach_mod.read_reply(path)
    except Exception as e:
        print("error: could not parse reply %s: %s" % (path, e), file=sys.stderr)
        return 2
    summary = coach_mod.apply_reply(db, reply, _now_iso())
    if not summary.get("applied"):
        print(p.muted("Nothing to apply: %s." % summary.get("reason", "no change")))
        return 0
    db.save()
    print(p.good("Applied coaching from ") + p.value(path))
    print(p.muted("  %d drill(s), %d tool tip(s), %d note(s) stored."
                  % (summary["drills"], summary["toolTips"], summary["notes"])))
    print()
    print(p.label("Focus: ") + p.value(summary["focus"] or "(none given)"))
    print(p.muted("See it any time with ") + p.value("marksman coach show") + p.muted("."))
    return 0


def cmd_coach_show(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)
    c = db.settings.get("coaching")
    if not c:
        print("No coaching yet. Run 'marksman coach export', have a Claude agent "
              "answer, then 'marksman coach apply'.")
        return 0
    print(p.title("Coach") + p.muted("  (applied %s)" % c.get("appliedAt", "?")))
    if c.get("focus"):
        print()
        print(p.accent("Focus: ") + p.value(c["focus"]))
    if c.get("analysis"):
        print()
        print(p.label("Analysis"))
        print(c["analysis"])
    if c.get("drills"):
        print()
        print(p.label("Drills"))
        for d in c["drills"]:
            print("  " + p.value(d.get("name", "drill")) + p.muted(" - "
                  + d.get("why", "")))
            if d.get("how"):
                print(p.muted("      " + d["how"]))
    if c.get("toolTips"):
        print()
        print(p.label("Tool tips"))
        for t in c["toolTips"]:
            w = db.get_tool(t.get("toolId", ""))
            name = w.name if w else t.get("toolId", "")
            print("  " + p.value("%s: " % name) + p.muted(t.get("tip", "")))
    return 0


# --------------------------------------------------------------------------- #
# Goals
# --------------------------------------------------------------------------- #

def cmd_goal(args: argparse.Namespace) -> int:
    action = getattr(args, "goal_action", None)
    if action == "set":
        return cmd_goal_set(args)
    if action == "rm":
        return cmd_goal_rm(args)
    return cmd_goal_list(args)


def cmd_goal_set(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)
    tool_id = None
    if args.tool:
        tool = db.find_tool(args.tool)
        if tool is None:
            print("error: no tool matching %r." % args.tool, file=sys.stderr)
            return 2
        tool_id = tool.id
    try:
        goal = goals_mod.new_goal(args.metric, args.target, tool_id, args.note or "")
    except ValueError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    db.settings.setdefault("goals", []).append(goal)
    db.save()
    key, unit, lower = goals_mod.METRICS[goal["metric"]]
    scope = db.get_tool(tool_id).name if tool_id else "overall"
    print(p.good("Goal ") + p.value(goal["id"]) + p.good(" set: ")
          + p.label("%s %s %g %s" % (goal["metric"], "<=" if lower else ">=",
                                     goal["target"], unit))
          + p.muted(" (%s)" % scope))
    return 0


def cmd_goal_rm(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)
    goals = db.settings.get("goals", [])
    kept = [g for g in goals if g["id"] != args.id]
    if len(kept) == len(goals):
        print("error: no goal with id %r (list with 'marksman goal')." % args.id,
              file=sys.stderr)
        return 2
    db.settings["goals"] = kept
    db.save()
    print(p.good("Removed goal ") + p.value(args.id) + p.good("."))
    return 0


def cmd_goal_list(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)
    rows = goals_mod.summary(db)
    if not rows:
        print("No goals yet. Set one with: "
              "marksman goal set --metric group_size --target 30 [--tool aeg1]")
        return 0
    print(p.title("%-8s %-14s %-8s %-16s %-9s %s"
                  % ("ID", "METRIC", "TARGET", "SCOPE", "BEST", "STATUS")))
    for r in rows:
        cmp = "<=" if r["lowerIsBetter"] else ">="
        target = "%g %s" % (r["target"], r["unit"])
        best = "-" if r["best"] is None else ("%.1f" % r["best"])
        if r["met"] is None:
            status = p.muted("no data")
        elif r["met"]:
            status = p.good("met")
        else:
            status = p.bad("working")
        print(p.value("%-8s" % r["id"]) + " " + p.label("%-14s" % r["metric"])
              + " " + p.muted("%-2s" % cmp) + p.value("%-6s" % ("%g" % r["target"]))
              + " " + p.muted("%-16.16s" % r["scope"])
              + " " + p.value("%-9s" % best) + " " + status)
    print()
    print(p.muted("Metrics: ") + p.value(", ".join(goals_mod.METRICS)))
    return 0


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #

def cmd_export(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    text = exporter.to_json(db) if args.format == "json" else exporter.to_csv(db)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        p = _make_painter(args, db)
        n = len(exporter.session_rows(db))
        print(p.good("Exported ") + p.value("%d session(s)" % n)
              + p.good(" to ") + p.value(args.out))
    else:
        sys.stdout.write(text)
    return 0


# --------------------------------------------------------------------------- #
# Logo / icon
# --------------------------------------------------------------------------- #

def cmd_logo(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)
    png_path, ico_path = logo_mod.save_logo(args.out, size=args.size)
    print(p.good("Wrote logo ") + p.value(png_path)
          + p.muted(" (%dpx)" % args.size))
    print(p.good("Wrote icon ") + p.value(ico_path) + p.muted(" (256px)"))
    return 0


# --------------------------------------------------------------------------- #
# Argument parser
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="marksman",
        description="Progress tracker for airsoft marksmanship: analyse "
                    "marked-up target images and track progress over time.",
    )
    p.add_argument("--db", default=DEFAULT_DB_PATH,
                   help="database file (default: %s)" % DEFAULT_DB_PATH)
    p.add_argument("--theme", help="skin for this run (overrides the saved one); "
                                   "see 'marksman theme'")
    p.add_argument("--no-color", action="store_true", dest="no_color",
                   help="disable coloured output")
    sub = p.add_subparsers(dest="command")
    sub.required = True

    # tool
    wp = sub.add_parser("tool", help="manage tools")
    wsub = wp.add_subparsers(dest="tool_command")
    wsub.required = True
    wa = wsub.add_parser("add", help="add a tool")
    wa.add_argument("--id", help="short id (auto if omitted)")
    wa.add_argument("--name", required=True)
    wa.add_argument("--category", default="Other",
                    help="e.g. 'AEG', 'GBB Pistol', 'Bolt-Action'")
    wa.add_argument("--bb", default="", help="free text, e.g. '6mm' or '0.25g'")
    wa.add_argument("--bb-mm", type=float, dest="bb_mm",
                    help="BB diameter in mm, default 6 (improves scoring)")
    wa.add_argument("--gas", action="store_true",
                    help="gas-powered (GBB / HPA)")
    wa.add_argument("--notes", default="")
    wa.set_defaults(func=cmd_tool_add)
    wl = wsub.add_parser("list", help="list tools")
    wl.set_defaults(func=cmd_tool_list)

    # targets
    tp = sub.add_parser("targets", help="list known target faces")
    tp.set_defaults(func=cmd_targets)

    # analyze
    ap = sub.add_parser("analyze", help="analyse a target (image or coordinates)")
    ap.add_argument("--tool", required=True, help="tool id or name")
    ap.add_argument("--target", help="target face name (enables scoring)")
    ap.add_argument("--distance", type=float, help="distance in metres")
    ap.add_argument("--date", help="ISO date (default: today)")
    ap.add_argument("--bbs", default="")
    ap.add_argument("--notes", default="")
    ap.add_argument("--id", help="session id (auto if omitted)")
    ap.add_argument("--no-save", action="store_true", help="analyse without saving")
    # input
    ap.add_argument("--shots", help="manual shots 'x,y x,y' in mm from POA")
    ap.add_argument("--image", help="path to a marked-up target image (PNG)")
    ap.add_argument("--video", help="path to a source video to attach (kept only "
                                    "as a reference; cleared by 'cleanup')")
    # vision options
    ap.add_argument("--mode", choices=["marker", "holes"], default="marker")
    ap.add_argument("--color", default="red",
                    help="marker colour preset (red/green/blue/...)")
    ap.add_argument("--rgb", help="explicit marker colour 'r,g,b'")
    ap.add_argument("--rgb-tol", type=int, default=60, dest="rgb_tol")
    ap.add_argument("--dark-threshold", type=int, default=70, dest="dark_threshold")
    ap.add_argument("--min-blob", type=int, default=12, dest="min_blob")
    ap.add_argument("--max-blob", type=int, default=None, dest="max_blob")
    ap.add_argument("--center", help="point of aim in pixels 'x,y'")
    ap.add_argument("--auto-center", action="store_true",
                    help="detect point of aim from the dark bull")
    ap.add_argument("--mm-per-px", type=float, dest="mm_per_px",
                    help="pixel scale (mm per pixel)")
    ap.add_argument("--face-width", type=float, dest="face_width",
                    help="physical width of the target face in mm (image spans it)")
    ap.add_argument("--reference", help="scale ref 'x1,y1 x2,y2 mm'")
    ap.set_defaults(func=cmd_analyze)

    # progress
    pp = sub.add_parser("progress", help="show progress reports")
    pp.add_argument("--by-category", action="store_true", dest="by_category")
    pp.add_argument("--by-tool", action="store_true", dest="by_tool")
    pp.add_argument("--tool", help="single tool id or name")
    pp.add_argument("--category", help="single category")
    pp.add_argument("--all", action="store_true",
                    help="overall + all categories + all tools")
    pp.add_argument("--sessions", action="store_true", help="list each session")
    pp.set_defaults(func=cmd_progress)

    # sessions
    sp = sub.add_parser("sessions", help="list saved sessions")
    sp.set_defaults(func=cmd_sessions)

    # coach (AI cowork folder handshake)
    chp = sub.add_parser("coach",
                         help="AI coaching via a cowork folder (no API key needed)")
    chp.set_defaults(func=cmd_coach)
    chsub = chp.add_subparsers(dest="coach_action")
    che = chsub.add_parser("export",
                           help="write a coach request folder for a Claude agent")
    che.add_argument("--out", help="folder (default: 'coach/' beside the DB)")
    che.add_argument("--instruction", help="override the coaching task text")
    che.set_defaults(func=cmd_coach_export)
    cha = chsub.add_parser("apply", help="ingest an agent's reply.json")
    cha.add_argument("--file", help="reply file (default: <folder>/reply.json)")
    cha.add_argument("--out", help="coach folder (default: 'coach/' beside the DB)")
    cha.set_defaults(func=cmd_coach_apply)
    chs = chsub.add_parser("show", help="show the latest applied coaching")
    chs.set_defaults(func=cmd_coach_show)

    # export (CSV/JSON for spreadsheets / backups)
    ep = sub.add_parser("export", help="export sessions as CSV or JSON")
    ep.add_argument("--format", choices=["csv", "json"], default="csv")
    ep.add_argument("--out", help="output file (default: write to stdout)")
    ep.set_defaults(func=cmd_export)

    # goals (a target for one metric, overall or per tool)
    gp = sub.add_parser("goal", help="set and track practice goals")
    gp.set_defaults(func=cmd_goal)
    gsub = gp.add_subparsers(dest="goal_action")
    gs = gsub.add_parser("set", help="set a goal")
    gs.add_argument("--metric", required=True, choices=list(goals_mod.METRICS),
                    help="metric to target")
    gs.add_argument("--target", required=True, type=float, help="target value")
    gs.add_argument("--tool", help="scope to one tool (default: overall)")
    gs.add_argument("--note", default="")
    gs.set_defaults(func=cmd_goal_set)
    gr = gsub.add_parser("rm", help="remove a goal by id")
    gr.add_argument("id")
    gr.set_defaults(func=cmd_goal_rm)
    gl = gsub.add_parser("list", help="list goals and progress")
    gl.set_defaults(func=cmd_goal_list)

    # logo (generate the app icon)
    lp = sub.add_parser("logo", help="generate the Marksman logo PNG + .ico")
    lp.add_argument("--out", default="assets", help="output folder (default: assets)")
    lp.add_argument("--size", type=int, default=512, help="PNG size in px")
    lp.set_defaults(func=cmd_logo)

    # render (graphical recreations from stored shot data)
    rp = sub.add_parser("render",
                        help="redraw target diagrams from stored shot data")
    rp.add_argument("--tool", help="only this tool (id or name)")
    rp.add_argument("--session", help="only this session id")
    rp.add_argument("--before", help="only sessions before this ISO date")
    rp.add_argument("--out", help="output PNG file (single session) or "
                                  "directory (default: the recreations folder)")
    rp.set_defaults(func=cmd_render)

    # cleanup (delete bulky source media; keep recreatable results)
    cp = sub.add_parser("cleanup",
                        help="delete stored images/videos to save space "
                             "(recreations are kept)")
    cp.add_argument("--tool", help="only this tool (id or name)")
    cp.add_argument("--session", help="only this session id")
    cp.add_argument("--before", help="only sessions before this ISO date")
    cp.add_argument("--apply", action="store_true",
                    help="actually delete (default is a dry run)")
    cp.add_argument("--no-recreate", action="store_true",
                    help="do not save a recreation diagram before deleting")
    cp.set_defaults(func=cmd_cleanup)

    # theme (skins)
    thp = sub.add_parser("theme", help="choose a visual skin for the reports")
    thp.set_defaults(func=cmd_theme)
    thsub = thp.add_subparsers(dest="theme_action")
    ths = thsub.add_parser("set", help="save a skin as the default")
    ths.add_argument("name", help="skin name (e.g. recon, inferno, orbital)")
    ths.set_defaults(func=cmd_theme_set)
    thv = thsub.add_parser("preview", help="preview a skin")
    thv.add_argument("name", nargs="?", help="skin name (default: current)")
    thv.set_defaults(func=cmd_theme_preview)
    thl = thsub.add_parser("list", help="list all skins")
    thl.set_defaults(func=cmd_theme_list)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    # AI-authored coaching text can carry unicode a legacy Windows console
    # (cp1252) can't encode; replace instead of crashing. ponytail: no-op on
    # UTF-8/redirected output and on test StringIO doubles (no reconfigure).
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(errors="replace")
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
