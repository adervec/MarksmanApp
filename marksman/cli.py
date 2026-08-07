"""Command-line interface for Marksman.

Examples
--------
    # Register a tool -- anything that launches a projectile
    marksman tool add --id b1 --name "Training AEG" \
        --category "AEG" --projectile 6mm --projectile-mm 6.0

    # Analyse a marked-up target image (red marker dots), score it, save it
    marksman analyze --tool b1 --target "Airsoft Practice 10m" --distance 10 \
        --image shots.png --color red --auto-center

    # Or enter shot coordinates by hand (millimetres from point of aim)
    marksman analyze --tool b1 --target "Airsoft Practice 10m" --distance 10 \
        --shots "1.2,3.4  -2.0,5.1  0.5,-1.0"

    # Track progress
    marksman progress                 # overall
    marksman progress --by-category
    marksman progress --by-tool
    marksman progress --tool ap1 --sessions
"""

from __future__ import annotations

import argparse
import json
import os
import textwrap
import sys
import uuid
import webbrowser
from typing import List, Optional, Tuple

from .models import Shot, Tool, Session, TargetSpec, normalize_category
from .grouping import analyze_group
from . import targets as targets_mod
from .storage import Database, DEFAULT_DB_PATH
from . import tracker
from . import report
from . import render as render_mod
from . import sheets as sheets_mod
from . import theme as theme_mod
from . import coach as coach_mod
from . import exporter
from . import logo as logo_mod
from . import goals as goals_mod
from . import drills as drills_mod
from . import packs as packs_mod
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


def _wrap(text: str, indent: str = "  ", width: int = 78) -> str:
    """Wrap prose for the terminal (used for pack descriptions and warnings)."""
    return textwrap.fill(text, width=width, initial_indent=indent,
                         subsequent_indent=indent)


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
    click_mrad = None
    if getattr(args, "click", None):
        try:
            click_mrad = tracker.parse_click(args.click)
        except ValueError as e:
            print("error: %s" % e)
            return 2
    tool = Tool(
        id=wid,
        name=args.name,
        category=args.category,
        projectile=args.projectile or "",
        is_powered=args.is_powered,
        projectile_mm=args.projectile_mm,
        sight_click_mrad=click_mrad,
        notes=args.notes or "",
    )
    db.add_tool(tool)
    db.save()
    print("Added tool %s: %s [%s]" % (tool.id, tool.name, tool.category))
    return 0


def cmd_tool_set(args: argparse.Namespace) -> int:
    """Change one field of an existing tool; leave the rest alone."""
    db = Database.load(args.db)
    tool = db.find_tool(args.id)
    if tool is None:
        print("error: no tool matching %r. List with 'marksman tool list'." % args.id)
        return 2
    changed = []
    for attr, value in (("name", args.name), ("category", args.category),
                        ("projectile", args.projectile), ("notes", args.notes)):
        if value is not None:
            setattr(tool, attr, value)
            changed.append(attr)
    if args.projectile_mm is not None:
        tool.projectile_mm = args.projectile_mm or None
        changed.append("projectile_mm")
    if args.is_powered is not None:
        tool.is_powered = args.is_powered
        changed.append("powered")
    if args.click is not None:
        try:
            tool.sight_click_mrad = tracker.parse_click(args.click) if args.click else None
        except ValueError as e:
            print("error: %s" % e)
            return 2
        changed.append("sight click")
    if not changed:
        print("Nothing to change. Pass --name, --category, --click, ...")
        return 0
    if args.category is not None:
        tool.category = normalize_category(tool.category)
    db.save()
    print("Updated %s: %s" % (tool.id, ", ".join(changed)))
    return 0


