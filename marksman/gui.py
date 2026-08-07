"""A desktop GUI for Marksman -- tkinter, standard library only.

Eight tabs over the same data the CLI uses: a dashboard, the drill catalogue
with tiers, an interactive target you click your shots onto, progress charts,
the session log, goals, the AI coach folder, and your tools.

Nothing here owns any logic: scoring, grouping, trends, drills, goals and the
coach all live in their own modules and are read straight through.  The GUI is
a view.  Launch it with ``marksman gui``.

ponytail: tkinter + hand-drawn Canvas rather than a UI toolkit or a plotting
library -- it ships with Python, so the app keeps its zero-dependency promise.
The ceiling is ~10k sessions (the tables render eagerly); paginate if anyone
ever gets there.
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
import webbrowser
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import coach as coach_mod
from . import drills as drills_mod
from . import exporter, goals as goals_mod, render, targets as targets_mod
from . import packs as packs_mod
from . import theme as theme_mod
from . import tracker
from .grouping import analyze_group
from .models import Session, Shot, TargetSpec, Tool
from .storage import DEFAULT_DB_PATH, Database


def _term(word: str) -> str:
    """The active pack's word for a core concept, else the neutral one."""
    return packs_mod.term(word)

# --------------------------------------------------------------------------- #
# Palette -- the terminal skins, translated to a dark desktop theme
# --------------------------------------------------------------------------- #

_BASE = {
    "bg": "#12151c", "panel": "#1a1f2a", "raised": "#232a38",
    "fg": "#e6ebf2", "muted": "#8b95a7", "grid": "#2b3444",
    "face": "#f4f1e8", "ink": "#1b1b1b", "hit": "#ff9628",
}

#: skin key -> the three colours that actually differ between skins
PALETTES = {
    "mono":      {"accent": "#5b8dd6", "good": "#4caf7d", "bad": "#d16d6d"},
    "recon":     {"accent": "#3ddc7f", "good": "#3ddc7f", "bad": "#ffb547"},
    "orbital":   {"accent": "#4db6ff", "good": "#5fd38d", "bad": "#ffb347"},
    "inferno":   {"accent": "#ff5a3c", "good": "#ffa53c", "bad": "#ff2d2d"},
    "frontline": {"accent": "#7fa8c9", "good": "#8fd0a8", "bad": "#ff9a3c"},
    "lightfall": {"accent": "#b48cff", "good": "#ffd166", "bad": "#ff6b8b"},
    "pandora":   {"accent": "#ffd400", "good": "#9ee37d", "bad": "#ff5c5c"},
    "dust":      {"accent": "#d9b382", "good": "#7fc7d9", "bad": "#e0774a"},
    "overdrive": {"accent": "#ff8a1f", "good": "#26d7d7", "bad": "#ff4d6d"},
    "dropzone":  {"accent": "#e03c46", "good": "#8fb0c9", "bad": "#ff8a5c"},
    "tropic":    {"accent": "#2ec4b6", "good": "#8fe3a8", "bad": "#ff9f45"},
}

TIER_COLORS = {"Rookie": "#a97142", "Steady": "#9aa7b4",
               "Sharp": "#e0b23c", "Marksman": "#4fd8c0"}

DISCLAIMER = (
    "Marksman is a hobby project by a software developer -- not a coach, "
    "instructor, doctor or lawyer. It computes metrics for personal progress "
    "tracking only; it is not coaching, safety, medical or legal advice, and "
    "not an official scoring system.\n\n"
    "Anything that launches a projectile can injure: always wear eye protection, "
    "follow your field's rules, and obey your local laws.\n\n"
    "Provided \"as is\", with no warranty. See DISCLAIMER.md."
)


def palette(key: Optional[str]) -> Dict[str, str]:
    out = dict(_BASE)
    out.update(PALETTES.get((key or "mono"), PALETTES["mono"]))
    return out


def _font(size: int = 10, bold: bool = False) -> tuple:
    family = "Segoe UI" if sys.platform.startswith("win") else "TkDefaultFont"
    return (family, size, "bold") if bold else (family, size)


def _fmt(v: Optional[float], digits: int = 1) -> str:
    return "--" if v is None else ("%.*f" % (digits, v))


# --------------------------------------------------------------------------- #
# Interactive target: click to place your shots
# --------------------------------------------------------------------------- #

