"""A parametric catalogue of printable practice sheets.

The scoring faces (:mod:`marksman.targets`) are what the app measures against;
this module is everything else worth pinning to a backstop: multi-bull grids,
dot drills, sight-in graph paper, shrinking ladders, clock drills, tracking
lines, playing-card silhouettes-of-nothing.

Nothing here is stored.  A design is a *family name* plus a *size in
millimetres* -- ``dots-15``, ``bulls-40``, ``grid-25``, ``face-120`` -- so the
catalogue is every family at every size on every paper: thousands of distinct
targets from a page of geometry.  ``face-<mm>`` is special: it builds a real
scored :class:`~marksman.models.TargetSpec` at that diameter and prints through
:func:`marksman.render.target_html`, tiling across sheets when it outgrows the
paper.

All drawing is SVG in millimetre units, handed to :func:`marksman.render`
for the page chrome (calibration ruler, print CSS), so everything prints at
true physical scale.
"""

from __future__ import annotations

import re
from math import cos, pi, sin
from typing import Callable, Dict, List, Tuple

from . import render, targets
from .models import TargetSpec

_INK = "#111"
_FAINT = "#bbb"


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #

def _cells(w: float, h: float, cell_w: float,
           cell_h: float = 0.0) -> List[Tuple[float, float]]:
    """Centres of the largest grid of cell_w x cell_h boxes that fits w x h."""
    cell_h = cell_h or cell_w
    cols = max(1, int(w / cell_w))
    rows = max(1, int(h / cell_h))
    x0 = (w - cols * cell_w) / 2.0
    y0 = (h - rows * cell_h) / 2.0
    return [(x0 + (c + 0.5) * cell_w, y0 + (r + 0.5) * cell_h)
            for r in range(rows) for c in range(cols)]


def _bull(cx: float, cy: float, d: float) -> List[str]:
    """A solid black bull with a white reference ring and centre aim dot."""
    r = d / 2.0
    out = ["<circle cx='%.3f' cy='%.3f' r='%.3f' fill='%s'/>" % (cx, cy, r, _INK)]
    if d >= 12.0:
        out.append("<circle cx='%.3f' cy='%.3f' r='%.3f' fill='none' "
                   "stroke='#fff' stroke-width='0.35'/>" % (cx, cy, r * 0.55))
    out.append("<circle cx='%.3f' cy='%.3f' r='%.3f' fill='#fff'/>"
               % (cx, cy, max(0.6, r * 0.07)))
    return out


# --------------------------------------------------------------------------- #
# The families -- each draws into a w x h mm area, origin top-left
# --------------------------------------------------------------------------- #

def _bulls(w: float, h: float, d: float) -> List[str]:
    out = []
    for cx, cy in _cells(w, h, d * 1.45):
        out += _bull(cx, cy, d)
    return out


def _dots(w: float, h: float, d: float) -> List[str]:
    return ["<circle cx='%.3f' cy='%.3f' r='%.3f' fill='%s'/>"
            % (cx, cy, d / 2.0, _INK)
            for cx, cy in _cells(w, h, max(d * 2.5, 16.0))]


def _squares(w: float, h: float, d: float) -> List[str]:
    out = []
    for cx, cy in _cells(w, h, d * 1.4):
        out.append("<rect x='%.3f' y='%.3f' width='%.3f' height='%.3f' "
                   "fill='none' stroke='%s' stroke-width='0.5'/>"
                   % (cx - d / 2, cy - d / 2, d, d, _INK))
        out.append("<circle cx='%.3f' cy='%.3f' r='0.7' fill='%s'/>"
                   % (cx, cy, _INK))
    return out


def _diamonds(w: float, h: float, d: float) -> List[str]:
    # A square rotated 45 degrees; the grid cell allows for the wider diagonal.
    out = []
    for cx, cy in _cells(w, h, d * 1.9):
        out.append("<rect x='%.3f' y='%.3f' width='%.3f' height='%.3f' "
                   "fill='%s' transform='rotate(45 %.3f %.3f)'/>"
                   % (cx - d / 2, cy - d / 2, d, d, _INK, cx, cy))
        out.append("<circle cx='%.3f' cy='%.3f' r='%.3f' fill='#fff'/>"
                   % (cx, cy, max(0.6, d * 0.05)))
    return out