def cmd_tool_list(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)
    if not db.tools:
        print("No tools yet. Add one with: marksman tool add --name ...")
        return 0
    print(p.title("%-12s %-28s %-18s %-12s SESSIONS"
                  % ("ID", "NAME", "CATEGORY", "PROJECTILE")))
    for w in sorted(db.tools.values(), key=lambda x: x.name.lower()):
        n = len(db.sessions_for_tool(w.id))
        print(p.value("%-12s" % w.id) + " " + p.label("%-28s" % w.name)
              + " " + p.muted("%-18s %-12s" % (w.category, w.projectile))
              + " " + p.value("%d" % n))
    return 0


def cmd_targets(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)
    if getattr(args, "print_face", None):
        return _print_face(args, db, p)
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
    print()
    print(p.title("Printable drill sheets (any family at any size in mm):"))
    for name, blurb in sheets_mod.catalog():
        print("  " + p.label("%-14s" % name) + p.muted(blurb))
    print(p.muted("Print any of it: marksman targets --print dots-15 "
                  "[--paper a3]"))
    return 0


def _slug(name: str) -> str:
    out = "".join(c.lower() if c.isalnum() else "-" for c in name)
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-") or "target"


def _print_face(args: argparse.Namespace, db: Database, p: Painter) -> int:
    """Write a true-scale printable face or drill sheet, and (by default)
    open it.  A name is tried as a scoring face first, then as a design from
    the parametric sheet catalogue ('dots-15', 'bulls-40', 'face-120', ...)."""
    name = args.print_face
    try:
        try:
            spec = resolve_target(name, db)
            page = render_mod.target_html(spec, distance_m=args.distance,
                                          paper=args.paper)
            what = "%s, %.0f mm across" % (spec.name, spec.outer_radius_mm * 2)
        except KeyError:
            page = sheets_mod.make(name, paper=args.paper)
            what = name
            spec = None
    except (KeyError, ValueError) as e:
        print(p.bad("error: %s" % (e.args[0] if e.args else e)))
        print(p.muted("  'marksman targets' lists every face and sheet design."))
        return 2
    path = os.path.abspath(args.out or
                           ("%s.html" % _slug(spec.name if spec else name)))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    sheets = page.count("class='sheet'")
    print(p.good("Printable target written: ") + p.value(path))
    print(p.muted("  %s, %d sheet%s of %s"
                  % (what, sheets, "" if sheets == 1 else "s", args.paper)))
    print(p.muted("  Print at 100% ('actual size'), then check the ruler on "
                  "each sheet measures what it says."))
    if not args.no_open:
        webbrowser.open("file:///" + path.replace("\\", "/"))
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)

    tool = db.find_tool(args.tool)
    if tool is None:
        print("error: no tool matching %r. List with 'marksman tool list'."
              % args.tool, file=sys.stderr)
        return 2

    # A named drill supplies its own distance and target face unless overridden,
    # so `--drill group-10` is enough to log a standards attempt.
    drill = None
    if getattr(args, "drill", None):
        try:
            drill = drills_mod.get_drill(args.drill)
        except KeyError as e:
            print("error: %s" % e, file=sys.stderr)
            return 2
        if args.distance is None:
            args.distance = drill["distance_m"]
        if not args.target:
            args.target = drill["target"]

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

    stats = analyze_group(shots, target=target,
                          projectile_mm=tool.projectile_mm or 0.0)

    date = args.date or Session.today_iso()
    session = Session(
        id=args.id or _new_session_id(date),
        tool_id=tool.id,
        date=date,
        shots=shots,
        stats=stats,
        distance_m=args.distance,
        target_name=target.name if target else "",
        projectiles=args.projectiles or "",
        drill_id=drill["id"] if drill else "",
        image_path=image_path,
        video_path=args.video or "",
        notes=args.notes or "",
    )

    print(p.label("Tool : ") + p.value(tool.name)
          + p.muted(" [%s]" % tool.category))
    print(p.muted(detection_note))
    print()
    print(report.format_group_stats(stats, session.target_name, args.distance,
                                    painter=p,
                                    click_mrad=tool.sight_click_mrad))

    if not args.no_save:
        db.add_session(session)
        db.save()
        print()
        print(p.good("Saved session ") + p.value(session.id)
              + p.muted(" (%d for this tool)."
                        % len(db.sessions_for_tool(tool.id))))
        if drill is not None:
            _print_drill_result(p, db, drill)
    return 0


