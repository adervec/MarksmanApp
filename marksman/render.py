"""Render a graphical *recreation* of a session from its stored data.

Every session keeps the shot coordinates (millimetres from the point of aim)
and the name of the target they were scored on.  That is enough to redraw a
clean diagram of the result -- the target rings with the shots plotted on them,
the point of aim, and the group centre -- **without** the original photo or
video.

This is the property that lets the bulky source media be deleted to save space
while the visual result is preserved: the recreation is a pure function of the
coordinate data, which is never thrown away (see ``marksman cleanup``).

Pure standard library: it draws straight into :class:`marksman.imageio.Image`
and writes a PNG.

The same module also emits :func:`target_html` -- a *printable* target face at
true physical scale, so the rings you shoot at are the rings the app scores
against.
"""

from __future__ import annotations

import math
import os
import re
from html import escape
from typing import List, Optional, Tuple

from . import imageio
from .models import Session, TargetSpec

# Palette -- a classic black-bull paper target on off-white.
_BG = (252, 250, 246)
_BLACK = (26, 26, 26)
_RINGLINE_ON_WHITE = (70, 70, 70)
_RINGLINE_ON_BLACK = (232, 232, 232)
_POA = (150, 150, 150)
_SHOT = (206, 32, 32)
_SHOT_EDGE = (252, 250, 246)     # thin light rim so shots read on the black
_GROUP = (38, 96, 208)

DEFAULT_SIZE_PX = 720
DEFAULT_MARGIN_PX = 26


# --------------------------------------------------------------------------- #
# Low-level drawing
# --------------------------------------------------------------------------- #

def _fill_disk(img: imageio.Image, cx: int, cy: int, r: float,
               color: Tuple[int, int, int]) -> None:
    ri = max(0, int(round(r)))
    rr = (r + 0.5) * (r + 0.5)
    for y in range(max(0, cy - ri), min(img.height - 1, cy + ri) + 1):
        dy = y - cy
        for x in range(max(0, cx - ri), min(img.width - 1, cx + ri) + 1):
            dx = x - cx
            if dx * dx + dy * dy <= rr:
                img.set(x, y, *color)


def _ring(img: imageio.Image, cx: int, cy: int, r: float,
          color: Tuple[int, int, int], thickness: float = 1.6) -> None:
    if r < 0.5:
        return
    half = thickness / 2.0 + 0.5
    lo, hi = max(0.0, r - half), r + half
    lo2, hi2 = lo * lo, hi * hi
    ri = int(math.ceil(hi))
    for y in range(max(0, cy - ri), min(img.height - 1, cy + ri) + 1):
        dy = y - cy
        for x in range(max(0, cx - ri), min(img.width - 1, cx + ri) + 1):
            dx = x - cx
            d2 = dx * dx + dy * dy
            if lo2 <= d2 <= hi2:
                img.set(x, y, *color)


def _vline(img: imageio.Image, x: int, y0: int, y1: int,
           color: Tuple[int, int, int]) -> None:
    if not (0 <= x < img.width):
        return
    for y in range(max(0, min(y0, y1)), min(img.height - 1, max(y0, y1)) + 1):
        img.set(x, y, *color)


def _hline(img: imageio.Image, y: int, x0: int, x1: int,
           color: Tuple[int, int, int]) -> None:
    if not (0 <= y < img.height):
        return
    for x in range(max(0, min(x0, x1)), min(img.width - 1, max(x0, x1)) + 1):
        img.set(x, y, *color)


# --------------------------------------------------------------------------- #
# Recreation
# --------------------------------------------------------------------------- #

def _black_radius_mm(target: TargetSpec) -> float:
    """Radius of the black aiming area.

    On bullseye-style faces the black covers the higher-value rings; we approximate
    it by the outer edge of the 7-ring, falling back to ~45 % of the outer
    radius when there is no 7-ring.
    """
    for ring in target.rings:
        if ring.value == 7:
            return ring.radius_mm
    return 0.45 * target.outer_radius_mm


