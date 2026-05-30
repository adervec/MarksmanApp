"""Command-line interface for Marksman.

Examples
--------
    # Register a weapon
    marksman weapon add --id ap1 --name "Walther LP500" \
        --category "Air Pistol" --caliber 4.5mm --caliber-mm 4.5 --airgun

    # Analyse a marked-up target image (red marker dots), score it, save it
    marksman analyze --weapon ap1 --target "ISSF 10m Air Pistol" --distance 10 \
        --image shots.png --color red --auto-center

    # Or enter shot coordinates by hand (millimetres from point of aim)
    marksman analyze --weapon ap1 --target "ISSF 10m Air Pistol" --distance 10 \
        --shots "1.2,3.4  -2.0,5.1  0.5,-1.0"

    # Track progress
    marksman progress                 # overall
    marksman progress --by-category
    marksman progress --by-weapon
    marksman progress --weapon ap1 --sessions
"""

from __future__ import annotations

import argparse
import sys
import uuid
from typing import List, Optional, Tuple

from .models import Shot, Weapon, Session, TargetSpec
from .grouping import analyze_group
from . import targets as targets_mod
from .storage import Database, DEFAULT_DB_PATH
from . import tracker
from . import report


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


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_weapon_add(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    wid = args.id or uuid.uuid4().hex[:8]
    weapon = Weapon(
        id=wid,
        name=args.name,
        category=args.category,
        caliber=args.caliber or "",
        is_airgun=args.airgun,
        caliber_mm=args.caliber_mm,
        notes=args.notes or "",
    )
    db.add_weapon(weapon)
    db.save()
    print("Added weapon %s: %s [%s]" % (weapon.id, weapon.name, weapon.category))
    return 0


def cmd_weapon_list(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    if not db.weapons:
        print("No weapons yet. Add one with: marksman weapon add --name ...")
        return 0
    print("%-12s %-28s %-18s %-10s SESSIONS" % ("ID", "NAME", "CATEGORY", "CALIBER"))
    for w in sorted(db.weapons.values(), key=lambda x: x.name.lower()):
        n = len(db.sessions_for_weapon(w.id))
        print("%-12s %-28s %-18s %-10s %d" % (w.id, w.name, w.category, w.caliber, n))
    return 0


def cmd_targets(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    names = set(targets_mod.list_targets()) | set(t.name for t in db.custom_targets.values())
    print("Available targets:")
    for name in sorted(names):
        try:
            spec = resolve_target(name, db)
        except KeyError:
            continue
        deci = " (decimal)" if spec.decimal_scoring else ""
        print("  %s%s: 10-ring %.1f mm, outer %.0f mm, max %d"
              % (spec.name, deci, spec.ten_ring_radius_mm * 2,
                 spec.outer_radius_mm * 2, spec.max_value))
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    db = Database.load(args.db)

    weapon = db.find_weapon(args.weapon)
    if weapon is None:
        print("error: no weapon matching %r. List with 'marksman weapon list'."
              % args.weapon, file=sys.stderr)
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

    caliber_mm = weapon.caliber_mm or 0.0
    stats = analyze_group(shots, target=target, caliber_mm=caliber_mm)

    date = args.date or Session.today_iso()
    session = Session(
        id=args.id or _new_session_id(date),
        weapon_id=weapon.id,
        date=date,
        shots=shots,
        stats=stats,
        distance_m=args.distance,
        target_name=target.name if target else "",
        ammo=args.ammo or "",
        image_path=image_path,
        notes=args.notes or "",
    )

    print("Weapon : %s [%s]" % (weapon.name, weapon.category))
    print(detection_note)
    print()
    print(report.format_group_stats(stats, session.target_name, args.distance))

    if not args.no_save:
        db.add_session(session)
        db.save()
        print()
        print("Saved session %s (%d for this weapon)."
              % (session.id, len(db.sessions_for_weapon(weapon.id))))
    return 0


def cmd_progress(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    sessions = db.all_sessions()
    if not sessions:
        print("No sessions yet. Analyse a target with 'marksman analyze'.")
        return 0

    if args.weapon:
        weapon = db.find_weapon(args.weapon)
        if weapon is None:
            print("error: no weapon matching %r." % args.weapon, file=sys.stderr)
            return 2
        rep = tracker.build_report(
            db.sessions_for_weapon(weapon.id), "weapon", weapon.name)
        print(report.format_progress(rep, show_sessions=args.sessions))
        return 0

    if args.category:
        rep = tracker.build_report(
            db.sessions_for_category(args.category), "category", args.category)
        print(report.format_progress(rep, show_sessions=args.sessions))
        return 0

    show_overall = args.all or not (args.by_category or args.by_weapon)

    if show_overall:
        rep = tracker.progress_overall(sessions)
        print(report.format_progress(rep, show_sessions=args.sessions))

    if args.by_category or args.all:
        for rep in tracker.progress_by_category(sessions, db.weapons).values():
            print()
            print(report.format_progress(rep, show_sessions=args.sessions))

    if args.by_weapon or args.all:
        for rep in tracker.progress_by_weapon(sessions, db.weapons).values():
            print()
            print(report.format_progress(rep, show_sessions=args.sessions))
    return 0


def cmd_sessions(args: argparse.Namespace) -> int:
    db = Database.load(args.db)
    sessions = sorted(db.all_sessions(), key=lambda s: (s.date, s.id))
    if not sessions:
        print("No sessions yet.")
        return 0
    print("%-12s %-22s %-24s %5s %8s %8s"
          % ("DATE", "WEAPON", "TARGET", "SHOTS", "ES(mm)", "SCORE"))
    for s in sessions:
        w = db.get_weapon(s.weapon_id)
        wname = w.name if w else s.weapon_id
        es = ("%.1f" % s.stats.extreme_spread_mm) if s.stats else "-"
        score = ("%.0f" % s.stats.total_score) if (s.stats and s.stats.total_score is not None) else "-"
        print("%-12s %-22.22s %-24.24s %5d %8s %8s"
              % (s.date, wname, s.target_name, len(s.shots), es, score))
    return 0


# --------------------------------------------------------------------------- #
# Argument parser
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="marksman",
        description="Progress tracker for shooting sports: analyse marked-up "
                    "target images and track progress over time.",
    )
    p.add_argument("--db", default=DEFAULT_DB_PATH,
                   help="database file (default: %s)" % DEFAULT_DB_PATH)
    sub = p.add_subparsers(dest="command")
    sub.required = True

    # weapon
    wp = sub.add_parser("weapon", help="manage weapons")
    wsub = wp.add_subparsers(dest="weapon_command")
    wsub.required = True
    wa = wsub.add_parser("add", help="add a weapon")
    wa.add_argument("--id", help="short id (auto if omitted)")
    wa.add_argument("--name", required=True)
    wa.add_argument("--category", default="Other",
                    help="e.g. 'Air Pistol', 'Centerfire Rifle'")
    wa.add_argument("--caliber", default="", help="free text, e.g. '.22 LR'")
    wa.add_argument("--caliber-mm", type=float, dest="caliber_mm",
                    help="projectile diameter in mm (improves scoring)")
    wa.add_argument("--airgun", action="store_true")
    wa.add_argument("--notes", default="")
    wa.set_defaults(func=cmd_weapon_add)
    wl = wsub.add_parser("list", help="list weapons")
    wl.set_defaults(func=cmd_weapon_list)

    # targets
    tp = sub.add_parser("targets", help="list known target faces")
    tp.set_defaults(func=cmd_targets)

    # analyze
    ap = sub.add_parser("analyze", help="analyse a target (image or coordinates)")
    ap.add_argument("--weapon", required=True, help="weapon id or name")
    ap.add_argument("--target", help="target face name (enables scoring)")
    ap.add_argument("--distance", type=float, help="distance in metres")
    ap.add_argument("--date", help="ISO date (default: today)")
    ap.add_argument("--ammo", default="")
    ap.add_argument("--notes", default="")
    ap.add_argument("--id", help="session id (auto if omitted)")
    ap.add_argument("--no-save", action="store_true", help="analyse without saving")
    # input
    ap.add_argument("--shots", help="manual shots 'x,y x,y' in mm from POA")
    ap.add_argument("--image", help="path to a marked-up target image (PNG)")
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
    pp.add_argument("--by-weapon", action="store_true", dest="by_weapon")
    pp.add_argument("--weapon", help="single weapon id or name")
    pp.add_argument("--category", help="single category")
    pp.add_argument("--all", action="store_true",
                    help="overall + all categories + all weapons")
    pp.add_argument("--sessions", action="store_true", help="list each session")
    pp.set_defaults(func=cmd_progress)

    # sessions
    sp = sub.add_parser("sessions", help="list saved sessions")
    sp.set_defaults(func=cmd_sessions)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
