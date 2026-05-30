import unittest

from marksman.models import Shot, Weapon, Session
from marksman.grouping import analyze_group
from marksman.targets import uniform_target
from marksman import tracker


def make_session(sid, date, weapon_id, spread_mm, distance_m=10.0):
    # Build two shots a known extreme spread apart, centred on POA.
    half = spread_mm / 2.0
    shots = [Shot(-half, 0.0), Shot(half, 0.0)]
    st = analyze_group(shots)
    return Session(id=sid, weapon_id=weapon_id, date=date, shots=shots,
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
        rep = tracker.build_report(sessions, "weapon", "W1")
        self.assertEqual(rep.session_count, 3)
        m = rep.metrics["extreme_spread_mm"]
        self.assertAlmostEqual(m.best, 10.0)
        self.assertAlmostEqual(m.latest, 10.0)
        self.assertAlmostEqual(m.first, 30.0)
        self.assertEqual(m.direction, "improving")
        self.assertLess(m.slope_per_day, 0.0)

    def test_angular_metric_in_report(self):
        sessions = [make_session("a", "2026-01-01", "w1", 10.0, distance_m=10.0)]
        rep = tracker.build_report(sessions, "weapon", "W1")
        self.assertAlmostEqual(rep.metrics["group_size_mrad"].latest, 1.0)
        self.assertAlmostEqual(rep.metrics["group_size_moa"].latest, 3.43774677, places=4)

    def test_score_pct_higher_is_better(self):
        tgt = uniform_target("T", ten_ring_diameter_mm=10.0, ring_step_mm=5.0)
        # Both shots dead centre = 10 each = 20/20 = 100%.
        shots = [Shot(0, 0), Shot(0, 0)]
        st = analyze_group(shots, target=tgt)
        s = Session("s", "w1", "2026-01-01", shots=shots, stats=st, distance_m=10.0)
        rep = tracker.build_report([s], "weapon", "W1")
        self.assertAlmostEqual(rep.metrics["score_pct"].latest, 100.0)
        self.assertFalse(rep.metrics["score_pct"].lower_is_better)

    def test_by_category_and_weapon(self):
        weapons = {
            "w1": Weapon("w1", "Pistol A", category="Air Pistol"),
            "w2": Weapon("w2", "Rifle B", category="Air Rifle"),
        }
        sessions = [
            make_session("a", "2026-01-01", "w1", 20.0),
            make_session("b", "2026-01-02", "w2", 10.0),
        ]
        cats = tracker.progress_by_category(sessions, weapons)
        self.assertIn("Air Pistol", cats)
        self.assertIn("Air Rifle", cats)
        self.assertEqual(cats["Air Pistol"].session_count, 1)

        by_w = tracker.progress_by_weapon(sessions, weapons)
        self.assertEqual(by_w["w1"].scope_name, "Pistol A")

    def test_empty_report(self):
        rep = tracker.build_report([], "overall", "All")
        self.assertEqual(rep.session_count, 0)
        self.assertIsNone(rep.first_date)


if __name__ == "__main__":
    unittest.main()