def render_session(session: Session, target: Optional[TargetSpec] = None,
                   size_px: int = DEFAULT_SIZE_PX,
                   margin_px: int = DEFAULT_MARGIN_PX,
                   shot_radius_mm: Optional[float] = None,
                   show_group: bool = True) -> imageio.Image:
    """Draw a recreation of ``session`` and return the :class:`Image`.

    ``target`` supplies the scoring rings (if known); without it the shots are
    still plotted against the point of aim.  ``shot_radius_mm`` sizes the shot
    marks to the projectile calibre when available.
    """
    size = max(160, int(size_px))
    cx = cy = size // 2

    # Physical half-extent we must fit: the target face and every shot.
    extents = [1.0]
    if target is not None and target.outer_radius_mm > 0:
        extents.append(target.outer_radius_mm)
    for s in session.shots:
        extents.append(math.hypot(s.x_mm, s.y_mm))
    half_mm = max(extents) * 1.08
    usable_px = (size / 2.0) - margin_px
    mm_per_px = (half_mm / usable_px) if usable_px > 0 else 1.0
    if mm_per_px <= 0:
        mm_per_px = 1.0

    def to_px(x_mm: float, y_mm: float) -> Tuple[int, int]:
        return (int(round(cx + x_mm / mm_per_px)),
                int(round(cy - y_mm / mm_per_px)))   # +y is up

    img = imageio.Image(size, size, bytearray(bytes(_BG) * (size * size)))

    # Rings (and black bull) ------------------------------------------------ #
    if target is not None and target.rings:
        black_r_mm = _black_radius_mm(target)
        if black_r_mm > 0:
            _fill_disk(img, cx, cy, black_r_mm / mm_per_px, _BLACK)
        for ring in target.rings:
            inside_black = ring.radius_mm <= black_r_mm + 1e-6
            color = _RINGLINE_ON_BLACK if inside_black else _RINGLINE_ON_WHITE
            _ring(img, cx, cy, ring.radius_mm / mm_per_px, color)

    # Point of aim crosshair ------------------------------------------------ #
    cross = max(6, size // 36)
    _vline(img, cx, cy - cross, cy + cross, _POA)
    _hline(img, cy, cx - cross, cx + cross, _POA)

    # Group centre + extreme-spread circle ---------------------------------- #
    if show_group and session.shots:
        gx = sum(s.x_mm for s in session.shots) / len(session.shots)
        gy = sum(s.y_mm for s in session.shots) / len(session.shots)
        gpx, gpy = to_px(gx, gy)
        es = session.stats.extreme_spread_mm if session.stats else None
        if es:
            _ring(img, gpx, gpy, (es / 2.0) / mm_per_px, _GROUP, thickness=1.0)
        _vline(img, gpx, gpy - 7, gpy + 7, _GROUP)
        _hline(img, gpy, gpx - 7, gpx + 7, _GROUP)

    # Shots ----------------------------------------------------------------- #
    if shot_radius_mm and shot_radius_mm > 0:
        r_px = max(2.0, shot_radius_mm / mm_per_px)
    else:
        r_px = max(3.0, size / 170.0)
    for s in session.shots:
        px, py = to_px(s.x_mm, s.y_mm)
        _fill_disk(img, px, py, r_px + 1.4, _SHOT_EDGE)
        _fill_disk(img, px, py, r_px, _SHOT)

    return img


def save_recreation(session: Session, path: str,
                    target: Optional[TargetSpec] = None, **kwargs) -> str:
    """Render ``session`` and write it as a PNG at ``path``; return ``path``."""
    img = render_session(session, target=target, **kwargs)
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    imageio.save_png(path, img)
    return path


# --------------------------------------------------------------------------- #
# Printable target faces
#
# The app scores against rings of an exact physical size, so you need a face
# printed at exactly that size.  This emits SVG inside an HTML page rather than
# a PNG: SVG in millimetre units prints at true scale in any browser, carries
# text (ring numbers, the calibration ruler) without this project needing a
# font, and hands the print dialog -- and "save as PDF" -- to the OS for free.
#
# ponytail: browser print instead of a PDF writer; add one if a headless
# server ever needs to produce PDFs without a browser.
# --------------------------------------------------------------------------- #

PAPER_MM = {
    "a3": (297.0, 420.0),
    "a4": (210.0, 297.0),
    "a5": (148.0, 210.0),
    "letter": (215.9, 279.4),
    "legal": (215.9, 355.6),
    "tabloid": (279.4, 431.8),
}

_PAGE_MARGIN_MM = 8.0        # printer-safe edge
_FOOTER_MM = 15.0            # reserved strip for the ruler and sheet label
_MAX_SHEETS = 40             # a bad face must not emit a thousand pages


def paper_size(name: str) -> Tuple[float, float]:
    """Paper size in mm as ``(width, height)``.

    Accepts a known name (``a4``, ``letter``, ...) or a custom ``WxH`` in mm.
    """
    key = str(name or "a4").strip().lower()
    if key in PAPER_MM:
        return PAPER_MM[key]
    match = re.match(r"^(\d+(?:\.\d+)?)\s*[x*]\s*(\d+(?:\.\d+)?)$", key)
    if not match:
        raise KeyError("Unknown paper %r. Known: %s -- or a custom size like "
                       "'200x250' (mm)."
                       % (name, ", ".join(sorted(PAPER_MM))))
    width, height = float(match.group(1)), float(match.group(2))
    if not (40.0 <= width <= 2000.0 and 40.0 <= height <= 2000.0):
        raise KeyError("Paper sides must be between 40 and 2000 mm.")
    return (width, height)


def _svg_rings(target: TargetSpec, cx: float, cy: float) -> List[str]:
    """The face itself, drawn in millimetre coordinates."""
    rings = sorted(target.rings, key=lambda r: r.radius_mm, reverse=True)
    black_r = _black_radius_mm(target)
    out = ["<circle cx='%.3f' cy='%.3f' r='%.3f' fill='#fff' stroke='#111' "
           "stroke-width='0.35'/>" % (cx, cy, rings[0].radius_mm) if rings else ""]
    if black_r > 0:
        out.append("<circle cx='%.3f' cy='%.3f' r='%.3f' fill='#111'/>"
                   % (cx, cy, black_r))
    for i, ring in enumerate(rings):
        on_black = ring.radius_mm <= black_r + 1e-6
        out.append("<circle cx='%.3f' cy='%.3f' r='%.3f' fill='none' "
                   "stroke='%s' stroke-width='0.35'/>"
                   % (cx, cy, ring.radius_mm, "#fff" if on_black else "#111"))
        # Ring value, on the horizontal centre line just inside its own edge.
        inner = rings[i + 1].radius_mm if i + 1 < len(rings) else 0.0
        band = ring.radius_mm - inner
        if band < 3.0 or ring.radius_mm < 6.0:
            continue                      # no room to read a number
        size = min(band * 0.62, 6.0)
        pos = ring.radius_mm - band / 2.0
        fill = "#fff" if pos <= black_r else "#111"
        for sign in (-1, 1):
            out.append("<text x='%.3f' y='%.3f' font-size='%.2f' fill='%s' "
                       "text-anchor='middle' dominant-baseline='central' "
                       "font-family='Helvetica,Arial,sans-serif'>%d</text>"
                       % (cx + sign * pos, cy, size, fill, ring.value))
    # Aiming cross, hairline so it never hides a hit.
    arm = max(2.0, min(6.0, target.outer_radius_mm * 0.06))
    out.append("<path d='M%.3f %.3f h%.3f M%.3f %.3f v%.3f' stroke='#e33' "
               "stroke-width='0.25' fill='none'/>"
               % (cx - arm, cy, arm * 2, cx, cy - arm, arm * 2))
    return [s for s in out if s]


def _svg_footer(page_w: float, page_h: float, caption: str,
                sheet: str) -> List[str]:
    """Calibration ruler + labels, drawn in page coordinates."""
    y = page_h - _FOOTER_MM + 6.0
    usable = page_w - 2 * _PAGE_MARGIN_MM
    # Leave room for the label beside it, or the instruction runs off the page.
    length = 100.0 if usable >= 150.0 else 50.0
    x0 = _PAGE_MARGIN_MM
    out = ["<path d='M%.3f %.3f h%.3f' stroke='#111' stroke-width='0.3'/>"
           % (x0, y, length)]
    tick = 0.0
    while tick <= length + 1e-6:                    # 10 mm ticks
        high = tick in (0.0, length)
        out.append("<path d='M%.3f %.3f v%.3f' stroke='#111' "
                   "stroke-width='0.3'/>" % (x0 + tick, y, -3.5 if high else -2.0))
        tick += 10.0
    out.append("<text x='%.3f' y='%.3f' font-size='2.8' fill='#111' "
               "font-family='Helvetica,Arial,sans-serif'>"
               "%d mm -- if not, reprint at 100%%</text>"
               % (x0 + length + 3.0, y, int(length)))
    out.append("<text x='%.3f' y='%.3f' font-size='3.2' fill='#555' "
               "font-family='Helvetica,Arial,sans-serif'>%s</text>"
               % (x0, y + 5.5, escape(caption)))
    out.append("<text x='%.3f' y='%.3f' font-size='3.2' fill='#555' "
               "text-anchor='end' font-family='Helvetica,Arial,sans-serif'>"
               "%s</text>" % (page_w - _PAGE_MARGIN_MM, y + 5.5, escape(sheet)))
    return out


def target_html(target: TargetSpec, distance_m: Optional[float] = None,
                paper: str = "a4") -> str:
    """A printable, true-scale page (or tiled pages) for ``target``.

    Open it in a browser and print at 100% -- the ruler at the foot of every
    sheet proves the scale came out right.  Faces too big for the paper are
    split into sheets with alignment marks to tape together.
    """
    page_w, page_h = paper_size(paper)
    extent = max(2.0 * target.outer_radius_mm,
                 target.face_width_mm or 0.0) or 100.0
    usable_w = page_w - 2 * _PAGE_MARGIN_MM
    usable_h = page_h - 2 * _PAGE_MARGIN_MM - _FOOTER_MM
    cols = max(1, int(math.ceil(extent / usable_w - 1e-9)))
    rows = max(1, int(math.ceil(extent / usable_h - 1e-9)))
    if cols * rows > _MAX_SHEETS:
        raise ValueError(
            "A %.0f mm face needs %d sheets of %s. Print it on bigger paper "
            "(--paper a3) or pick a smaller face."
            % (extent, cols * rows, paper))

    # Centre the face inside the whole tiled area, then each sheet is just a
    # shifted window onto the same drawing.
    body = "".join(_svg_rings(target, cols * usable_w / 2.0,
                              rows * usable_h / 2.0))
    dist = (" at %g m" % distance_m) if distance_m else ""
    caption = "%s -- %.0f mm face%s" % (target.name, extent, dist)

    sheets = []
    for row in range(rows):
        for col in range(cols):
            idx = row * cols + col + 1
            label = ("sheet %d of %d (col %d, row %d)"
                     % (idx, cols * rows, col + 1, row + 1)
                     if cols * rows > 1 else "Marksman -- practice face, not "
                     "an official target")
            marks = ""
            if cols * rows > 1:                      # corner alignment ticks
                for mx in (_PAGE_MARGIN_MM, _PAGE_MARGIN_MM + usable_w):
                    for my in (_PAGE_MARGIN_MM, _PAGE_MARGIN_MM + usable_h):
                        marks += ("<path d='M%.3f %.3f h4 M%.3f %.3f v4' "
                                  "stroke='#999' stroke-width='0.2'/>"
                                  % (mx - 2, my, mx, my - 2))
            sheets.append(
                "<div class='sheet'><svg xmlns='http://www.w3.org/2000/svg' "
                "width='%.3fmm' height='%.3fmm' viewBox='0 0 %.3f %.3f'>"
                "<clipPath id='clip%d'><rect x='%.3f' y='%.3f' width='%.3f' "
                "height='%.3f'/></clipPath>"
                "<g clip-path='url(#clip%d)'><g transform='translate(%.3f,%.3f)'>"
                "%s</g></g>%s%s</svg></div>"
                % (page_w, page_h, page_w, page_h,
                   idx, _PAGE_MARGIN_MM, _PAGE_MARGIN_MM, usable_w, usable_h,
                   idx, _PAGE_MARGIN_MM - col * usable_w,
                   _PAGE_MARGIN_MM - row * usable_h,
                   body, marks,
                   "".join(_svg_footer(page_w, page_h, caption, label))))

    return _PRINT_PAGE % {
        "title": escape(caption),
        "w": "%.3f" % page_w,
        "h": "%.3f" % page_h,
        "sheets": "".join(sheets),
        "note": escape(
            "%d sheet%s. Print at 100%% (\"actual size\"), never \"fit to "
            "page\", then check the ruler at the foot of each sheet."
            % (cols * rows, "" if cols * rows == 1 else "s, taped together")),
    }


def sheet_html(caption: str, body: str, paper: str = "a4") -> str:
    """One printable sheet of arbitrary SVG drawn in millimetre units.

    ``body``'s origin is the top-left of the printable area -- inside the page
    margins and above the footer strip.  Used by :mod:`marksman.sheets` for
    the drill-sheet catalogue; the ruler and print CSS come along for free.
    """
    page_w, page_h = paper_size(paper)
    sheet = ("<div class='sheet'><svg xmlns='http://www.w3.org/2000/svg' "
             "width='%.3fmm' height='%.3fmm' viewBox='0 0 %.3f %.3f'>"
             "<g transform='translate(%.3f,%.3f)'>%s</g>%s</svg></div>"
             % (page_w, page_h, page_w, page_h,
                _PAGE_MARGIN_MM, _PAGE_MARGIN_MM, body,
                "".join(_svg_footer(page_w, page_h, caption,
                                    "Marksman -- practice sheet, not an "
                                    "official target"))))
    return _PRINT_PAGE % {
        "title": escape(caption),
        "w": "%.3f" % page_w,
        "h": "%.3f" % page_h,
        "sheets": sheet,
        "note": escape('Print at 100% ("actual size"), never "fit to page", '
                       "then check the ruler at the foot of the sheet."),
    }


_PRINT_PAGE = """<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<style>
@page{size:%(w)smm %(h)smm;margin:0}
html,body{margin:0;padding:0;background:#fff;color:#111;
  font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
.sheet{width:%(w)smm;height:%(h)smm;overflow:hidden;page-break-after:always;break-after:page}
.sheet:last-child{page-break-after:auto;break-after:auto}
.bar{padding:12px 16px;background:#111;color:#fff;display:flex;gap:12px;
  align-items:center;flex-wrap:wrap}
.bar button{font:inherit;padding:8px 16px;border:0;border-radius:6px;
  background:#e8a33d;color:#111;font-weight:600;cursor:pointer}
@media screen{body{background:#555}.sheet{background:#fff;margin:12px auto;
  box-shadow:0 2px 10px rgba(0,0,0,.5)}}
@media print{.bar{display:none}}
</style>
<div class="bar"><button onclick="print()">Print</button>
<span>%(title)s</span><span style="opacity:.7">%(note)s</span></div>
%(sheets)s
"""
