"""Visual *skins* (themes) for Marksman's terminal output.

A skin is a palette of ANSI colours plus a few texture choices (the sparkline
ramp, the horizontal-rule character, a marker glyph) that give the reports a
distinct look.  The set is inspired by the *vibe* of the best-selling console
first-person shooters since 2001 -- Call of Duty, Battlefield, Halo, Doom,
Destiny, Borderlands, Counter-Strike, Overwatch, Apex Legends, Far Cry -- using
original, trademark-free names and no game assets.

Everything here is pure standard library.  Colours are emitted as 256-colour
SGR sequences and are written *only* when output is going to a capable terminal
(a TTY, with ``NO_COLOR`` unset).  When colour is disabled every helper returns
its text unchanged, so piped or redirected output stays plain and stable --
exactly what the rest of the codebase (and its tests) expect.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional

# --------------------------------------------------------------------------- #
# Low-level SGR (Select Graphic Rendition) helpers
# --------------------------------------------------------------------------- #

RESET = "\x1b[0m"

#: Default sparkline ramp (low -> high).  Kept ASCII-safe and identical to the
#: historical ramp so that plain/disabled output never changes.
DEFAULT_SPARK = "_.-~=*#@"


def fg(n: int, bold: bool = False) -> str:
    """A 256-colour foreground SGR sequence (optionally bold)."""
    return ("\x1b[1;38;5;%dm" % n) if bold else ("\x1b[38;5;%dm" % n)


# --------------------------------------------------------------------------- #
# Theme definition
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Theme:
    """One skin: semantic colour roles plus a little texture.

    Each ``c_*`` field is a ready-to-emit SGR sequence (or an empty string for
    "no styling", as used by the plain ``mono`` skin).  Roles are *semantic*
    rather than literal colours so the report code never hard-codes a colour:

    * ``title``  -- report/section headings
    * ``rule``   -- the underline beneath a heading
    * ``label``  -- metric names ("Mean radius")
    * ``value``  -- the numbers themselves
    * ``unit``   -- units ("mm", "MOA")
    * ``accent`` -- highlights, sparklines, the brand flourish
    * ``good``   -- improving trends / positive deltas
    * ``bad``    -- declining trends / negative deltas
    * ``muted``  -- secondary text, separators, hints
    """

    key: str
    title: str            # human-readable display name
    inspired_by: str      # the vibe this skin nods to (shown in the listing)
    c_title: str
    c_rule: str
    c_label: str
    c_value: str
    c_unit: str
    c_accent: str
    c_good: str
    c_bad: str
    c_muted: str
    spark: str = DEFAULT_SPARK
    rule_char: str = "-"
    glyph: str = "*"


# --------------------------------------------------------------------------- #
# Painter -- applies a Theme to text, honouring an on/off switch
# --------------------------------------------------------------------------- #

class Painter:
    """Wraps text in a :class:`Theme`'s colours when ``enabled``.

    When disabled (the default for non-terminals, ``NO_COLOR``, ``--no-color``)
    every method returns its argument unchanged, so output is byte-for-byte the
    same as the original plain text.
    """

    _TREND = {"improving": "good", "declining": "bad", "flat": "muted",
              "n/a": "muted"}

    def __init__(self, theme: Theme, enabled: bool = False):
        self.theme = theme
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        if not self.enabled or not code:
            return text
        return code + text + RESET

    # Semantic roles ------------------------------------------------------- #
    def title(self, s: str) -> str:  return self._wrap(self.theme.c_title, s)
    def rule(self, s: str) -> str:   return self._wrap(self.theme.c_rule, s)
    def label(self, s: str) -> str:  return self._wrap(self.theme.c_label, s)
    def value(self, s: str) -> str:  return self._wrap(self.theme.c_value, s)
    def unit(self, s: str) -> str:   return self._wrap(self.theme.c_unit, s)
    def accent(self, s: str) -> str: return self._wrap(self.theme.c_accent, s)
    def good(self, s: str) -> str:   return self._wrap(self.theme.c_good, s)
    def bad(self, s: str) -> str:    return self._wrap(self.theme.c_bad, s)
    def muted(self, s: str) -> str:  return self._wrap(self.theme.c_muted, s)

    def trend(self, direction: str, s: str) -> str:
        """Colour a trend word ("improving"/"declining"/...) by its meaning."""
        role = self._TREND.get(direction, "muted")
        return getattr(self, role)(s)

    # Texture -------------------------------------------------------------- #
    @property
    def spark_ramp(self) -> str:
        """The skin's sparkline ramp -- but only when colour/skin is active.

        Falling back to the default ramp when disabled keeps piped output
        identical regardless of the selected skin.
        """
        return self.theme.spark if self.enabled else DEFAULT_SPARK

    @property
    def rule_char(self) -> str:
        return self.theme.rule_char if self.enabled else "-"

    @property
    def glyph(self) -> str:
        return self.theme.glyph if self.enabled else "*"


# --------------------------------------------------------------------------- #
# The skins
# --------------------------------------------------------------------------- #
#
# 256-colour indices are chosen to evoke each franchise's signature HUD/palette.
# Ramps are ASCII-only for maximum portability (Windows code pages included).

_THEME_LIST: List[Theme] = [
    Theme(
        key="mono", title="Iron Sights",
        inspired_by="a clean printed score card -- no colour, just the numbers",
        c_title="", c_rule="", c_label="", c_value="", c_unit="",
        c_accent="", c_good="", c_bad="", c_muted="",
        spark=DEFAULT_SPARK, rule_char="-", glyph="*",
    ),
    Theme(
        key="recon", title="Night Recon",
        inspired_by="Call of Duty: Modern Warfare -- night-vision phosphor & amber",
        c_title=fg(46, bold=True), c_rule=fg(28), c_label=fg(108),
        c_value=fg(231, bold=True), c_unit=fg(65), c_accent=fg(208),
        c_good=fg(46), c_bad=fg(196), c_muted=fg(240),
        spark=".,:;+*x#", rule_char="-", glyph="+",
    ),
    Theme(
        key="orbital", title="Orbital",
        inspired_by="Halo -- UNSC blue HUD with Spartan green & holo-amber",
        c_title=fg(45, bold=True), c_rule=fg(39), c_label=fg(110),
        c_value=fg(231, bold=True), c_unit=fg(109), c_accent=fg(220),
        c_good=fg(82), c_bad=fg(203), c_muted=fg(244),
        spark=".:-=+*#@", rule_char="=", glyph=">",
    ),
    Theme(
        key="inferno", title="Inferno",
        inspired_by="Doom -- molten blood-red and hellfire orange",
        c_title=fg(196, bold=True), c_rule=fg(88), c_label=fg(173),
        c_value=fg(231, bold=True), c_unit=fg(130), c_accent=fg(208),
        c_good=fg(214), c_bad=fg(124), c_muted=fg(240),
        spark=".:^*xX#@", rule_char="=", glyph="X",
    ),
    Theme(
        key="frontline", title="Frontline",
        inspired_by="Battlefield -- steel-blue smoke cut with dog-tag orange",
        c_title=fg(208, bold=True), c_rule=fg(66), c_label=fg(110),
        c_value=fg(231, bold=True), c_unit=fg(109), c_accent=fg(39),
        c_good=fg(78), c_bad=fg(203), c_muted=fg(244),
        spark=".:=+*#%@", rule_char="-", glyph="|",
    ),
    Theme(
        key="lightfall", title="Light & Dark",
        inspired_by="Destiny -- Guardian purple lit by golden Light",
        c_title=fg(141, bold=True), c_rule=fg(99), c_label=fg(146),
        c_value=fg(231, bold=True), c_unit=fg(103), c_accent=fg(220),
        c_good=fg(220), c_bad=fg(168), c_muted=fg(244),
        spark=".:-=*o0@", rule_char="~", glyph="*",
    ),
    Theme(
        key="pandora", title="Cel-Shade",
        inspired_by="Borderlands -- bold comic yellow with inky outlines",
        c_title=fg(226, bold=True), c_rule=fg(130), c_label=fg(178),
        c_value=fg(231, bold=True), c_unit=fg(136), c_accent=fg(208),
        c_good=fg(190), c_bad=fg(160), c_muted=fg(240),
        spark=".:oO0Q#@", rule_char="=", glyph="!",
    ),
    Theme(
        key="dust", title="Dust",
        inspired_by="Counter-Strike -- desert sand versus counter-terrorist blue",
        c_title=fg(222, bold=True), c_rule=fg(137), c_label=fg(180),
        c_value=fg(231, bold=True), c_unit=fg(144), c_accent=fg(39),
        c_good=fg(78), c_bad=fg(196), c_muted=fg(244),
        spark=".,:;+=#@", rule_char="-", glyph="+",
    ),
    Theme(
        key="overdrive", title="Overdrive",
        inspired_by="Overwatch -- vibrant orange energy over bright cyan",
        c_title=fg(208, bold=True), c_rule=fg(45), c_label=fg(75),
        c_value=fg(231, bold=True), c_unit=fg(80), c_accent=fg(51),
        c_good=fg(48), c_bad=fg(205), c_muted=fg(245),
        spark=".:-=+*o#", rule_char="=", glyph="o",
    ),
    Theme(
        key="dropzone", title="Drop Zone",
        inspired_by="Apex Legends / battle royale -- crimson on gunmetal slate",
        c_title=fg(197, bold=True), c_rule=fg(240), c_label=fg(250),
        c_value=fg(231, bold=True), c_unit=fg(245), c_accent=fg(203),
        c_good=fg(84), c_bad=fg(124), c_muted=fg(240),
        spark=".:-=+*#@", rule_char="-", glyph=">",
    ),
    Theme(
        key="tropic", title="Far Outpost",
        inspired_by="Far Cry -- lush tropical teal under a sunset orange",
        c_title=fg(214, bold=True), c_rule=fg(29), c_label=fg(108),
        c_value=fg(231, bold=True), c_unit=fg(72), c_accent=fg(43),
        c_good=fg(41), c_bad=fg(167), c_muted=fg(244),
        spark=".,:~=*#@", rule_char="~", glyph="^",
    ),
]

#: Skins keyed by their CLI name, insertion order preserved (mono first).
THEMES: Dict[str, Theme] = {t.key: t for t in _THEME_LIST}

#: The skin used when none is chosen / an unknown one is requested.
DEFAULT_THEME = "mono"


def list_themes() -> List[Theme]:
    """All skins, in display order (the plain ``mono`` skin first)."""
    return list(_THEME_LIST)


def get_theme(name: Optional[str]) -> Theme:
    """Resolve a skin by key, case-insensitively; fall back to ``mono``.

    Resilient by design: rendering should never crash on a stale/unknown name.
    Use :func:`is_theme` when you need to *reject* an unknown name (e.g. when
    the user is explicitly setting one).
    """
    if not name:
        return THEMES[DEFAULT_THEME]
    return THEMES.get(name.strip().lower(), THEMES[DEFAULT_THEME])


def is_theme(name: Optional[str]) -> bool:
    """True if ``name`` is a known skin key (case-insensitive)."""
    return bool(name) and name.strip().lower() in THEMES


# --------------------------------------------------------------------------- #
# Terminal capability detection
# --------------------------------------------------------------------------- #

def enable_ansi(stream=None) -> bool:
    """Best-effort enable of ANSI VT processing on Windows consoles.

    Modern terminals (Windows Terminal, PowerShell 7, VS Code) already support
    ANSI; legacy ``conhost``/``cmd.exe`` needs the virtual-terminal flag set via
    the Win32 API.  Returns True if ANSI should work, False otherwise.  A no-op
    (returns True) on non-Windows platforms.
    """
    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        STD_OUTPUT_HANDLE = -11
        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        if handle == 0 or handle == -1:
            return False
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        kernel32.SetConsoleMode(
            handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
        return True
    except Exception:
        return False


def color_enabled(stream=None, no_color: bool = False,
                  force: bool = False) -> bool:
    """Decide whether to emit colour to ``stream`` (default ``sys.stdout``).

    Honours, in order: an explicit ``no_color`` request, the ``NO_COLOR`` and
    ``MARKSMAN_NO_COLOR`` environment conventions, ``force`` (used by the
    ``theme`` preview/set so swatches show even when not a TTY), then finally
    whether the stream is an interactive terminal.

    Enabling Windows VT processing is a best-effort *side effect*, not a veto:
    on modern consoles it succeeds, and on the rare legacy console it can't,
    we still honour the user's clear intent rather than silently degrading.
    """
    if stream is None:
        stream = sys.stdout
    if no_color:
        return False
    if os.environ.get("NO_COLOR") or os.environ.get("MARKSMAN_NO_COLOR"):
        return False
    if not force:
        isatty = getattr(stream, "isatty", None)
        if not (callable(isatty) and isatty()):
            return False
    enable_ansi(stream)
    return True


def plain_painter() -> Painter:
    """A disabled painter (no colour, default ramp) -- the safe fallback."""
    return Painter(THEMES[DEFAULT_THEME], enabled=False)


# --------------------------------------------------------------------------- #
# Preview helper
# --------------------------------------------------------------------------- #

def _demo_spark(ramp: str) -> str:
    """Render a representative descending pattern across an arbitrary ramp."""
    vals = [0.95, 0.80, 0.78, 0.50, 0.55, 0.35, 0.20, 0.18, 0.08, 0.0]
    hi = len(ramp) - 1
    return "".join(ramp[int(v * hi + 0.5)] for v in vals)


def sample_report(painter: Painter) -> str:
    """A small, data-free showcase of a skin -- used by ``theme preview``."""
    p = painter
    head = " M A R K S M A N  //  %s " % p.theme.title
    lines = [
        p.title(head),
        p.rule(p.rule_char * len(head)),
        "  " + p.label("Group size (extreme spread) : ")
            + p.value("12.4") + " " + p.unit("mm"),
        "  " + p.label("Mean radius                 : ")
            + p.value("4.8") + " " + p.unit("mm"),
        "  " + p.label("Score                       : ")
            + p.value("96.3") + p.muted(" / 100"),
        "  " + p.label("Trend                       : ")
            + p.good("improving") + p.muted("  /  ") + p.bad("declining"),
        "  " + p.label("Group over time             : ")
            + p.accent(_demo_spark(p.spark_ramp)),
        "  " + p.muted("swatch ") + p.glyph + " "
            + p.accent("accent") + p.muted(" / ")
            + p.good("good") + p.muted(" / ") + p.bad("bad"),
    ]
    return "\n".join(lines)