def _grid(w: float, h: float, s: float) -> List[str]:
    """Sight-in sheet: graph lines every s mm, heavier every fifth, centre
    diamond to aim at.  Walking a group across a known grid is how you count a
    correction without trusting anyone's arithmetic -- including this app's."""
    out = []
    cx, cy = w / 2.0, h / 2.0
    for axis in ("x", "y"):
        span, mid = (w, cx) if axis == "x" else (h, cy)
        n = 0
        pos = mid % s                      # lines land on the centre lines
        while pos <= span + 1e-6:
            heavy = abs(pos - mid) < 1e-6 or (round((pos - mid) / s) % 5 == 0)
            line = ("M%.3f 0 v%.3f" % (pos, h) if axis == "x"
                    else "M0 %.3f h%.3f" % (pos, w))
            out.append("<path d='%s' stroke='%s' stroke-width='%s'/>"
                       % (line, _INK if heavy else _FAINT,
                          "0.35" if heavy else "0.15"))
            pos += s
            n += 1
    r = min(12.0, max(5.0, s * 0.8))
    out.append("<path d='M%.3f %.3f L%.3f %.3f L%.3f %.3f L%.3f %.3f Z' "
               "fill='%s'/>" % (cx, cy - r, cx + r, cy, cx, cy + r,
                                cx - r, cy, _INK))
    out.append("<circle cx='%.3f' cy='%.3f' r='0.8' fill='#fff'/>" % (cx, cy))
    return out


def _clock(w: float, h: float, d: float) -> List[str]:
    """Twelve bulls on a circle plus one in the middle: call a number, hit it.
    Transition practice, and a whole session on one sheet."""
    cx, cy = w / 2.0, h / 2.0
    ring_r = min(w, h) / 2.0 - d * 0.75
    out = _bull(cx, cy, d)
    for hour in range(12):
        a = hour * pi / 6.0 - pi / 2.0
        out += _bull(cx + ring_r * cos(a), cy + ring_r * sin(a), d)
    return out


def _ladder(w: float, h: float, d: float) -> List[str]:
    """Rows of shrinking bulls: start big, work left to right until you miss."""
    sizes = [d * f for f in (1.0, 0.82, 0.66, 0.52, 0.4, 0.3) if d * f >= 4.0]
    row_w = sum(s * 1.35 for s in sizes)
    if row_w > w:                          # squeeze a wide ladder onto the page
        sizes = [s * w / row_w for s in sizes]
        row_w = w
    row_h = sizes[0] * 1.35
    rows = max(1, int(h / row_h))
    y0 = (h - rows * row_h) / 2.0
    out = []
    for r in range(rows):
        cy = y0 + (r + 0.5) * row_h
        x = (w - row_w) / 2.0
        for s in sizes:
            out += _bull(x + s * 1.35 / 2.0, cy, s)
            x += s * 1.35
    return out


def _lines(w: float, h: float, s: float) -> List[str]:
    """Vertical bars to track and trace -- trigger-control and follow-through
    drills; shots walking off a line show movement the bull hides."""
    out = []
    cols = max(1, int(w / s))
    x0 = (w - (cols - 1) * s) / 2.0
    for c in range(cols):
        out.append("<path d='M%.3f 0 v%.3f' stroke='%s' stroke-width='1.2'/>"
                   % (x0 + c * s, h, _INK))
    out.append("<path d='M0 %.3f h%.3f' stroke='%s' stroke-width='0.3' "
               "stroke-dasharray='4 3'/>" % (h / 2.0, w, _INK))
    return out


def _cards(w: float, h: float, cw: float) -> List[str]:
    """Playing-card rectangles at any scale (a real card is 63.5 mm wide)."""
    ch = cw * 88.9 / 63.5
    out = []
    for cx, cy in _cells(w, h, cw * 1.18, ch * 1.14):
        out.append("<rect x='%.3f' y='%.3f' width='%.3f' height='%.3f' "
                   "rx='%.3f' fill='none' stroke='%s' stroke-width='0.5'/>"
                   % (cx - cw / 2, cy - ch / 2, cw, ch, cw * 0.06, _INK))
        out.append("<circle cx='%.3f' cy='%.3f' r='0.7' fill='%s'/>"
                   % (cx, cy, _INK))
    return out


_Draw = Callable[[float, float, float], List[str]]

# family: (what the size means & what you get, default mm, minimum mm,
#          largest usable fraction of the paper's short side, draw)
FAMILIES: Dict[str, Tuple[str, float, float, float, _Draw]] = {
    "bulls": ("grid of solid bulls with an aim dot; size is the bull "
              "diameter", 40.0, 8.0, 1.0, _bulls),
    "dots": ("rows of small aiming dots; size is the dot diameter",
             15.0, 3.0, 0.5, _dots),
    "squares": ("outlined squares with a centre dot; size is the side",
                40.0, 8.0, 1.0, _squares),
    "diamonds": ("solid diamonds, a classic sight picture; size is the width",
                 30.0, 8.0, 0.7, _diamonds),
    "grid": ("sight-in graph paper with a centre diamond; size is the "
             "line spacing", 10.0, 5.0, 0.25, _grid),
    "clock": ("twelve bulls in a circle plus one centre; size is each bull",
              25.0, 8.0, 0.28, _clock),
    "ladder": ("rows of shrinking bulls, big to small; size is the biggest",
               50.0, 10.0, 0.45, _ladder),
    "lines": ("vertical tracking bars; size is the spacing",
              30.0, 10.0, 0.5, _lines),
    "cards": ("playing-card outlines; size is the card width (63.5 is "
              "life-size)", 63.5, 20.0, 0.9, _cards),
}

