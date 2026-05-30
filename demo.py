#!/usr/bin/env python
"""End-to-end demonstration of Marksman.

Synthesises two marked-up target images (red dots where shots landed), runs the
real image-analysis pipeline on them, scores them, stores them as dated
sessions for one weapon, and prints the progress report -- proving the whole
core feature works with zero third-party dependencies.

    python demo.py
"""

import os
import tempfile

from marksman import imageio, vision
from marksman.grouping import analyze_group
from marksman.models import Weapon, Session
from marksman.storage import Database
from marksman.targets import get_target
from marksman import tracker, report


FACE_MM = 170.0          # ISSF 10m air pistol face is ~170 mm across
IMG_PX = 700             # rendered image width/height in pixels
DOT_R = 4                # radius (px) of each red marker dot
MM_PER_PX = FACE_MM / IMG_PX
CENTER = (IMG_PX / 2.0, IMG_PX / 2.0)


def mm_to_px(x_mm, y_mm):
    cx, cy = CENTER
    return (int(round(cx + x_mm / MM_PER_PX)),
            int(round(cy - y_mm / MM_PER_PX)))   # y up -> image y down


def render_target(shots_mm, path):
    """Draw a white target face with a faint bull and a red dot per shot."""
    img = imageio.Image(IMG_PX, IMG_PX, bytearray([255] * (IMG_PX * IMG_PX * 3)))
    # faint grey aiming bull so the picture looks like a target
    _disk(img, int(CENTER[0]), int(CENTER[1]), 40, (205, 205, 205))
    for (x_mm, y_mm) in shots_mm:
        px, py = mm_to_px(x_mm, y_mm)
        _disk(img, px, py, DOT_R, (210, 25, 25))     # red marker
    imageio.save_png(path, img)


def _disk(img, cx, cy, r, color):
    rr = r * r
    for y in range(cy - r, cy + r + 1):
        if 0 <= y < img.height:
            for x in range(cx - r, cx + r + 1):
                if 0 <= x < img.width and (x - cx) ** 2 + (y - cy) ** 2 <= rr:
                    img.set(x, y, *color)


def analyse_and_store(db, weapon, date, shots_mm, target, path):
    render_target(shots_mm, path)
    result = vision.analyze_image(
        path, mode="marker", color="red",
        center_px=CENTER, mm_per_px=MM_PER_PX,
    )
    stats = analyze_group(result.shots, target=target,
                          caliber_mm=weapon.caliber_mm or 0.0)
    session = Session(
        id=date, weapon_id=weapon.id, date=date, shots=result.shots,
        stats=stats, distance_m=10.0, target_name=target.name, image_path=path,
    )
    db.add_session(session)
    print("=== %s : detected %d shots from %s ===" % (
        date, len(result.shots), os.path.basename(path)))
    print(report.format_group_stats(stats, target.name, 10.0))
    print()
    return session


def main():
    workdir = tempfile.mkdtemp(prefix="marksman_demo_")
    db = Database(path=os.path.join(workdir, "demo_data.json"))
    weapon = Weapon(id="ap1", name="Walther LP500", category="Air Pistol",
                    caliber="4.5mm", is_airgun=True, caliber_mm=4.5)
    db.add_weapon(weapon)
    target = get_target("ISSF 10m Air Pistol")

    # Session 1: a loose, high-right group (early days) -- ~17 mm, off-centre.
    s1 = [(8, 12), (15, 6), (4, 18), (12, 14), (18, 9)]
    # Session 2, weeks later: tighter (~10 mm) and well centred (improvement!).
    s2 = [(0, 5), (5, 0), (0, -5), (-5, 0), (0, 0)]

    analyse_and_store(db, weapon, "2026-03-01", s1, target,
                      os.path.join(workdir, "session1.png"))
    analyse_and_store(db, weapon, "2026-04-15", s2, target,
                      os.path.join(workdir, "session2.png"))
    db.save()

    print("#" * 70)
    print("PROGRESS")
    print("#" * 70)
    print()
    print(report.format_progress(tracker.progress_overall(db.all_sessions()),
                                 show_sessions=True))
    print()
    for rep in tracker.progress_by_weapon(db.all_sessions(), db.weapons).values():
        print(report.format_progress(rep))
    print()
    print("Data saved at: %s" % db.path)


if __name__ == "__main__":
    main()
