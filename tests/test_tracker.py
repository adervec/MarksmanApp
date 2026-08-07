import unittest

from marksman.models import Shot, Tool, Session
from marksman.grouping import analyze_group
from marksman.targets import uniform_target
from marksman import tracker


def make_session(sid, date, tool_id, spread_mm, distance_m=10.0):
    # Build two shots a known extreme spread apart, centred on POA.
    half = spread_mm / 2.0
    shots = [Shot(-half, 0.0), Shot(half, 0.0)]
    st = analyze_group(shots)
    return Session(id=sid, tool_id=tool_id, date=date, shots=shots,
                   stats=st, distance_m=distance_m)


class TestTracker(unittest.TestCase):
    def test_angular_conversion(self):
        self.assertAlmostEqual(tracker.mm_to_mrad(10.0, 10.0), 1.0)
        self.assertIsNone(tracker.mm_to_mrad(10.0, None))
        self.assertAlmostEqual(tracker.mrad_to_moa(1.0), 3.43774677, places=5)

    def test_improving_trend_group_size(self):
        # Group size shrinks over time -> improving (lower is better).
        sessions = [
            make_session("a", "2026-01-01", "w1", 30.0),
            make_session("b", "2026-01-08", "w1", 20.0),
            make_session("c", "2026-01-15", "w1", 10.0),
        ]
        rep = tracker.build_report(sessions, "tool", "W1")
        self.assertEqual(rep.session_count, 3)
        m = rep.metrics["extreme_spread_mm"]
        self.assertAlmostEqual(m.best, 10.0)
        self.assertAlmostEqual(m.latest, 10.0)
        self.assertAlmostEqual(m.first, 30.0)
        self.assertEqual(m.direction, "improving")
        self.assertLess(m.slope_per_day, 0.0)

    def test_angular_metric_in_report(self):
        sessions = [make_session("a", "2026-01-01", "w1", 10.0, distance_m=10.0)]
        rep = tracker.build_report(sessions, "tool", "W1")
        self.assertAlmostEqual(rep.metrics["group_size_mrad"].latest, 1.0)
        self.assertAlmostEqual(rep.metrics["group_size_moa"].latest, 3.43774677, places=4)

    def test_score_pct_higher_is_better(self):
        tgt = uniform_target("T", ten_ring_diameter_mm=10.0, ring_step_mm=5.0)
        # Both shots dead centre = 10 each = 20/20 = 100%.
        shots = [Shot(0, 0), Shot(0, 0)]
        st = analyze_group(shots, target=tgt)
        s = Session("s", "w1", "2026-01-01", shots=shots, stats=st, distance_m=10.0)
        rep = tracker.build_report([s], "tool", "W1")
        self.assertAlmostEqual(rep.metrics["score_pct"].latest, 100.0)
        self.assertFalse(rep.metrics["score_pct"].lower_is_better)

    def test_by_category_and_tool(self):
        tools = {
            "w1": Tool("w1", "AEG One", category="AEG"),
            "w2": Tool("w2", "GBB Rifle Two", category="GBB Rifle"),
        }
        sessions = [
            make_session("a", "2026-01-01", "w1", 20.0),
            make_session("b", "2026-01-02", "w2", 10.0),
        ]
        cats = tracker.progress_by_category(sessions, tools)
        self.assertIn("AEG", cats)
        self.assertIn("GBB Rifle", cats)
        self.assertEqual(cats["AEG"].session_count, 1)

        by_w = tracker.progress_by_tool(sessions, tools)
        self.assertEqual(by_w["w1"].scope_name, "AEG One")

    def test_parse_click_reads_what_is_on_the_turret(self):
        self.assertAlmostEqual(tracker.parse_click("0.1mrad"), 0.1)
        self.assertAlmostEqual(tracker.parse_click("0.1 MRAD"), 0.1)
        self.assertAlmostEqual(tracker.parse_click("1/4moa"), 0.25 / 3.43774677)
        self.assertAlmostEqual(tracker.parse_click("0.25moa"), 0.25 / 3.43774677)
        self.assertAlmostEqual(tracker.parse_click("1cm@100m"), 0.1)
        for bad in ("", "moa", "4", "1/0moa", "-1mrad", "0mrad", "9999moa",
                    "0.1 clicks", "drop table"):
            with self.assertRaises(ValueError, msg=bad):
                tracker.parse_click(bad)

    def test_sight_correction_points_back_at_the_aim_mark(self):
        stats = analyze_group([Shot(12.0, 8.0), Shot(14.0, 10.0), Shot(13.0, 6.0)])
        corr = tracker.sight_correction(stats, distance_m=10.0, click_mrad=0.1)
        # Group sits high and right, so the sight has to come down and left.
        self.assertLess(corr["dx_mm"], 0)
        self.assertLess(corr["dy_mm"], 0)
        self.assertEqual(corr["horizontal"], "left")
        self.assertEqual(corr["vertical"], "down")
        self.assertAlmostEqual(corr["mrad_x"], 1.3, places=2)   # 13 mm at 10 m
        self.assertEqual(corr["clicks_x"], 13)                  # at 0.1 mrad
        text = tracker.format_correction(corr, 10.0)
        self.assertIn("13 clicks left", text)
        self.assertIn("8 clicks down", text)

    def test_correction_degrades_gracefully(self):
        stats = analyze_group([Shot(12.0, 0.0), Shot(14.0, 0.0)])
        # No click value: angles. No distance either: millimetres.
        self.assertIn("MOA", tracker.format_correction(
            tracker.sight_correction(stats, 10.0), 10.0))
        self.assertIn("13.0 mm left", tracker.format_correction(
            tracker.sight_correction(stats)))
        # A centred group gets no advice at all.
        centred = analyze_group([Shot(-2.0, 0.0), Shot(2.0, 0.0)])
        self.assertEqual(tracker.format_correction(
            tracker.sight_correction(centred, 10.0), 10.0), "")

    def test_empty_report(self):
        rep = tracker.build_report([], "overall", "All")
        self.assertEqual(rep.session_count, 0)
        self.assertIsNone(rep.first_date)


if __name__ == "__main__":
    unittest.main()