class TargetCanvas(tk.Canvas):
    """Draws a target face to scale and lets you click shots onto it.

    Left click adds a shot, right click removes the nearest one, and every
    change calls ``on_change`` so the stats panel can follow along live.
    """

    def __init__(self, master, pal: Dict[str, str], on_change=None, size: int = 460):
        tk.Canvas.__init__(self, master, width=size, height=size,
                           bg=pal["panel"], highlightthickness=0)
        self.pal = pal
        self.on_change = on_change
        self.shots = []            # type: List[Shot]
        self.target = None         # type: Optional[TargetSpec]
        self.projectile_mm = 6.0
        self._scale = 1.0          # px per mm
        self.bind("<Button-1>", self._add)
        self.bind("<Button-3>", self._remove)
        self.bind("<Configure>", lambda e: self.redraw())

    # -- data -------------------------------------------------------------- #
    def set_target(self, target: Optional[TargetSpec]) -> None:
        self.target = target
        self.redraw()

    def set_shots(self, shots: List[Shot]) -> None:
        self.shots = list(shots)
        self.redraw()
        self._changed()

    def clear(self) -> None:
        self.set_shots([])

    def undo(self) -> None:
        if self.shots:
            self.shots.pop()
            self.redraw()
            self._changed()

    def _changed(self) -> None:
        if self.on_change:
            self.on_change(self.shots)

    # -- geometry ---------------------------------------------------------- #
    def _extent_mm(self) -> float:
        if self.target and self.target.outer_radius_mm > 0:
            return self.target.outer_radius_mm
        reach = max([s.radius_mm for s in self.shots] or [50.0])
        return max(reach * 1.25, 20.0)

    def _px(self, x_mm: float, y_mm: float):
        cx, cy = self._centre()
        return cx + x_mm * self._scale, cy - y_mm * self._scale

    def _mm(self, px: float, py: float):
        cx, cy = self._centre()
        return (px - cx) / self._scale, (cy - py) / self._scale

    def _centre(self):
        return (self.winfo_width() or int(self["width"])) / 2.0, \
               (self.winfo_height() or int(self["height"])) / 2.0

    # -- events ------------------------------------------------------------ #
    def _add(self, event) -> None:
        x, y = self._mm(event.x, event.y)
        self.shots.append(Shot(round(x, 2), round(y, 2)))
        self.redraw()
        self._changed()

    def _remove(self, event) -> None:
        if not self.shots:
            return
        x, y = self._mm(event.x, event.y)
        nearest = min(self.shots,
                      key=lambda s: (s.x_mm - x) ** 2 + (s.y_mm - y) ** 2)
        self.shots.remove(nearest)
        self.redraw()
        self._changed()

    # -- painting ---------------------------------------------------------- #
    def redraw(self) -> None:
        self.delete("all")
        w = self.winfo_width() or int(self["width"])
        h = self.winfo_height() or int(self["height"])
        if w < 20 or h < 20:
            return
        pal = self.pal
        extent = self._extent_mm()
        self._scale = (min(w, h) / 2.0 - 18) / extent
        cx, cy = w / 2.0, h / 2.0

        if self.target:
            self._draw_face(cx, cy)
        else:
            self.create_oval(cx - 4, cy - 4, cx + 4, cy + 4,
                             outline=pal["muted"], width=1)
            self.create_text(cx, 14, text="no target face selected",
                             fill=pal["muted"], font=_font(9))

        # Point of aim crosshair.
        self.create_line(cx - 9, cy, cx + 9, cy, fill=pal["accent"], width=1)
        self.create_line(cx, cy - 9, cx, cy + 9, fill=pal["accent"], width=1)

        self._draw_shots(cx, cy)
        if not self.shots:
            self.create_text(cx, h - 14,
                             text="click to place shots  •  right-click removes",
                             fill=pal["muted"], font=_font(9))

    def _draw_face(self, cx: float, cy: float) -> None:
        pal = self.pal
        rings = sorted(self.target.rings, key=lambda r: -r.diameter_mm)
        black_r = render._black_radius_mm(self.target)
        for ring in rings:
            r = ring.radius_mm * self._scale
            dark = ring.radius_mm <= black_r + 1e-9
            self.create_oval(cx - r, cy - r, cx + r, cy + r,
                             fill=(pal["ink"] if dark else pal["face"]),
                             outline=(pal["face"] if dark else pal["ink"]),
                             width=1)
        # Ring numbers, centred in their own band -- skipped where the band is
        # too narrow to hold one legibly (they crowd badly near the middle).
        radii = [r.radius_mm for r in rings] + [0.0]
        for ring, inner_mm in zip(rings, radii[1:]):
            band = (ring.radius_mm - inner_mm) * self._scale
            if band < 13:
                continue
            x = cx - (ring.radius_mm + inner_mm) / 2.0 * self._scale
            self.create_text(
                x, cy, text=str(ring.value), font=_font(8),
                fill=(pal["face"] if ring.radius_mm <= black_r else pal["ink"]))

    def _draw_shots(self, cx: float, cy: float) -> None:
        pal = self.pal
        if not self.shots:
            return
        r = max(self.projectile_mm * self._scale / 2.0, 3.0)
        if len(self.shots) >= 2:
            gx = sum(s.x_mm for s in self.shots) / len(self.shots)
            gy = sum(s.y_mm for s in self.shots) / len(self.shots)
            mr = sum(((s.x_mm - gx) ** 2 + (s.y_mm - gy) ** 2) ** 0.5
                     for s in self.shots) / len(self.shots)
            px, py = self._px(gx, gy)
            rr = mr * self._scale
            if rr > 1:
                self.create_oval(px - rr, py - rr, px + rr, py + rr,
                                 outline=pal["good"], width=1, dash=(3, 3))
            self.create_line(cx, cy, px, py, fill=pal["good"], width=1)
            self.create_line(px - 5, py, px + 5, py, fill=pal["good"], width=2)
            self.create_line(px, py - 5, px, py + 5, fill=pal["good"], width=2)
        for i, s in enumerate(self.shots, 1):
            px, py = self._px(s.x_mm, s.y_mm)
            self.create_oval(px - r, py - r, px + r, py + r,
                             fill=pal["hit"], outline="#000000", width=1)
            if r >= 6:
                self.create_text(px, py, text=str(i), font=_font(7),
                                 fill="#000000")


# --------------------------------------------------------------------------- #
# Line chart
# --------------------------------------------------------------------------- #

class Chart(tk.Canvas):
    """A minimal time-series line chart (one metric, oldest to newest)."""

    def __init__(self, master, pal: Dict[str, str], height: int = 240):
        tk.Canvas.__init__(self, master, height=height, bg=pal["panel"],
                           highlightthickness=0)
        self.pal = pal
        self.points = []           # type: List[tuple]
        self.title = ""
        self.lower_is_better = True
        self.bind("<Configure>", lambda e: self.redraw())

    def set_series(self, points: List[tuple], title: str,
                   lower_is_better: bool = True) -> None:
        self.points = [p for p in points if p[1] is not None]
        self.title = title
        self.lower_is_better = lower_is_better
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        pal = self.pal
        w = self.winfo_width() or 600
        h = self.winfo_height() or 240
        left, right, top, bottom = 58, 14, 28, 30
        self.create_text(left, 12, text=self.title, anchor="w",
                         fill=pal["fg"], font=_font(10, True))
        if len(self.points) < 1:
            self.create_text(w / 2, h / 2, text="no data yet",
                             fill=pal["muted"], font=_font(10))
            return

        values = [p[1] for p in self.points]
        lo, hi = min(values), max(values)
        if hi - lo < 1e-9:
            lo, hi = lo - 1.0, hi + 1.0
        pad = (hi - lo) * 0.12
        lo, hi = lo - pad, hi + pad
        plot_w = max(w - left - right, 10)
        plot_h = max(h - top - bottom, 10)

        def xy(i, v):
            n = max(len(self.points) - 1, 1)
            return (left + plot_w * (i / n),
                    top + plot_h * (1.0 - (v - lo) / (hi - lo)))

        for step in range(5):                       # gridlines + y labels
            v = hi - (hi - lo) * step / 4.0
            y = top + plot_h * step / 4.0
            self.create_line(left, y, w - right, y, fill=pal["grid"])
            self.create_text(left - 6, y, text="%.1f" % v, anchor="e",
                             fill=pal["muted"], font=_font(8))

        coords = []
        for i, (_, v) in enumerate(self.points):
            coords.extend(xy(i, v))
        if len(coords) >= 4:
            self.create_line(*coords, fill=pal["accent"], width=2, smooth=False)

        best = min(values) if self.lower_is_better else max(values)
        for i, (label, v) in enumerate(self.points):
            px, py = xy(i, v)
            is_best = abs(v - best) < 1e-9
            r = 5 if is_best else 3
            self.create_oval(px - r, py - r, px + r, py + r,
                             fill=(pal["good"] if is_best else pal["accent"]),
                             outline=pal["panel"])
        # First and last x labels only -- dates get unreadable fast.
        self.create_text(left, h - 12, text=str(self.points[0][0]), anchor="w",
                         fill=pal["muted"], font=_font(8))
        if len(self.points) > 1:
            self.create_text(w - right, h - 12, text=str(self.points[-1][0]),
                             anchor="e", fill=pal["muted"], font=_font(8))
        self.create_text(w - right, 12,
                         text="best %.1f  •  latest %.1f" % (best, values[-1]),
                         anchor="e", fill=pal["good"], font=_font(9))


# --------------------------------------------------------------------------- #
# The application
# --------------------------------------------------------------------------- #

