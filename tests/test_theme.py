import io
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout

from marksman import report, theme
from marksman.cli import main
from marksman.models import GroupStats
from marksman.storage import Database
from marksman.theme import (DEFAULT_SPARK, Painter, color_enabled, get_theme,
                            is_theme, list_themes)

_ESC = "\x1b"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(s):
    return _ANSI_RE.sub("", s)


class _FakeTTY(io.StringIO):
    def __init__(self, isatty):
        super().__init__()
        self._isatty = isatty

    def isatty(self):
        return self._isatty


SAMPLE_STATS = GroupStats(
    shot_count=5, extreme_spread_mm=12.34, mean_radius_mm=4.8, rms_radius_mm=5.1,
    cep_mm=4.2, std_x_mm=3.3, std_y_mm=2.1, bounding_width_mm=10.0,
    bounding_height_mm=9.0, center_x_mm=1.2, center_y_mm=-3.4, poa_offset_mm=3.6,
    poa_offset_angle_deg=290.0, total_score=96.3, max_possible_score=100.0,
    average_score=9.63)


class TestThemeRegistry(unittest.TestCase):
    def test_registry_integrity(self):
        themes = list_themes()
        self.assertGreaterEqual(len(themes), 8)               # a real "variety"
        keys = [t.key for t in themes]
        self.assertEqual(len(keys), len(set(keys)))            # unique keys
        self.assertEqual(keys[0], "mono")                      # plain default first
        for t in themes:
            self.assertTrue(t.key and t.key == t.key.lower())
            self.assertTrue(t.title and t.inspired_by)
            self.assertTrue(t.spark and len(t.spark) >= 2)
            self.assertTrue(t.rule_char and t.glyph)

    def test_get_theme_fallback(self):
        self.assertEqual(get_theme(None).key, "mono")
        self.assertEqual(get_theme("does-not-exist").key, "mono")
        self.assertEqual(get_theme("RECON").key, "recon")      # case-insensitive
        self.assertEqual(get_theme("  inferno ").key, "inferno")

    def test_is_theme(self):
        self.assertTrue(is_theme("recon"))
        self.assertTrue(is_theme("INFERNO"))
        self.assertFalse(is_theme("nope"))
        self.assertFalse(is_theme(None))
        self.assertFalse(is_theme(""))


class TestPainter(unittest.TestCase):
    def test_mono_is_always_plain(self):
        # Even enabled, the mono skin emits no escape codes.
        p = Painter(get_theme("mono"), enabled=True)
        for role in ("title", "label", "value", "accent", "good", "bad", "muted"):
            self.assertEqual(getattr(p, role)("hi"), "hi")

    def test_disabled_painter_unchanged(self):
        p = Painter(get_theme("inferno"), enabled=False)
        for role in ("title", "label", "value", "accent", "good", "bad", "muted"):
            out = getattr(p, role)("hi")
            self.assertEqual(out, "hi")
            self.assertNotIn(_ESC, out)

    def test_enabled_painter_wraps_and_strips_clean(self):
        p = Painter(get_theme("inferno"), enabled=True)
        out = p.title("Group")
        self.assertIn(_ESC, out)
        self.assertTrue(out.endswith(theme.RESET))
        self.assertEqual(strip_ansi(out), "Group")

    def test_trend_colours_by_meaning(self):
        p = Painter(get_theme("recon"), enabled=True)
        self.assertEqual(p.trend("improving", "x"), p.good("x"))
        self.assertEqual(p.trend("declining", "x"), p.bad("x"))
        self.assertEqual(p.trend("flat", "x"), p.muted("x"))
        self.assertEqual(p.trend("n/a", "x"), p.muted("x"))

    def test_spark_ramp_follows_enabled(self):
        th = get_theme("pandora")
        self.assertEqual(Painter(th, enabled=False).spark_ramp, DEFAULT_SPARK)
        self.assertEqual(Painter(th, enabled=True).spark_ramp, th.spark)