_NAME = re.compile(r"^([a-z]+)(?:[-\s](\d+(?:\.\d+)?))?$")


def scoring_face(diameter_mm: float) -> TargetSpec:
    """A real scored bullseye at any diameter: 10 rings when there is room,
    5 when there isn't."""
    n = 10 if diameter_mm >= 100.0 else 5
    step = diameter_mm / 2.0 / n
    return targets.uniform_target("Practice face %g mm" % diameter_mm,
                                  ten_ring_diameter_mm=step * 2.0,
                                  ring_step_mm=step, lowest_value=11 - n)


def parse(design: str) -> Tuple[str, float]:
    """``'dots-15'`` -> ``('dots', 15.0)``; bare family name takes its default.

    Raises :class:`KeyError` with the full family list for anything else --
    design names arrive from the web query string, so this is the gate.
    """
    key = str(design or "").strip().lower()
    match = _NAME.match(key)
    fam = match.group(1) if match else ""
    if fam != "face" and fam not in FAMILIES:
        raise KeyError("Unknown design %r. Families: face, %s -- add a size "
                       "in mm, like 'dots-15' or 'face-120'."
                       % (design, ", ".join(sorted(FAMILIES))))
    lo, default = (20.0, 100.0) if fam == "face" else (
        FAMILIES[fam][2], FAMILIES[fam][1])
    mm = float(match.group(2)) if match.group(2) else default
    if not (lo <= mm <= 1000.0):
        raise KeyError("%s wants a size between %g and 1000 mm, not %g."
                       % (fam, lo, mm))
    return fam, mm


def make(design: str, paper: str = "a4") -> str:
    """The printable HTML page for ``design`` on ``paper``.

    Raises :class:`KeyError` for an unknown design, a size out of range, or a
    size that cannot fit the paper.
    """
    fam, mm = parse(design)
    if fam == "face":
        return render.target_html(scoring_face(mm), paper=paper)
    page_w, page_h = render.paper_size(paper)
    w = page_w - 2 * render._PAGE_MARGIN_MM
    h = page_h - 2 * render._PAGE_MARGIN_MM - render._FOOTER_MM
    blurb, _, _, fit, draw = FAMILIES[fam]
    if mm > fit * min(w, h):
        raise KeyError("%g mm is too big for %s-%g on this paper (up to "
                       "%.0f mm). Use bigger paper, or a custom size like "
                       "'400x600'." % (mm, fam, mm, fit * min(w, h)))
    caption = "%s %g mm -- %s" % (fam, mm, blurb)
    return render.sheet_html(caption, "".join(draw(w, h, mm)), paper)


def catalog() -> List[Tuple[str, str]]:
    """One ready-to-print example per family, listing-ready ``(name, blurb)``."""
    rows = [("face-100", "a scored bullseye at any diameter -- face-40 to "
             "face-1000, tiled over sheets when it outgrows the paper")]
    for fam in sorted(FAMILIES):
        blurb, default, lo, _, _ = FAMILIES[fam]
        rows.append(("%s-%g" % (fam, default),
                     "%s (any size from %g mm)" % (blurb, lo)))
    return rows


if __name__ == "__main__":
    # ponytail: the runnable check -- every family generates, sizes scale,
    # the gate rejects what it should.
    for name, _blurb in catalog():
        page = make(name)
        assert "width='210.000mm'" in page, name       # true-scale a4
        assert "reprint at 100%" in page, name         # calibration ruler
    assert make("dots-5").count("<circle") > make("dots-40").count("<circle")
    assert make("face-600").count("class='sheet'") > 1  # big faces tile
    assert len(scoring_face(60.0).rings) == 5
    for bad in ("silhouette", "dots-1", "bulls-999", "", "face-5000"):
        try:
            make(bad)
            raise AssertionError("accepted %r" % bad)
        except KeyError:
            pass
    make("clock-50", paper="a4")                        # fits a4...
    try:
        make("clock-50", paper="a5")                    # ...but not a5
        raise AssertionError("clock-50 fit a5")
    except KeyError:
        pass
    print("sheets self-check OK: %d families, e.g. %s"
          % (len(FAMILIES) + 1, ", ".join(n for n, _ in catalog())))
