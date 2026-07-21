"""GUI smoke tests.

They drive the real widgets, so they need a display; on a headless box (CI)
Tk raises and every test here skips rather than failing the suite.
"""

import os
import tempfile
import unittest

from marksman.grouping import analyze_group
from marksman.models import Session, Shot, Tool
from marksman.storage import Database

try:
    import tkinter as tk
    from marksman import gui
except ImportError:                                  # no tkinter in this Python
    gui = None


def _display_available():
    if gui is None:
        return False
    try:
        root = tk.Tk()
    except tk.TclError:
        return False
    root.destroy()
    return True


HAVE_DISPLAY = _display_available()


@unittest.skipUnless(HAVE_DISPLAY, "no display / tkinter available")
class TestApp(unittest.TestCase):

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(self.path)
        db = Database(path=self.path)
        db.add_tool(Tool("aeg1", "Training AEG", category="AEG", bb_mm=6.0))
        shots = [Shot(-15.0, 0.0), Shot(15.0, 0.0)]
        db.add_session(Session("s1", "aeg1", "2026-01-01", shots=shots,
                               stats=analyze_group(shots), distance_m=10.0,
                               drill_id="group-10"))
        db.save()
        self.app = gui.App(self.path, "recon")

    def tearDown(self):
        self.app.destroy()
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_every_tab_builds_and_refreshes(self):
        for i in range(len(self.app.nb.tabs())):
            self.app.nb.select(i)
            self.app.update()
        self.app.refresh()                     # a second pass must be idempotent
        self.app.update()

    def test_canvas_click_places_a_shot_and_updates_stats(self):
        self.app.nb.select(2)
        self.app.update()
        canvas = self.app.canvas
        canvas.set_shots([])
        canvas.event_generate("<Button-1>", x=100, y=100)
        self.app.update()
        self.assertEqual(len(canvas.shots), 1)
        canvas.set_shots([Shot(-20.0, 0.0), Shot(20.0, 0.0)])
        self.app.update()
        self.assertIn("40.0", self.app._stats_lbl.cget("text"))
        canvas.undo()
        self.assertEqual(len(canvas.shots), 1)

    def test_saving_a_drill_session_persists_and_scores_the_tier(self):
        self.app.start_drill("group-10")       # jumps to the log tab, prefills
        self.app.update()
        self.assertEqual(self.app._log_target.get(), "Airsoft Practice 10m")
        self.app.canvas.set_shots([Shot(-10.0, 0.0), Shot(10.0, 0.0)])
        self.app._save_session()
        self.app.update()

        reloaded = Database.load(self.path)
        saved = [s for s in reloaded.all_sessions() if s.id != "s1"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].drill_id, "group-10")
        self.assertAlmostEqual(saved[0].stats.extreme_spread_mm, 20.0)
        self.assertIn("Marksman", self.app._status.cget("text"))

    def test_save_without_shots_is_refused(self):
        self.app.nb.select(2)
        self.app.canvas.set_shots([])
        before = len(self.app.db.sessions)
        # The warning dialog would block, so stub it out for this call.
        gui.messagebox.showwarning = lambda *a, **k: None
        self.app._save_session()
        self.assertEqual(len(self.app.db.sessions), before)


@unittest.skipUnless(gui is not None, "no tkinter")
class TestPalette(unittest.TestCase):
    def test_every_skin_has_a_palette(self):
        from marksman import theme as theme_mod
        for th in theme_mod.list_themes():
            self.assertIn(th.key, gui.PALETTES, th.key)
        for key in list(gui.PALETTES) + ["nonsense"]:
            pal = gui.palette(key)
            for role in ("bg", "fg", "accent", "good", "bad", "muted"):
                self.assertTrue(pal[role].startswith("#"), (key, role))


if __name__ == "__main__":
    unittest.main()