class App(tk.Tk):

    def __init__(self, db_path: str = DEFAULT_DB_PATH,
                 theme_key: Optional[str] = None):
        tk.Tk.__init__(self)
        self.db = Database.load(db_path)
        self.theme_key = theme_key or self.db.settings.get("theme") or "mono"
        self.pal = palette(self.theme_key)
        self._refreshers = []      # type: List[Any]

        self.title("Marksman")
        self.geometry("1180x780")
        self.minsize(980, 640)
        self.configure(bg=self.pal["bg"])
        self._set_icon()
        self._style()
        self._menu()
        self._header()
        self._tabs()
        self._status = tk.Label(self, text="", bg=self.pal["bg"],
                                fg=self.pal["muted"], anchor="w", font=_font(9))
        self._status.pack(fill="x", side="bottom", padx=12, pady=(0, 6))
        self.refresh()
        self.say("Loaded %s" % os.path.abspath(self.db.path))

    # -- chrome ------------------------------------------------------------ #
    def _set_icon(self) -> None:
        ico = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "assets", "marksman.ico")
        if os.path.exists(ico):
            try:
                self.iconbitmap(ico)
            except tk.TclError:
                pass           # non-Windows Tk, or no icon support -- cosmetic

    def _style(self) -> None:
        pal = self.pal
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=pal["bg"], foreground=pal["fg"],
                     fieldbackground=pal["raised"], font=_font(10))
        st.configure("TFrame", background=pal["bg"])
        st.configure("Panel.TFrame", background=pal["panel"])
        st.configure("Card.TFrame", background=pal["raised"])
        st.configure("TLabel", background=pal["bg"], foreground=pal["fg"])
        st.configure("Panel.TLabel", background=pal["panel"], foreground=pal["fg"])
        st.configure("Card.TLabel", background=pal["raised"], foreground=pal["fg"])
        st.configure("Muted.TLabel", background=pal["bg"], foreground=pal["muted"],
                     font=_font(9))
        st.configure("CardMuted.TLabel", background=pal["raised"],
                     foreground=pal["muted"], font=_font(9))
        st.configure("H1.TLabel", background=pal["bg"], foreground=pal["fg"],
                     font=_font(17, True))
        st.configure("H2.TLabel", background=pal["bg"], foreground=pal["accent"],
                     font=_font(12, True))
        st.configure("Stat.TLabel", background=pal["raised"],
                     foreground=pal["accent"], font=_font(20, True))
        st.configure("TButton", background=pal["raised"], foreground=pal["fg"],
                     borderwidth=0, padding=(10, 5))
        st.map("TButton", background=[("active", pal["accent"]),
                                      ("pressed", pal["accent"])],
               foreground=[("active", pal["bg"])])
        st.configure("Accent.TButton", background=pal["accent"],
                     foreground=pal["bg"], font=_font(10, True))
        st.configure("TNotebook", background=pal["bg"], borderwidth=0)
        st.configure("TNotebook.Tab", background=pal["panel"],
                     foreground=pal["muted"], padding=(16, 8), borderwidth=0)
        st.map("TNotebook.Tab", background=[("selected", pal["bg"])],
               foreground=[("selected", pal["accent"])])
        st.configure("Treeview", background=pal["panel"],
                     fieldbackground=pal["panel"], foreground=pal["fg"],
                     borderwidth=0, rowheight=24)
        st.configure("Treeview.Heading", background=pal["raised"],
                     foreground=pal["muted"], borderwidth=0, font=_font(9, True))
        st.map("Treeview", background=[("selected", pal["accent"])],
               foreground=[("selected", pal["bg"])])
        st.configure("TEntry", fieldbackground=pal["raised"],
                     foreground=pal["fg"], insertcolor=pal["fg"], borderwidth=0)
        st.configure("TCombobox", fieldbackground=pal["raised"],
                     background=pal["raised"], foreground=pal["fg"],
                     arrowcolor=pal["fg"], borderwidth=0)
        # A readonly combobox renders its text *selected*; without this the
        # value sits on the platform highlight colour and is unreadable.
        st.map("TCombobox",
               fieldbackground=[("readonly", pal["raised"])],
               foreground=[("readonly", pal["fg"])],
               selectbackground=[("readonly", pal["raised"])],
               selectforeground=[("readonly", pal["fg"])])
        st.configure("Horizontal.TProgressbar", background=pal["accent"],
                     troughcolor=pal["panel"], borderwidth=0)
        self.option_add("*TCombobox*Listbox.background", pal["raised"])
        self.option_add("*TCombobox*Listbox.foreground", pal["fg"])
        self.option_add("*TCombobox*Listbox.selectBackground", pal["accent"])

    def _menu(self) -> None:
        pal = self.pal
        bar = tk.Menu(self)
        kw = dict(tearoff=0, bg=pal["panel"], fg=pal["fg"],
                  activebackground=pal["accent"], activeforeground=pal["bg"])
        f = tk.Menu(bar, **kw)
        f.add_command(label="Open database…", command=self.open_db)
        f.add_command(label="Export CSV…",
                      command=lambda: self.export_data("csv"))
        f.add_command(label="Export JSON…",
                      command=lambda: self.export_data("json"))
        f.add_separator()
        f.add_command(label="Quit", command=self.destroy)
        bar.add_cascade(label="File", menu=f)

        v = tk.Menu(bar, **kw)
        for th in theme_mod.list_themes():
            v.add_command(label="%s -- %s" % (th.title, th.inspired_by),
                          command=lambda k=th.key: self.set_skin(k))
        bar.add_cascade(label="Skin", menu=v)

        h = tk.Menu(bar, **kw)
        h.add_command(label="Disclaimer", command=self.show_disclaimer)
        h.add_command(label="About Marksman", command=self.show_about)
        bar.add_cascade(label="Help", menu=h)
        self.config(menu=bar)

    def _header(self) -> None:
        pal = self.pal
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=14, pady=(12, 4))
        ttk.Label(bar, text="Marksman", style="H1.TLabel").pack(side="left")
        ttk.Label(bar, text="  marksmanship drills & progress",
                  style="Muted.TLabel").pack(side="left", pady=(6, 0))
        self._streak_lbl = ttk.Label(bar, text="", style="H2.TLabel")
        self._streak_lbl.pack(side="right")

    def _tabs(self) -> None:
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=12, pady=6)
        self.nb = nb
        for title, build in (
            ("Dashboard", self._tab_dashboard),
            ("Drills", self._tab_drills),
            ("Log a session", self._tab_log),
            ("Progress", self._tab_progress),
            ("Sessions", self._tab_sessions),
            ("Goals", self._tab_goals),
            ("AI coach", self._tab_coach),
            ("Tools", self._tab_tools),
        ):
            frame = ttk.Frame(nb, padding=12)
            nb.add(frame, text=title)
            build(frame)

    # -- shared helpers ---------------------------------------------------- #
    def say(self, msg: str) -> None:
        self._status.configure(text=msg)

    def save(self) -> None:
        self.db.save()

    def refresh(self) -> None:
        """Re-read every tab from the database."""
        streak = coach_mod.current_streak(
            set(s.date for s in self.db.all_sessions()), date.today())
        self._streak_lbl.configure(
            text=("\U0001f525 %d-day streak" % streak) if streak else "no streak yet")
        for fn in self._refreshers:
            try:
                fn()
            except Exception:                      # a broken tab must not kill the app
                traceback.print_exc()

    def _card(self, master, title: str, value: str, sub: str = "") -> ttk.Frame:
        card = ttk.Frame(master, style="Card.TFrame", padding=(14, 10))
        ttk.Label(card, text=title.upper(), style="CardMuted.TLabel").pack(anchor="w")
        ttk.Label(card, text=value, style="Stat.TLabel").pack(anchor="w")
        ttk.Label(card, text=sub, style="CardMuted.TLabel").pack(anchor="w")
        return card

    def _tree(self, master, columns, widths, height=12) -> ttk.Treeview:
        wrap = ttk.Frame(master)
        wrap.pack(fill="both", expand=True)
        tv = ttk.Treeview(wrap, columns=columns, show="headings", height=height)
        for col, wd in zip(columns, widths):
            tv.heading(col, text=col)
            tv.column(col, width=wd, anchor="w")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return tv

    def _text(self, master, height=18) -> tk.Text:
        t = tk.Text(master, height=height, bg=self.pal["panel"],
                    fg=self.pal["fg"], insertbackground=self.pal["fg"],
                    relief="flat", wrap="word", font=_font(10), padx=10, pady=8)
        t.pack(fill="both", expand=True)
        return t

    # -- menu actions ------------------------------------------------------ #
    def open_db(self) -> None:
        path = filedialog.askopenfilename(
            title="Open Marksman database", filetypes=[("JSON", "*.json")])
        if path:
            self.db = Database.load(path)
            self.refresh()
            self.say("Loaded %s" % path)

    def export_data(self, fmt: str) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension="." + fmt,
            initialfile="marksman_sessions." + fmt,
            filetypes=[(fmt.upper(), "*." + fmt)])
        if not path:
            return
        text = exporter.to_csv(self.db) if fmt == "csv" else exporter.to_json(self.db)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        self.say("Exported %d sessions to %s" % (len(self.db.sessions), path))

    def set_skin(self, key: str) -> None:
        self.db.settings["theme"] = key
        self.save()
        messagebox.showinfo(
            "Skin saved",
            "Skin set to '%s'. Reopen the window to see it applied." % key)
        self.say("Skin set to %s (restart to apply)" % key)

    def show_disclaimer(self) -> None:
        messagebox.showwarning("Disclaimer -- please read", DISCLAIMER)

    def show_about(self) -> None:
        messagebox.showinfo(
            "About Marksman",
            "Marksman -- marksmanship drills and progress tracking.\n"
            "Pure Python standard library, no third-party packages.\n"
            "MIT licensed. © 2026 Adam Erik Eryavec.\n\n" + DISCLAIMER)

    # ------------------------------------------------------------------ #
    # Tab 1: Dashboard
    # ------------------------------------------------------------------ #
    def _tab_dashboard(self, root: ttk.Frame) -> None:
        cards = ttk.Frame(root)
        cards.pack(fill="x")
        body = ttk.Frame(root)
        body.pack(fill="both", expand=True, pady=(14, 0))
        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        right = ttk.Frame(body)
        right.pack(side="right", fill="both", expand=True)

        ttk.Label(left, text="Today's plan", style="H2.TLabel").pack(anchor="w")
        plan_box = ttk.Frame(left)
        plan_box.pack(fill="both", expand=True, pady=(6, 0))

        ttk.Label(right, text="Recent sessions", style="H2.TLabel").pack(anchor="w")
        recent = self._tree(right, ("date", "tool", "drill", "group", "score"),
                            (86, 130, 120, 74, 62), height=8)
        ttk.Label(right, text="Goals", style="H2.TLabel").pack(anchor="w", pady=(10, 0))
        goal_box = self._tree(right, ("goal", "target", "best", "status"),
                              (150, 80, 80, 70), height=5)

        def refresh():
            for w in cards.winfo_children() + plan_box.winfo_children():
                w.destroy()
            sessions = [s for s in self.db.all_sessions() if s.stats]
            shots = sum(s.stats.shot_count for s in sessions)
            rep = tracker.progress_overall(self.db.all_sessions())
            best = rep.metrics["extreme_spread_mm"].best if rep.session_count else None
            pts = drills_mod.tier_points(self.db)
            streak = coach_mod.current_streak(
                set(s.date for s in self.db.all_sessions()), date.today())
            for title, value, sub in (
                ("Sessions", str(len(sessions)), "%d shots logged" % shots),
                ("Best group", _fmt(best), "mm extreme spread"),
                ("Drill tiers", "%d/%d" % (pts["earned"], pts["possible"]),
                 "%d of %d drills tried" % (pts["drillsAttempted"], pts["drillsTotal"])),
                ("Streak", str(streak), "consecutive days"),
                ("Goals met", "%d/%d" % (
                    sum(1 for g in goals_mod.summary(self.db) if g["met"]),
                    len(self.db.settings.get("goals", []))), "targets you set"),
            ):
                self._card(cards, title, value, sub).pack(side="left", padx=(0, 10))

            for row in drills_mod.plan(self.db, 3):
                card = ttk.Frame(plan_box, style="Card.TFrame", padding=(12, 10))
                card.pack(fill="x", pady=(0, 8))
                top = ttk.Frame(card, style="Card.TFrame")
                top.pack(fill="x")
                ttk.Label(top, text=row["name"], style="Card.TLabel",
                          font=_font(11, True)).pack(side="left")
                ttk.Label(top, text="  " + (row["tier"] or "unranked"),
                          style="CardMuted.TLabel").pack(side="left")
                ttk.Button(top, text="Log it", style="Accent.TButton",
                           command=lambda r=row: self.start_drill(r["id"])
                           ).pack(side="right")
                d = drills_mod.get_drill(row["id"])
                ttk.Label(card, text="%s  •  %.0f m  •  %d shots  •  %s"
                          % (row["family"], d["distance_m"], d["shots"], row["reason"]),
                          style="CardMuted.TLabel").pack(anchor="w")

            recent.delete(*recent.get_children())
            for s in sorted(sessions, key=lambda s: s.date, reverse=True)[:8]:
                tool = self.db.get_tool(s.tool_id)
                drill = s.drill_id or "--"
                score = s.stats.total_score
                recent.insert("", "end", values=(
                    s.date, tool.name if tool else s.tool_id, drill,
                    _fmt(s.stats.extreme_spread_mm), _fmt(score)))

            goal_box.delete(*goal_box.get_children())
            for g in goals_mod.summary(self.db):
                status = "met" if g["met"] else ("working" if g["met"] is False else "no data")
                goal_box.insert("", "end", values=(
                    "%s (%s)" % (g["metric"], g["scope"]),
                    "%s%g" % ("<=" if g["lowerIsBetter"] else ">=", g["target"]),
                    _fmt(g["best"]), status))

        self._refreshers.append(refresh)

    def start_drill(self, drill_id: str) -> None:
        """Jump to the log tab with this drill pre-selected."""
        self.nb.select(2)
        self._log_drill.set(drills_mod.get_drill(drill_id)["name"])
        self._on_drill_pick()

    # ------------------------------------------------------------------ #
    # Tab 2: Drills
    # ------------------------------------------------------------------ #
    def _tab_drills(self, root: ttk.Frame) -> None:
        pal = self.pal
        ttk.Label(root, text="Drill standards", style="H2.TLabel").pack(anchor="w")
        ttk.Label(root, text="Four tiers per drill: %s. Practice standards for "
                             "this app only -- not an official classification."
                  % " → ".join(drills_mod.TIERS),
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 8))

        body = ttk.Frame(root)
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True, padx=(0, 12))
        tv = self._tree(left,
                        ("drill", "family", "metric", "best", "tier", "next", "tries"),
                        (150, 96, 88, 66, 84, 90, 50), height=16)
        right = ttk.Frame(body, style="Panel.TFrame", padding=12, width=430)
        right.pack(side="right", fill="both")
        right.pack_propagate(False)
        detail = tk.Text(right, bg=pal["panel"], fg=pal["fg"], relief="flat",
                         wrap="word", font=_font(10), width=44,
                         insertbackground=pal["fg"])
        detail.pack(fill="both", expand=True)
        detail.tag_configure("h", foreground=pal["accent"], font=_font(12, True))
        detail.tag_configure("sub", foreground=pal["muted"], font=_font(9))
        detail.tag_configure("k", foreground=pal["good"], font=_font(10, True))
        btns = ttk.Frame(right, style="Panel.TFrame")
        btns.pack(fill="x", pady=(10, 0))
        self._drill_sel = None  # type: Optional[str]
        ttk.Button(btns, text="Log this drill", style="Accent.TButton",
                   command=lambda: self._drill_sel and self.start_drill(self._drill_sel)
                   ).pack(side="left")

        def show(drill_id: str) -> None:
            self._drill_sel = drill_id
            d = drills_mod.get_drill(drill_id)
            row = drills_mod.standing(self.db, d)
            detail.configure(state="normal")
            detail.delete("1.0", "end")
            detail.insert("end", d["name"] + "\n", "h")
            detail.insert("end", "%s  •  %.0f m  •  %d shots  •  %s\n\n"
                          % (d["family"], d["distance_m"], d["shots"], d["target"]), "sub")
            detail.insert("end", d["why"] + "\n\n")
            detail.insert("end", "How to run it\n", "k")
            for step in d["how"]:
                detail.insert("end", "  • %s\n" % step)
            detail.insert("end", "\nCues\n", "k")
            for cue in d["cues"]:
                detail.insert("end", "  • %s\n" % cue)
            detail.insert("end", "\nStandards (%s, %s)\n"
                          % (row["metric"], row["unit"]), "k")
            for tier, cut in zip(drills_mod.TIERS, d["cutoffs"]):
                mark = "✓" if (row["tier"] and
                                    drills_mod.TIERS.index(row["tier"])
                                    >= drills_mod.TIERS.index(tier)) else " "
                detail.insert("end", "  %s %-9s %s %g\n"
                              % (mark, tier,
                                 "≤" if row["lowerIsBetter"] else "≥", cut))
            detail.insert("end", "\nYour standing\n", "k")
            detail.insert("end", "  attempts %d   best %s   latest %s\n"
                          % (row["attempts"], _fmt(row["best"]), _fmt(row["latest"])))
            if row["nextCutoff"]:
                detail.insert("end", "  next: %s at %g %s\n"
                              % (row["nextTier"], row["nextCutoff"], row["unit"]))
            detail.configure(state="disabled")

        def on_select(_event=None):
            sel = tv.selection()
            if sel:
                show(tv.item(sel[0], "tags")[0])

        tv.bind("<<TreeviewSelect>>", on_select)
        tv.bind("<Double-1>", lambda e: self._drill_sel and self.start_drill(self._drill_sel))

        def refresh():
            keep = self._drill_sel
            tv.delete(*tv.get_children())
            for row in drills_mod.standings(self.db):
                nxt = ("--" if not row["nextTier"]
                       else "%s %g" % (row["nextTier"], row["nextCutoff"]))
                tv.insert("", "end", tags=(row["id"],), values=(
                    row["name"], row["family"], row["metric"],
                    _fmt(row["best"]), row["tier"] or "–", nxt, row["attempts"]))
            children = tv.get_children()
            if children:
                target = children[0]
                for c in children:
                    if tv.item(c, "tags")[0] == keep:
                        target = c
                tv.selection_set(target)
                on_select()

        self._refreshers.append(refresh)

    # ------------------------------------------------------------------ #
    # Tab 3: Log a session
    # ------------------------------------------------------------------ #
    def _tab_log(self, root: ttk.Frame) -> None:
        pal = self.pal
        body = ttk.Frame(root)
        body.pack(fill="both", expand=True)
        form = ttk.Frame(body, style="Panel.TFrame", padding=12, width=330)
        form.pack(side="left", fill="y")
        form.pack_propagate(False)
        rightside = ttk.Frame(body)
        rightside.pack(side="right", fill="both", expand=True, padx=(12, 0))

        self._log_tool = tk.StringVar()
        self._log_drill = tk.StringVar(value="(free practice)")
        self._log_target = tk.StringVar(value="")
        self._log_dist = tk.StringVar(value="10")
        self._log_date = tk.StringVar(value=date.today().isoformat())
        self._log_bbs = tk.StringVar()
        self._log_notes = tk.StringVar()

        def field(label, var, values=None, cb=None):
            ttk.Label(form, text=label, style="Panel.TLabel",
                      font=_font(9, True)).pack(anchor="w", pady=(8, 2))
            if values is None:
                w = ttk.Entry(form, textvariable=var)
            else:
                w = ttk.Combobox(form, textvariable=var, values=values,
                                 state="readonly")
                if cb:
                    w.bind("<<ComboboxSelected>>", lambda e: cb())
            w.pack(fill="x")
            return w

        ttk.Label(form, text="New session", style="Panel.TLabel",
                  font=_font(12, True)).pack(anchor="w")
        self._w_tool = field("Tool", self._log_tool, [])
        self._w_drill = field("Drill", self._log_drill,
                              ["(free practice)"] + [d["name"] for d in drills_mod.all_drills()],
                              cb=lambda: self._on_drill_pick())
        faces = targets_mod.list_targets()
        if faces and not self._log_target.get():
            self._log_target.set(faces[0])
        self._w_target = field("Target face", self._log_target,
                               faces, cb=lambda: self._sync_canvas())
        field("Distance (m)", self._log_dist)
        field("Date", self._log_date)
        field(_term("projectiles").title(), self._log_bbs)
        field("Notes", self._log_notes)

        row = ttk.Frame(form, style="Panel.TFrame")
        row.pack(fill="x", pady=(14, 0))
        ttk.Button(row, text="From image…", command=self._load_image).pack(side="left")
        ttk.Button(row, text="Print face…", command=self._print_face).pack(side="left", padx=4)
        ttk.Button(row, text="Undo", command=lambda: self.canvas.undo()).pack(side="left", padx=4)
        ttk.Button(row, text="Clear", command=lambda: self.canvas.clear()).pack(side="left")
        ttk.Button(form, text="Save session", style="Accent.TButton",
                   command=self._save_session).pack(fill="x", pady=(10, 0))

        self.canvas = TargetCanvas(rightside, pal, on_change=self._on_shots)
        self.canvas.pack(fill="both", expand=True)
        self._stats_lbl = tk.Label(rightside, text="", bg=pal["bg"], fg=pal["fg"],
                                   justify="left", anchor="w", font=_font(10))
        self._stats_lbl.pack(fill="x", pady=(8, 0))

        def refresh():
            names = [t.name for t in self.db.tools.values()]
            self._w_tool.configure(values=names)
            if names and self._log_tool.get() not in names:
                self._log_tool.set(names[0])
            self._sync_canvas()

        self._refreshers.append(refresh)

    def _on_drill_pick(self) -> None:
        name = self._log_drill.get()
        for d in drills_mod.all_drills():
            if d["name"] == name:
                self._log_target.set(d["target"])
                self._log_dist.set("%g" % d["distance_m"])
                self.say("%s: %d shots at %g m on %s"
                         % (d["name"], d["shots"], d["distance_m"], d["target"]))
                break
        self._sync_canvas()

    def _sync_canvas(self) -> None:
        try:
            spec = targets_mod.get_target(self._log_target.get())
        except KeyError:
            spec = None
        tool = self.db.find_tool(self._log_tool.get() or "")
        self.canvas.projectile_mm = (tool.projectile_mm if tool and tool.projectile_mm else 6.0)
        self.canvas.set_target(spec)
        self._on_shots(self.canvas.shots)

    def _on_shots(self, shots: List[Shot]) -> None:
        if not shots:
            self._stats_lbl.configure(text="No shots yet -- click the target to "
                                           "place them, or load an image.")
            return
        try:
            spec = targets_mod.get_target(self._log_target.get())
        except KeyError:
            spec = None
        tool = self.db.find_tool(self._log_tool.get() or "")
        stats = analyze_group(shots, target=spec,
                              projectile_mm=(tool.projectile_mm if tool and tool.projectile_mm else 0.0))
        try:
            dist = float(self._log_dist.get())
        except ValueError:
            dist = None
        mrad = tracker.mm_to_mrad(stats.extreme_spread_mm, dist)
        parts = [
            "%d shots" % stats.shot_count,
            "group %s mm" % _fmt(stats.extreme_spread_mm),
            "mean radius %s mm" % _fmt(stats.mean_radius_mm),
            "zero error %s mm" % _fmt(stats.poa_offset_mm),
        ]
        if mrad is not None:
            parts.insert(2, "%s mrad / %s MOA"
                         % (_fmt(mrad, 2), _fmt(tracker.mrad_to_moa(mrad), 2)))
        if stats.total_score is not None:
            parts.append("score %s/%s" % (_fmt(stats.total_score),
                                          _fmt(stats.max_possible_score, 0)))
        advice = tracker.format_correction(
            tracker.sight_correction(
                stats, dist, tool.sight_click_mrad if tool else None), dist)
        text = "   •   ".join(parts)
        if advice:
            text += "\n" + advice
        self._stats_lbl.configure(text=text)

    def _print_face(self) -> None:
        """Write the chosen face at true scale and open it for printing."""
        try:
            spec = targets_mod.get_target(self._log_target.get())
        except KeyError:
            messagebox.showwarning("No face", "Pick a target face first.")
            return
        paper = self.db.settings.get("paper", "a4")
        try:
            dist = float(self._log_dist.get()) if self._log_dist.get() else None
        except ValueError:
            dist = None
        try:
            page = render.target_html(spec, distance_m=dist, paper=paper)
        except (KeyError, ValueError) as e:
            messagebox.showerror("Can't print that face", str(e))
            return
        path = os.path.join(tempfile.gettempdir(),
                            "marksman-face-%s.html" % abs(hash(spec.name)))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(page)
        webbrowser.open("file:///" + path.replace("\\", "/"))
        self.say("Opened %s for printing -- print at 100%%, not 'fit to page'."
                 % spec.name)

    def _load_image(self) -> None:
        path = filedialog.askopenfilename(
            title="Target photo (marked hits)",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All", "*.*")])
        if not path:
            return
        from . import vision
        try:
            spec = targets_mod.get_target(self._log_target.get())
        except KeyError:
            spec = None
        try:
            result = vision.analyze_image(
                path, mode="marker", color="red", auto_center=True,
                face_width_mm=(spec.face_width_mm if spec else None))
        except Exception as e:
            messagebox.showerror("Image analysis failed", str(e))
            return
        if not result.shots:
            messagebox.showwarning(
                "Nothing found",
                "No marked hits detected. Mark each hit with a red dot or ring, "
                "or place the shots by clicking the target.")
            return
        self._pending_image = path
        self.canvas.set_shots(result.shots)
        self.say("Detected %d shots from %s (%.4f mm/px)"
                 % (len(result.shots), os.path.basename(path), result.mm_per_px))

    def _save_session(self) -> None:
        shots = list(self.canvas.shots)
        if not shots:
            messagebox.showwarning("No shots", "Place at least one shot first.")
            return
        tool = self.db.find_tool(self._log_tool.get() or "")
        if tool is None:
            messagebox.showwarning("No tool", "Add a tool on the Tools tab first.")
            return
        try:
            spec = targets_mod.get_target(self._log_target.get())
        except KeyError:
            spec = None
        try:
            dist = float(self._log_dist.get()) if self._log_dist.get() else None
        except ValueError:
            messagebox.showwarning("Distance", "Distance must be a number.")
            return
        try:
            when = date.fromisoformat(self._log_date.get()).isoformat()
        except ValueError:
            messagebox.showwarning("Date", "Date must be YYYY-MM-DD.")
            return

        drill_id = ""
        for d in drills_mod.all_drills():
            if d["name"] == self._log_drill.get():
                drill_id = d["id"]
        stats = analyze_group(shots, target=spec, projectile_mm=tool.projectile_mm or 0.0)
        sid = "%s-%s" % (when, os.urandom(3).hex())
        session = Session(
            id=sid, tool_id=tool.id, date=when, shots=shots, stats=stats,
            distance_m=dist, target_name=spec.name if spec else "",
            projectiles=self._log_bbs.get(), drill_id=drill_id,
            image_path=getattr(self, "_pending_image", ""),
            notes=self._log_notes.get())
        self.db.add_session(session)
        self.save()
        self._pending_image = ""

        msg = "Saved %s: %d shots, group %s mm" % (sid, len(shots),
                                                   _fmt(stats.extreme_spread_mm))
        if drill_id:
            row = drills_mod.standing(self.db, drills_mod.get_drill(drill_id))
            if row["tier"]:
                msg += "  —  %s tier" % row["tier"]
        self.canvas.clear()
        self.refresh()
        self.say(msg)

    # ------------------------------------------------------------------ #
    # Tab 4: Progress
    # ------------------------------------------------------------------ #
    def _tab_progress(self, root: ttk.Frame) -> None:
        controls = ttk.Frame(root)
        controls.pack(fill="x")
        scope = tk.StringVar(value="Overall")
        metric = tk.StringVar(value="extreme_spread_mm")
        ttk.Label(controls, text="Scope").pack(side="left")
        scope_box = ttk.Combobox(controls, textvariable=scope, state="readonly",
                                 width=28)
        scope_box.pack(side="left", padx=(6, 16))
        ttk.Label(controls, text="Metric").pack(side="left")
        metric_box = ttk.Combobox(
            controls, textvariable=metric, state="readonly", width=24,
            values=["extreme_spread_mm", "mean_radius_mm", "group_size_mrad",
                    "group_size_moa", "poa_offset_mm", "score_pct"])
        metric_box.pack(side="left", padx=6)

        chart = Chart(root, self.pal, height=260)
        chart.pack(fill="both", expand=True, pady=10)
        table = self._tree(root, ("metric", "unit", "best", "average", "latest",
                                  "trend", "n"),
                           (190, 70, 74, 78, 74, 90, 44), height=7)

        def sessions_for(label: str):
            if label == "Overall":
                return self.db.all_sessions()
            kind, _, name = label.partition(": ")
            if kind == "Tool":
                tool = self.db.find_tool(name)
                return self.db.sessions_for_tool(tool.id) if tool else []
            if kind == "Category":
                return self.db.sessions_for_category(name)
            if kind == "Drill":
                return drills_mod.attempts(self.db, name)
            return self.db.all_sessions()

        def redraw(*_a):
            rep = tracker.build_report(sessions_for(scope.get()), "gui", scope.get())
            key = metric.get()
            m = rep.metrics.get(key)
            points = [(r["date"], r.get(key if key in r else "extreme_spread_mm"))
                      for r in rep.series]
            if key in ("group_size_moa",):     # not in the per-session series
                points = [(r["date"],
                           tracker.mrad_to_moa(r["group_size_mrad"]))
                          for r in rep.series]
            title = "%s  —  %s" % (scope.get(), m.name if m else key)
            chart.set_series(points, title,
                             lower_is_better=(m.lower_is_better if m else True))
            table.delete(*table.get_children())
            for k, mt in rep.metrics.items():
                table.insert("", "end", values=(
                    mt.name, mt.unit, _fmt(mt.best), _fmt(mt.average),
                    _fmt(mt.latest), mt.direction, mt.count))

        scope_box.bind("<<ComboboxSelected>>", redraw)
        metric_box.bind("<<ComboboxSelected>>", redraw)

        def refresh():
            options = ["Overall"]
            options += ["Tool: %s" % t.name for t in self.db.tools.values()]
            cats = sorted(set(t.category for t in self.db.tools.values()))
            options += ["Category: %s" % c for c in cats]
            options += ["Drill: %s" % d["id"] for d in drills_mod.all_drills()
                        if drills_mod.attempts(self.db, d["id"])]
            scope_box.configure(values=options)
            if scope.get() not in options:
                scope.set("Overall")
            redraw()

        self._refreshers.append(refresh)

    # ------------------------------------------------------------------ #
    # Tab 5: Sessions
    # ------------------------------------------------------------------ #
    def _tab_sessions(self, root: ttk.Frame) -> None:
        bar = ttk.Frame(root)
        bar.pack(fill="x", pady=(0, 8))
        tv = self._tree(root, ("date", "id", "tool", "drill", "dist", "shots",
                               "group", "mean r", "zero", "score"),
                        (86, 130, 120, 110, 50, 50, 66, 66, 60, 62), height=20)

        def selected() -> Optional[Session]:
            sel = tv.selection()
            return self.db.sessions.get(tv.item(sel[0], "tags")[0]) if sel else None

        def do_render():
            s = selected()
            if not s:
                return
            out = os.path.join(self.db.recreations_dir, "%s.png" % s.id)
            spec = None
            if s.target_name:
                try:
                    spec = targets_mod.get_target(s.target_name)
                except KeyError:
                    spec = None
            render.save_recreation(s, out, target=spec)
            s.recreation_path = out
            self.save()
            self.say("Rendered %s" % out)

        def do_delete():
            s = selected()
            if not s:
                return
            if messagebox.askyesno("Delete session",
                                   "Delete session %s? This cannot be undone."
                                   % s.id):
                del self.db.sessions[s.id]
                self.save()
                self.refresh()
                self.say("Deleted %s" % s.id)

        ttk.Button(bar, text="Render diagram", command=do_render).pack(side="left")
        ttk.Button(bar, text="Delete", command=do_delete).pack(side="left", padx=6)
        ttk.Button(bar, text="Export CSV…",
                   command=lambda: self.export_data("csv")).pack(side="left")

        def refresh():
            tv.delete(*tv.get_children())
            for s in sorted(self.db.all_sessions(), key=lambda s: s.date, reverse=True):
                st = s.stats
                tool = self.db.get_tool(s.tool_id)
                tv.insert("", "end", tags=(s.id,), values=(
                    s.date, s.id, tool.name if tool else s.tool_id,
                    s.drill_id or "--",
                    "%g" % s.distance_m if s.distance_m else "--",
                    st.shot_count if st else 0,
                    _fmt(st.extreme_spread_mm) if st else "--",
                    _fmt(st.mean_radius_mm) if st else "--",
                    _fmt(st.poa_offset_mm) if st else "--",
                    _fmt(st.total_score) if st else "--"))

        self._refreshers.append(refresh)

    # ------------------------------------------------------------------ #
    # Tab 6: Goals
    # ------------------------------------------------------------------ #
    def _tab_goals(self, root: ttk.Frame) -> None:
        form = ttk.Frame(root, style="Panel.TFrame", padding=10)
        form.pack(fill="x")
        metric = tk.StringVar(value="group_size")
        target = tk.StringVar(value="30")
        tool = tk.StringVar(value="(overall)")
        ttk.Label(form, text="Metric", style="Panel.TLabel").pack(side="left")
        ttk.Combobox(form, textvariable=metric, state="readonly", width=14,
                     values=list(goals_mod.METRICS)).pack(side="left", padx=(6, 12))
        ttk.Label(form, text="Target", style="Panel.TLabel").pack(side="left")
        ttk.Entry(form, textvariable=target, width=8).pack(side="left", padx=(6, 12))
        ttk.Label(form, text="Tool", style="Panel.TLabel").pack(side="left")
        tool_box = ttk.Combobox(form, textvariable=tool, state="readonly", width=20)
        tool_box.pack(side="left", padx=6)

        tv = self._tree(root, ("id", "metric", "target", "scope", "best",
                               "latest", "status"),
                        (70, 120, 90, 150, 78, 78, 80), height=14)

        def add():
            try:
                value = float(target.get())
            except ValueError:
                messagebox.showwarning("Target", "Target must be a number.")
                return
            tid = None
            if tool.get() != "(overall)":
                found = self.db.find_tool(tool.get())
                tid = found.id if found else None
            goal = goals_mod.new_goal(metric.get(), value, tool_id=tid)
            self.db.settings.setdefault("goals", []).append(goal)
            self.save()
            self.refresh()
            self.say("Goal %s set" % goal["id"])

        def remove():
            sel = tv.selection()
            if not sel:
                return
            gid = tv.item(sel[0], "values")[0]
            self.db.settings["goals"] = [
                g for g in self.db.settings.get("goals", []) if g["id"] != gid]
            self.save()
            self.refresh()
            self.say("Goal %s removed" % gid)

        ttk.Button(form, text="Add goal", style="Accent.TButton",
                   command=add).pack(side="left", padx=(12, 0))
        ttk.Button(form, text="Remove selected", command=remove).pack(side="left", padx=6)

        def refresh():
            tool_box.configure(values=["(overall)"] +
                               [t.name for t in self.db.tools.values()])
            tv.delete(*tv.get_children())
            for g in goals_mod.summary(self.db):
                status = "met" if g["met"] else ("working" if g["met"] is False
                                                 else "no data")
                tv.insert("", "end", values=(
                    g["id"], "%s (%s)" % (g["metric"], g["unit"]),
                    "%s%g" % ("<=" if g["lowerIsBetter"] else ">=", g["target"]),
                    g["scope"], _fmt(g["best"]), _fmt(g["latest"]), status))

        self._refreshers.append(refresh)

    # ------------------------------------------------------------------ #
    # Tab 7: AI coach
    # ------------------------------------------------------------------ #
    def _tab_coach(self, root: ttk.Frame) -> None:
        bar = ttk.Frame(root)
        bar.pack(fill="x", pady=(0, 8))
        out = self._text(root)

        def export():
            written = coach_mod.write_request(
                self.db, coach_mod.coach_dir(self.db),
                coach_mod.DEFAULT_INSTRUCTION,
                datetime.now().isoformat(timespec="seconds"))
            self.save()
            show()
            out.insert("1.0", "Wrote:\n  %s\n\nPoint a Claude Code agent at that "
                              "folder (it reads CLAUDE.md), or paste request.md "
                              "into any Claude chat. It writes reply.json; then "
                              "press Apply reply.\n\n"
                       % "\n  ".join(sorted(written.values())))
            self.say("Coach request written to %s" % coach_mod.coach_dir(self.db))

        def apply():
            path = os.path.join(coach_mod.coach_dir(self.db), "reply.json")
            if not os.path.exists(path):
                path = filedialog.askopenfilename(
                    title="Coach reply", filetypes=[("JSON", "*.json")])
                if not path:
                    return
            try:
                reply = coach_mod.read_reply(path)
                result = coach_mod.apply_reply(
                    self.db, reply, datetime.now().isoformat(timespec="seconds"))
            except Exception as e:
                messagebox.showerror("Could not apply reply", str(e))
                return
            self.save()
            show()
            self.say("Applied: %s" % result.get("reason", "coaching updated"))

        def show():
            out.delete("1.0", "end")
            c = self.db.settings.get("coaching")
            if not c:
                out.insert("end", "No coaching yet.\n\nPress 'Export request' to "
                                  "write the cowork folder, have an AI agent read "
                                  "it and write reply.json, then press 'Apply "
                                  "reply'.\n\nThis is practice feedback, not "
                                  "professional coaching, medical or safety "
                                  "advice.\n")
                return
            out.insert("end", "Updated %s\n\n" % c.get("updatedAt", "?"))
            if c.get("analysis"):
                out.insert("end", "ANALYSIS\n%s\n\n" % c["analysis"])
            if c.get("focus"):
                out.insert("end", "FOCUS\n%s\n\n" % c["focus"])
            for drill in c.get("drills", []):
                out.insert("end", "DRILL  %s\n  %s\n\n"
                           % (drill.get("name", "?"), drill.get("detail", "")))
            for tip in c.get("toolTips", []):
                tool = self.db.get_tool(tip.get("toolId", ""))
                out.insert("end", "TOOL  %s\n  %s\n\n"
                           % (tool.name if tool else tip.get("toolId", "?"),
                              tip.get("tip", "")))
            for note in c.get("notes", []):
                out.insert("end", "NOTE  %s\n" % note.get("text", ""))

        ttk.Button(bar, text="Export request", style="Accent.TButton",
                   command=export).pack(side="left")
        ttk.Button(bar, text="Apply reply", command=apply).pack(side="left", padx=6)
        ttk.Button(bar, text="Refresh", command=show).pack(side="left")
        ttk.Label(bar, text="  no API key, no network calls — it is a folder",
                  style="Muted.TLabel").pack(side="left", padx=8)
        self._refreshers.append(show)

    # ------------------------------------------------------------------ #
    # Tab 8: Tools
    # ------------------------------------------------------------------ #
    def _tab_tools(self, root: ttk.Frame) -> None:
        form = ttk.Frame(root, style="Panel.TFrame", padding=10)
        form.pack(fill="x")
        tid, name = tk.StringVar(), tk.StringVar()
        cat = tk.StringVar(value="Other")
        bb = tk.StringVar(value="6.0")
        clickvar = tk.StringVar(value="")     # e.g. "1/4moa", "0.1mrad"

        for label, var, width in (("Id", tid, 12), ("Name", name, 22)):
            ttk.Label(form, text=label, style="Panel.TLabel").pack(side="left")
            ttk.Entry(form, textvariable=var, width=width).pack(side="left", padx=(6, 12))
        ttk.Label(form, text="Category", style="Panel.TLabel").pack(side="left")
        ttk.Combobox(form, textvariable=cat, state="readonly", width=14,
                     values=packs_mod.categories()).pack(side="left", padx=(6, 12))
        ttk.Label(form, text=_term("projectile").title() + " mm",
                  style="Panel.TLabel").pack(side="left")
        ttk.Entry(form, textvariable=bb, width=6).pack(side="left", padx=6)
        # Optional: turns a zero error into "4 clicks left" on the Log tab.
        ttk.Label(form, text="Sight click", style="Panel.TLabel").pack(side="left")
        ttk.Entry(form, textvariable=clickvar, width=9).pack(side="left", padx=6)

        tv = self._tree(root, ("id", "name", "category", "bb mm", "sessions"),
                        (110, 200, 130, 70, 80), height=14)

        def add():
            if not tid.get().strip() or not name.get().strip():
                messagebox.showwarning("Tool", "Id and name are both required.")
                return
            try:
                click_mrad = (tracker.parse_click(clickvar.get())
                              if clickvar.get().strip() else None)
                self.db.add_tool(Tool(tid.get().strip(), name.get().strip(),
                                      category=cat.get(),
                                      projectile_mm=float(bb.get()) if bb.get() else None,
                                      sight_click_mrad=click_mrad))
            except ValueError as e:
                messagebox.showwarning("Tool", str(e))
                return
            self.save()
            self.refresh()
            self.say("Added tool %s" % tid.get())
            tid.set("")
            name.set("")

        def remove():
            sel = tv.selection()
            if not sel:
                return
            key = tv.item(sel[0], "values")[0]
            if self.db.sessions_for_tool(key):
                messagebox.showwarning(
                    "Tool in use",
                    "%s has sessions logged against it; delete those first." % key)
                return
            self.db.tools.pop(key, None)
            self.save()
            self.refresh()
            self.say("Removed tool %s" % key)

        ttk.Button(form, text="Add tool", style="Accent.TButton",
                   command=add).pack(side="left", padx=(12, 0))
        ttk.Button(form, text="Remove selected", command=remove).pack(side="left", padx=6)

        def refresh():
            tv.delete(*tv.get_children())
            for t in self.db.tools.values():
                tv.insert("", "end", values=(
                    t.id, t.name, t.category, _fmt(t.projectile_mm, 1),
                    len(self.db.sessions_for_tool(t.id))))

        self._refreshers.append(refresh)


def launch(db_path: str = DEFAULT_DB_PATH, theme_key: Optional[str] = None) -> int:
    """Open the desktop window. Returns a process exit code."""
    try:
        app = App(db_path, theme_key)
    except tk.TclError as e:
        print("error: could not open a window (%s). A desktop session with Tk "
              "is required; the CLI works headless." % e, file=sys.stderr)
        return 1
    app.mainloop()
    return 0