def _print_drill_result(p: Painter, db: Database, drill: dict) -> None:
    """After logging a drill attempt: the tier it earned and the next rung."""
    row = drills_mod.standing(db, drill)
    print()
    print(p.label("Drill : ") + p.value(drill["name"])
          + p.muted("  (attempt %d)" % row["attempts"]))
    if row["tier"]:
        print(p.good("Tier  : ") + p.value(row["tier"])
              + p.muted("  best %.1f %s" % (row["best"], row["unit"])))
    else:
        print(p.muted("Tier  : below %s (%s %g %s)"
                      % (drills_mod.TIERS[0],
                         "<=" if row["lowerIsBetter"] else ">=",
                         drill["cutoffs"][0], row["unit"])))
    if row["nextCutoff"] is not None:
        print(p.label("Next  : ") + p.value(row["nextTier"])
              + p.muted(" at %s%g %s"
                        % ("<=" if row["lowerIsBetter"] else ">=",
                           row["nextCutoff"], row["unit"])))


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
    shot_r = (tool.projectile_mm / 2.0) if (tool and tool.projectile_mm) else None
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
              "marksman goal set --metric group_size --target 30 [--tool b1]")
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
# Drills
# --------------------------------------------------------------------------- #

def cmd_import(args: argparse.Namespace) -> int:
    """Merge another Marksman database (or a Drive backup) into this one.

    Union by id, and nothing already here is overwritten -- so importing the
    same file twice changes nothing the second time.
    """
    db = Database.load(args.db)
    p = _make_painter(args, db)
    try:
        with open(args.file, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as e:
        print(p.bad("error: can't read %s (%s)" % (args.file, e)))
        return 2
    if not isinstance(raw, dict) or not isinstance(raw.get("sessions"), list):
        print(p.bad("error: %s isn't a Marksman database or backup." % args.file))
        print(p.muted("  Expected the app's own JSON file, or the bundle the "
                      "Drive tab syncs. A CSV/JSON export is a report, not a "
                      "backup -- it can't be imported."))
        return 2
    try:
        other = Database.from_dict(raw, path=args.file)
    except (KeyError, TypeError, ValueError) as e:
        print(p.bad("error: that file is damaged (%s)" % e))
        return 2

    new_tools = [t for t in other.tools.values() if t.id not in db.tools]
    new_faces = [t for t in other.custom_targets.values()
                 if t.name.lower() not in db.custom_targets]
    known = set(db.tools) | set(t.id for t in new_tools)
    new_sessions, orphans = [], 0
    for sess in other.sessions.values():
        if sess.id in db.sessions:
            continue
        if sess.tool_id not in known:      # a session with no tool can't be scoped
            orphans += 1
            continue
        new_sessions.append(sess)

    print(p.title("Importing %s" % os.path.basename(args.file)))
    print("  " + p.label("tools    : ") + p.value("%d new" % len(new_tools))
          + p.muted(" of %d" % len(other.tools)))
    print("  " + p.label("sessions : ") + p.value("%d new" % len(new_sessions))
          + p.muted(" of %d" % len(other.sessions)))
    if new_faces:
        print("  " + p.label("faces    : ") + p.value("%d new" % len(new_faces)))
    if orphans:
        print("  " + p.muted("%d session(s) skipped: their tool isn't in either "
                             "file." % orphans))
    if args.dry_run:
        print(p.muted("Dry run -- nothing written. Re-run without --dry-run."))
        return 0
    if not (new_tools or new_sessions or new_faces):
        print(p.muted("Nothing to add; this database already has it all."))
        return 0
    for t in new_tools:
        db.tools[t.id] = t
    for f in new_faces:
        db.custom_targets[f.name.lower()] = f
    for sess in new_sessions:
        db.sessions[sess.id] = sess
    db.save()
    print(p.good("Merged into %s" % db.path))
    return 0


def cmd_drill(args: argparse.Namespace) -> int:
    action = getattr(args, "drill_action", None)
    if action == "show":
        return cmd_drill_show(args)
    if action == "plan":
        return cmd_drill_plan(args)
    return cmd_drill_list(args)


def _tier_paint(p: Painter, tier) -> str:
    if tier is None:
        return p.muted("%-9s" % "-")
    return (p.good if tier == drills_mod.TIERS[-1] else p.value)("%-9s" % tier)


def cmd_drill_list(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)
    rows = drills_mod.standings(db)
    if getattr(args, "family", None):
        want = args.family.strip().lower()
        rows = [r for r in rows if r["family"].lower() == want]
        if not rows:
            print("error: no drills in family %r (families: %s)."
                  % (args.family, ", ".join(drills_mod.families())), file=sys.stderr)
            return 2
    print(p.title("%-16s %-13s %-12s %-8s %-9s %-14s %s"
                  % ("ID", "FAMILY", "METRIC", "BEST", "TIER", "NEXT", "TRIES")))
    for r in rows:
        nxt = ("-" if not r["nextTier"]
               else "%s %g" % (r["nextTier"], r["nextCutoff"]))
        best = "-" if r["best"] is None else "%.1f" % r["best"]
        print(p.value("%-16s" % r["id"]) + " " + p.muted("%-13.13s" % r["family"])
              + " " + p.label("%-12.12s" % r["metric"])
              + " " + p.value("%-8s" % best) + " " + _tier_paint(p, r["tier"])
              + " " + p.muted("%-14.14s" % nxt) + " " + p.value(str(r["attempts"])))
    pts = drills_mod.tier_points(db)
    print()
    print(p.label("Tiers earned: ") + p.value("%d/%d" % (pts["earned"], pts["possible"]))
          + p.muted("  across %d/%d drills tried"
                    % (pts["drillsAttempted"], pts["drillsTotal"])))
    print(p.muted("Details: ") + p.value("marksman drill show <id>")
          + p.muted("   Today's picks: ") + p.value("marksman drill plan"))
    return 0


def cmd_drill_show(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)
    try:
        d = drills_mod.get_drill(args.id)
    except KeyError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    r = drills_mod.standing(db, d)
    print(p.title(d["name"]) + p.muted("  [%s]" % d["id"]))
    print(p.muted("%s  |  %g m  |  %d shots  |  %s"
                  % (d["family"], d["distance_m"], d["shots"], d["target"])))
    print()
    print(d["why"])
    print()
    print(p.label("How to run it"))
    for step in d["how"]:
        print(p.muted("  - ") + step)
    print()
    print(p.label("Cues"))
    for cue in d["cues"]:
        print(p.muted("  - ") + cue)
    print()
    print(p.label("Standards ") + p.muted("(%s, %s)" % (r["metric"], r["unit"])))
    for tier, cutoff in zip(drills_mod.TIERS, d["cutoffs"]):
        earned = bool(r["tier"]) and \
            drills_mod.TIERS.index(r["tier"]) >= drills_mod.TIERS.index(tier)
        mark = p.good("[x] ") if earned else p.muted("[ ] ")
        print("  " + mark + p.value("%-9s" % tier)
              + p.muted("%s " % ("<=" if r["lowerIsBetter"] else ">="))
              + p.value("%g" % cutoff))
    print()
    if r["attempts"]:
        print(p.label("Your standing: ") + p.value("%d attempt(s)" % r["attempts"])
              + p.muted(", best ") + p.value("%.1f" % r["best"])
              + p.muted(", latest ") + p.value("%.1f" % r["latest"])
              + p.muted(", trend ") + p.trend(r["direction"], r["direction"]))
    else:
        print(p.muted("Not attempted yet."))
    print(p.muted("Log it with: ") + p.value(
        "marksman analyze --tool <id> --drill %s --distance %g --target %r ..."
        % (d["id"], d["distance_m"], d["target"])))
    return 0


def cmd_drill_plan(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)
    rows = drills_mod.plan(db, count=args.count)
    print(p.title("Today's plan") + p.muted("  (adaptive: untried, close to the "
                                            "next tier, or overdue)"))
    print()
    for i, r in enumerate(rows, 1):
        d = drills_mod.get_drill(r["id"])
        print(p.accent(" %d. " % i) + p.value(r["name"])
              + p.muted("  [%s]" % r["id"]))
        print(p.muted("     %s  |  %g m  |  %d shots  |  %s"
                      % (d["family"], d["distance_m"], d["shots"], d["target"])))
        print(p.muted("     why: ") + p.label(r["reason"])
              + p.muted("   tier: ") + (r["tier"] or "-"))
    print()
    print(p.muted("Full catalogue: ") + p.value("marksman drill"))
    return 0


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #

def cmd_gui(args: argparse.Namespace) -> int:
    try:
        from . import gui as gui_mod
    except ImportError as e:      # tkinter absent (some Linux distros ship it apart)
        print("error: the GUI needs tkinter, which this Python doesn't have (%s).\n"
              "       Install it (e.g. 'sudo apt install python3-tk') or use the "
              "CLI." % e, file=sys.stderr)
        return 1
    return gui_mod.launch(args.db, getattr(args, "theme", None))


def cmd_web(args: argparse.Namespace) -> int:
    from . import web as web_mod
    if args.new_key:
        db = Database.load(args.db)
        db.settings.pop("web_key", None)
        db.save()
        print("Access key rotated -- old links, bookmarks and installed icons "
              "will stop working.")
    return web_mod.serve(args.db, host=args.host, port=args.port)


# --------------------------------------------------------------------------- #
# Equipment packs
# --------------------------------------------------------------------------- #

def cmd_pack(args: argparse.Namespace) -> int:
    return cmd_pack_list(args)


def _sens(p: Painter, level: int) -> str:
    label = packs_mod.SENSITIVITY.get(level, ("?", ""))[0]
    paint = p.good if level <= 2 else (p.label if level <= 3 else p.bad)
    return paint("%d %s" % (level, label))


def cmd_pack_list(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)
    rows = packs_mod.status(db)
    cfg = packs_mod.settings(db)
    if not rows:
        print("No packs found. Bundled packs live in %s" % packs_mod.BUNDLED_DIR)
        return 0
    print(p.title("Equipment packs") + p.muted("   (ceiling: sensitivity %d)"
                                               % cfg["max_sensitivity"]))
    print()
    for row in rows:
        mark = p.good("on ") if row["active"] else p.muted("off")
        print(" " + mark + " " + p.value("%-14s" % row["id"])
              + p.label("%-26s" % row["name"][:26])
              + _sens(p, row.get("sensitivity", 5)))
        detail = "%d drills, %d faces" % (len(row["drills"]), len(row["targets"]))
        if not row["active"]:
            detail += "  --  " + row["reason"]
        print("    " + p.muted(detail))
    print()
    print(p.muted("Your packs go in: ") + p.value(packs_mod.user_dir()))
    print(p.muted("Details: ") + p.value("marksman pack show <id>"))
    return 0


def cmd_pack_show(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)
    for row in packs_mod.status(db):
        if row["id"] != args.id.strip().lower():
            continue
        print(p.title(row["name"]) + p.muted("  [%s] v%s" % (row["id"], row.get("version", "?"))))
        if row.get("error"):
            print(p.bad("  broken: %s" % row["error"]))
            return 1
        print()
        if row.get("description"):
            print(_wrap(row["description"], indent="  "))
            print()
        level = row["sensitivity"]
        name, blurb = packs_mod.SENSITIVITY[level]
        print(p.muted("  Sensitivity: ") + _sens(p, level) + p.muted(" -- " + blurb))
        print(p.muted("  Status:      ") + (p.good("active") if row["active"]
                                            else p.bad(row["reason"])))
        print(p.muted("  Source:      ") + ("bundled with the app" if row["bundled"]
                                            else row["source"]))
        if row.get("safety"):
            print()
            print(p.bad("  Safety (from the pack author)"))
            print(_wrap(row["safety"], indent="    "))
        if row["categories"]:
            print()
            print(p.muted("  Categories: ") + ", ".join(row["categories"]))
        if row["targets"]:
            print()
            print(p.title("  Target faces"))
            for t in row["targets"]:
                print("    " + p.label("%-24s" % t["name"])
                      + p.muted("%.0f mm ten-ring, %.0f mm face"
                                % (t["ten_ring_mm"], t["face_mm"])))
        if row["drills"]:
            print()
            print(p.title("  Drills"))
            for d in row["drills"]:
                print("    " + p.value("%-18s" % d["id"]) + p.label("%-24s" % d["name"])
                      + p.muted("%g m, %d shots, %s" % (d["distance_m"], d["shots"],
                                                        d["metric"])))
        return 0
    print("error: no pack %r. List them with 'marksman pack list'." % args.id,
          file=sys.stderr)
    return 2


def _save_pack_cfg(db: Database, **changes) -> None:
    cfg = db.settings.get("packs")
    if not isinstance(cfg, dict):
        cfg = {}
    cfg.update(changes)
    db.settings["packs"] = cfg
    db.save()
    packs_mod.reset()


def cmd_pack_enable(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)
    known = dict((r["id"], r) for r in packs_mod.status(db))
    pid = args.id.strip().lower()
    if pid not in known:
        print("error: no pack %r." % args.id, file=sys.stderr)
        return 2
    cfg = packs_mod.settings(db)
    disabled = [x for x in cfg["disabled"] if x != pid]
    _save_pack_cfg(db, disabled=disabled)

    row = known[pid]
    if row["sensitivity"] > cfg["max_sensitivity"]:
        print(p.bad("Switched on, but still not loading."))
        print(p.muted("  %s declares sensitivity ") % row["name"]
              + _sens(p, row["sensitivity"])
              + p.muted(", above your ceiling of %d." % cfg["max_sensitivity"]))
        print(p.muted("  Raise it deliberately with: ")
              + p.value("marksman pack ceiling %d" % row["sensitivity"]))
        return 0
    print(p.good("Enabled ") + p.value(row["name"]))
    return 0


def cmd_pack_disable(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)
    pid = args.id.strip().lower()
    cfg = packs_mod.settings(db)
    if pid not in cfg["disabled"]:
        _save_pack_cfg(db, disabled=cfg["disabled"] + [pid])
    print(p.good("Disabled ") + p.value(pid)
          + p.muted(" -- its drills and faces are no longer offered."))
    print(p.muted("Sessions you already logged against them are untouched."))
    return 0


def cmd_pack_ceiling(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)
    if args.level is None:
        cfg = packs_mod.settings(db)
        print(p.title("Sensitivity ceiling: ") + _sens(p, cfg["max_sensitivity"]))
        print()
        for lvl in sorted(packs_mod.SENSITIVITY):
            name, blurb = packs_mod.SENSITIVITY[lvl]
            print("  " + _sens(p, lvl) + p.muted("  " + blurb))
        print()
        print(p.muted("Packs above the ceiling are found but not loaded."))
        print(p.muted("Raise it with: ") + p.value("marksman pack ceiling <1-5>"))
        return 0
    if args.level not in packs_mod.SENSITIVITY:
        print("error: ceiling must be 1-5.", file=sys.stderr)
        return 2
    _save_pack_cfg(db, max_sensitivity=args.level)
    print(p.good("Ceiling set to ") + _sens(p, args.level))
    if args.level >= 3:
        print()
        print(p.bad("You are now loading packs whose subject may be regulated "
                    "where you live."))
        print(_wrap(
            "Packs are content written by whoever wrote them -- not by this app, "
            "and not checked by it. Their drills, distances and standards are "
            "somebody's opinion. You are responsible for obeying the law and for "
            "handling anything you own safely.", indent="  "))
    now = [r["name"] for r in packs_mod.status(db) if r["active"]]
    print()
    print(p.muted("Active packs: ") + p.value(", ".join(now) or "(none)"))
    return 0


def cmd_pack_install(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    p = _make_painter(args, db)
    try:
        pack = packs_mod.install(args.path, db)
    except (OSError, ValueError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    print(p.good("Installed ") + p.value(pack["name"])
          + p.muted(" [%s] -- %d drills, %d faces"
                    % (pack["id"], len(pack["drills"]), len(pack["targets"]))))
    print(p.muted("  Sensitivity: ") + _sens(p, pack["sensitivity"]))
    cfg = packs_mod.settings(db)
    if pack["sensitivity"] > cfg["max_sensitivity"]:
        print(p.bad("  Not loading yet: above your ceiling of %d."
                    % cfg["max_sensitivity"]))
        print(p.muted("  Allow it with: ")
              + p.value("marksman pack ceiling %d" % pack["sensitivity"]))
    if pack.get("safety"):
        print()
        print(p.bad("  Safety (from the pack author)"))
        print(_wrap(pack["safety"], indent="    "))
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
        description="Progress tracker for marksmanship with whatever you "
                    "shoot: analyse "
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
                    help="grouping category; 'marksman pack show' lists the "
                         "ones your packs offer")
    wa.add_argument("--projectile", default="",
                    help="free text, e.g. '6mm 0.25g' or 'Elite dart'")
    wa.add_argument("--projectile-mm", type=float, dest="projectile_mm",
                    help="projectile diameter in mm (improves scoring)")
    wa.add_argument("--powered", action="store_true", dest="is_powered",
                    help="gas / battery / air driven rather than manual")
    wa.add_argument("--click", dest="click",
                    help="one click of this tool's sight, as written on the "
                         "turret: '0.1mrad', '1/4moa', '0.25moa'. Turns a zero "
                         "error into clicks to dial.")
    wa.add_argument("--notes", default="")
    wa.set_defaults(func=cmd_tool_add)
    ws = wsub.add_parser("set", help="change an existing tool")
    ws.add_argument("--id", required=True, help="tool id or name")
    ws.add_argument("--name")
    ws.add_argument("--category")
    ws.add_argument("--projectile")
    ws.add_argument("--projectile-mm", type=float, dest="projectile_mm")
    ws.add_argument("--powered", dest="is_powered", action="store_true",
                    default=None)
    ws.add_argument("--manual", dest="is_powered", action="store_false")
    ws.add_argument("--click", help="sight click value ('0.1mrad', '1/4moa'); "
                                    "pass '' to clear it")
    ws.add_argument("--notes")
    ws.set_defaults(func=cmd_tool_set)

    wl = wsub.add_parser("list", help="list tools")
    wl.set_defaults(func=cmd_tool_list)

    # targets
    tp = sub.add_parser("targets",
                        help="list target faces and printable drill sheets")
    tp.add_argument("--print", dest="print_face", metavar="FACE",
                    help="write a true-scale printable page and open it: a "
                         "face by name, or a drill sheet like 'dots-15', "
                         "'bulls-40', 'grid-10', 'face-120'")
    tp.add_argument("--paper", default="a4",
                    help="paper for --print: a4, letter, a3, a5, legal, "
                         "tabloid, or a custom 'WxH' in mm (default: a4)")
    tp.add_argument("--distance", type=float,
                    help="note this distance on the printed sheet")
    tp.add_argument("-o", "--out", help="write the page here (default: "
                                        "<face-name>.html in this folder)")
    tp.add_argument("--no-open", action="store_true",
                    help="just write the file, do not open a browser")
    tp.set_defaults(func=cmd_targets)

    # analyze
    ap = sub.add_parser("analyze", help="analyse a target (image or coordinates)")
    ap.add_argument("--tool", required=True, help="tool id or name")
    ap.add_argument("--target", help="target face name (enables scoring)")
    ap.add_argument("--drill", help="log this as a named drill attempt "
                                    "(see 'marksman drill'); supplies the "
                                    "distance and target face")
    ap.add_argument("--distance", type=float, help="distance in metres")
    ap.add_argument("--date", help="ISO date (default: today)")
    ap.add_argument("--projectiles", default="",
                    help="what was loaded that day, free text")
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

    # drills (named practice drills with tiered standards)
    dp = sub.add_parser("drill", help="practice drills with tiered standards")
    dp.add_argument("--family", help="only this family (e.g. Precision)")
    dp.set_defaults(func=cmd_drill)
    dsub = dp.add_subparsers(dest="drill_action")
    dl = dsub.add_parser("list", help="list drills and your tier on each")
    dl.add_argument("--family", help="only this family")
    dl.set_defaults(func=cmd_drill_list)
    ds = dsub.add_parser("show", help="how to run a drill, and its standards")
    ds.add_argument("id", help="drill id (see 'marksman drill')")
    ds.set_defaults(func=cmd_drill_show)
    dpl = dsub.add_parser("plan", help="today's recommended drills")
    dpl.add_argument("--count", type=int, default=3, help="how many (default 3)")
    dpl.set_defaults(func=cmd_drill_plan)

    # pack (equipment packs: the content layer)
    kp = sub.add_parser("pack", help="equipment packs (drills, faces, categories)")
    ksub = kp.add_subparsers(dest="pack_command")
    kp.set_defaults(func=cmd_pack)
    kl = ksub.add_parser("list", help="list installed packs")
    kl.set_defaults(func=cmd_pack_list)
    ks = ksub.add_parser("show", help="everything in one pack")
    ks.add_argument("id")
    ks.set_defaults(func=cmd_pack_show)
    ke = ksub.add_parser("enable", help="switch a pack back on")
    ke.add_argument("id")
    ke.set_defaults(func=cmd_pack_enable)
    kd = ksub.add_parser("disable", help="switch a pack off")
    kd.add_argument("id")
    kd.set_defaults(func=cmd_pack_disable)
    kc = ksub.add_parser("ceiling",
                         help="the sensitivity level you allow packs to reach")
    kc.add_argument("level", nargs="?", type=int,
                    help="1-5; omit to see the ladder")
    kc.set_defaults(func=cmd_pack_ceiling)
    ki = ksub.add_parser("install", help="install a pack JSON file")
    ki.add_argument("path")
    ki.set_defaults(func=cmd_pack_install)

    # gui (desktop window)
    up = sub.add_parser("gui", help="open the desktop app (tkinter)")
    up.set_defaults(func=cmd_gui)

    # web (phone / browser app on the LAN)
    wb = sub.add_parser("web", help="serve the app to your phone's browser "
                                    "(same Wi-Fi)")
    wb.add_argument("--host", default="", help="bind address (default: all "
                                               "interfaces)")
    wb.add_argument("--port", type=int, default=8317, help="port (default 8317)")
    wb.add_argument("--new-key", action="store_true", dest="new_key",
                    help="issue a fresh access key (invalidates existing links)")
    wb.set_defaults(func=cmd_web)

    # import (merge another database or a Drive backup)
    ip = sub.add_parser("import", help="merge another Marksman database or "
                                       "Drive backup into this one")
    ip.add_argument("file", help="the other .json database or backup")
    ip.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="report what would be added, write nothing")
    ip.set_defaults(func=cmd_import)

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
    # Content (drills, target faces, categories) comes from the equipment packs
    # this database allows, so load them before any command runs.
    try:
        packs_mod.load(Database.load(args.db))
    except (OSError, ValueError):
        packs_mod.load(None)          # unreadable db: fall back to the defaults
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
