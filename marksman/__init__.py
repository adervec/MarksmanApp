"""Marksman -- a progress tracker for marksmanship with anything.

The core of the package analyses marked-up images of targets (the groupings of
shots on a paper target), turns them into precise marksmanship metrics, and
tracks how those metrics change over time -- overall, per tool *category*,
and per specific *tool*.

Layers (lowest to highest):

* :mod:`marksman.models`    -- plain data: shots, tools, sessions, targets.
* :mod:`marksman.targets`   -- built-in target face specifications (ring sizes).
* :mod:`marksman.grouping`  -- the analysis maths (group size, mean radius, score).
* :mod:`marksman.imageio`   -- minimal PNG read/write (pure standard library).
* :mod:`marksman.vision`    -- extract shot positions from a marked-up image.
* :mod:`marksman.storage`   -- load/save the database (JSON).
* :mod:`marksman.tracker`   -- aggregate sessions into progress reports.
* :mod:`marksman.render`    -- redraw a result diagram from stored shot data.
* :mod:`marksman.theme`     -- visual "skins" for the terminal reports.
* :mod:`marksman.packs`     -- equipment packs: the content layer (drills, faces).
* :mod:`marksman.drills`    -- tiered standards, PRs and the adaptive plan.
* :mod:`marksman.goals`     -- user-set targets for a single metric.
* :mod:`marksman.coach`     -- the AI coach cowork folder (no network calls).
* :mod:`marksman.cli`       -- command line entry point.
* :mod:`marksman.gui`       -- the desktop app (tkinter).
* :mod:`marksman.web`       -- the phone/browser app served on your LAN.

Every layer is standard-library only -- there are no third-party dependencies.
"""

from .models import (
    Shot,
    Tool,
    Session,
    TargetSpec,
    Ring,
    GroupStats,
)
from .grouping import analyze_group, score_shot
from .render import render_session, save_recreation
from .theme import Theme, Painter, get_theme, list_themes

__all__ = [
    "Shot",
    "Tool",
    "Session",
    "TargetSpec",
    "Ring",
    "GroupStats",
    "analyze_group",
    "score_shot",
    "render_session",
    "save_recreation",
    "Theme",
    "Painter",
    "get_theme",
    "list_themes",
]

__version__ = "0.8.0"