class TestColorEnabled(unittest.TestCase):
    def setUp(self):
        # Isolate from the ambient environment / CI settings.
        self._saved = {k: os.environ.pop(k, None)
                       for k in ("NO_COLOR", "MARKSMAN_NO_COLOR")}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_no_color_arg(self):
        self.assertFalse(color_enabled(_FakeTTY(True), no_color=True))

    def test_no_color_env(self):
        os.environ["NO_COLOR"] = "1"
        self.assertFalse(color_enabled(_FakeTTY(True)))

    def test_non_tty_disables(self):
        self.assertFalse(color_enabled(_FakeTTY(False)))

    def test_tty_enables(self):
        self.assertTrue(color_enabled(_FakeTTY(True)))


class TestSparkline(unittest.TestCase):
    def test_custom_ramp(self):
        out = report.sparkline([0.0, 1.0, 2.0], ramp="ABC")
        self.assertEqual(out, "ABC")

    def test_default_ramp_unchanged(self):
        a = report.sparkline([0.0, 1.0, 2.0])
        b = report.sparkline([0.0, 1.0, 2.0], ramp=DEFAULT_SPARK)
        self.assertEqual(a, b)
        self.assertNotIn(_ESC, a)


class TestReportPlainEquivalence(unittest.TestCase):
    """A disabled painter must not change the produced text at all."""

    def test_group_stats_plain(self):
        plain = report.format_group_stats(SAMPLE_STATS, "ISSF 10m Air Pistol", 10.0)
        themed_disabled = report.format_group_stats(
            SAMPLE_STATS, "ISSF 10m Air Pistol", 10.0,
            painter=Painter(get_theme("inferno"), enabled=False))
        self.assertEqual(plain, themed_disabled)
        self.assertNotIn(_ESC, plain)

    def test_group_stats_themed_strips_to_plain(self):
        plain = report.format_group_stats(SAMPLE_STATS, "ISSF 10m Air Pistol", 10.0)
        themed = report.format_group_stats(
            SAMPLE_STATS, "ISSF 10m Air Pistol", 10.0,
            painter=Painter(get_theme("orbital"), enabled=True))
        self.assertIn(_ESC, themed)
        self.assertEqual(strip_ansi(themed), plain)


class TestThemeCli(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "db.json")

    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--db", self.db] + list(args))
        return code, buf.getvalue()

    def test_list_runs_and_names_skins(self):
        code, out = self.run_cli("theme")
        self.assertEqual(code, 0)
        plain = strip_ansi(out)
        for key in ("mono", "recon", "inferno", "orbital"):
            self.assertIn(key, plain)

    def test_set_persists_and_rejects_unknown(self):
        code, _ = self.run_cli("theme", "set", "recon")
        self.assertEqual(code, 0)
        db = Database.load(self.db)
        self.assertEqual(db.settings.get("theme"), "recon")

        code, _ = self.run_cli("theme", "set", "bogus")
        self.assertEqual(code, 2)                # unknown skin rejected
        db = Database.load(self.db)
        self.assertEqual(db.settings.get("theme"), "recon")   # unchanged

    def test_preview_named_and_current(self):
        code, out = self.run_cli("theme", "preview", "inferno")
        self.assertEqual(code, 0)
        self.assertIn("Inferno", strip_ansi(out))

        self.run_cli("theme", "set", "recon")
        code, out = self.run_cli("theme", "preview")           # current skin
        self.assertEqual(code, 0)
        self.assertIn("Night Recon", strip_ansi(out))

    def test_reports_stay_plain_when_not_a_tty(self):
        # Captured output is not a terminal, so even a chosen skin stays plain.
        self.run_cli("weapon", "add", "--id", "ap1", "--name", "AP",
                     "--category", "Air Pistol", "--caliber-mm", "4.5", "--airgun")
        code, out = self.run_cli("--theme", "inferno", "analyze", "--weapon",
                                 "ap1", "--target", "ISSF 10m Air Pistol",
                                 "--distance", "10", "--shots", "0,0 2,0 0,2")
        self.assertEqual(code, 0)
        self.assertNotIn(_ESC, out)
        self.assertIn("Group size", out)

        code, out = self.run_cli("--theme", "inferno", "progress", "--weapon", "ap1")
        self.assertEqual(code, 0)
        self.assertNotIn(_ESC, out)

    def test_no_color_flag_forces_plain(self):
        code, out = self.run_cli("--no-color", "theme", "preview", "inferno")
        self.assertEqual(code, 0)
        self.assertNotIn(_ESC, out)


if __name__ == "__main__":
    unittest.main()
