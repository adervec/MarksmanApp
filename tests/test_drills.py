import unittest
from datetime import date, timedelta

from marksman import drills
from marksman.grouping import analyze_group
from marksman.models import Session, Shot, Tool
from marksman.storage import Database


def _db():
    db = Database()
    db.add_tool(Tool("aeg1", "Training AEG", category="AEG", bb_mm=6.0))
    return db


def _log(db, drill_id, spread_mm, when="2026-01-01", sid=None):
    """Add a session whose extreme spread is exactly ``spread_mm``."""
    shots = [Shot(-spread_mm / 2, 0.0), Shot(spread_mm / 2, 0.0)]
    db.add_session(Session(sid or ("s-" + when + drill_id), "aeg1", when,
                           shots=shots, stats=analyze_group(shots),
                           distance_m=10.0, drill_id=drill_id))


class TestCatalog(unittest.TestCase):
    def test_every_drill_is_well_formed(self):
        for d in drills.all_drills():
            self.assertEqual(len(d["cutoffs"]), len(drills.TIERS), d["id"])
            self.assertTrue(d["how"] and d["cues"] and d["why"], d["id"])
            drills.get_drill(d["id"])          # resolvable by id

    def test_cutoffs_get_harder_each_tier(self):
        from marksman.goals import METRICS
        for d in drills.all_drills():
            lower = METRICS[d["metric"]][2]
            pairs = list(zip(d["cutoffs"], d["cutoffs"][1:]))
            for a, b in pairs:
                self.assertTrue(b < a if lower else b > a,
                                "%s: %g -> %g not harder" % (d["id"], a, b))

    def test_unknown_drill_raises(self):
        with self.assertRaises(KeyError):
            drills.get_drill("no-such-drill")


class TestTiers(unittest.TestCase):
    def test_tier_boundaries_lower_is_better(self):
        d = drills.get_drill("group-10")        # 90 / 60 / 40 / 25 mm
        self.assertIsNone(drills.tier_for(d, 90.1))
        self.assertEqual(drills.tier_for(d, 90.0), "Rookie")    # on the line counts
        self.assertEqual(drills.tier_for(d, 41.0), "Steady")
        self.assertEqual(drills.tier_for(d, 25.0), "Marksman")
        self.assertIsNone(drills.tier_for(d, None))

    def test_tier_boundaries_higher_is_better(self):
        d = drills.get_drill("cqb-7")           # score %: 55 / 70 / 82 / 92
        self.assertIsNone(drills.tier_for(d, 54.9))
        self.assertEqual(drills.tier_for(d, 55.0), "Rookie")
        self.assertEqual(drills.tier_for(d, 99.0), "Marksman")

    def test_next_cutoff_walks_up_then_stops(self):
        d = drills.get_drill("group-10")
        self.assertEqual(drills.next_cutoff(d, None), 90.0)
        self.assertEqual(drills.next_cutoff(d, "Sharp"), 25.0)
        self.assertIsNone(drills.next_cutoff(d, "Marksman"))


class TestStanding(unittest.TestCase):
    def test_pr_drives_the_tier(self):
        db = _db()
        _log(db, "group-10", 70.0, "2026-01-01")
        _log(db, "group-10", 30.0, "2026-01-02")   # the PR
        _log(db, "group-10", 80.0, "2026-01-03")   # a bad day doesn't demote you
        row = drills.standing(db, drills.get_drill("group-10"))
        self.assertEqual(row["attempts"], 3)
        self.assertAlmostEqual(row["best"], 30.0)
        self.assertAlmostEqual(row["latest"], 80.0)
        self.assertEqual(row["tier"], "Sharp")
        self.assertEqual(row["nextTier"], "Marksman")

    def test_sessions_of_other_drills_are_ignored(self):
        db = _db()
        _log(db, "group-10", 30.0)
        _log(db, "cold-start", 5.0, "2026-01-02")
        self.assertEqual(drills.standing(db, drills.get_drill("group-10"))["attempts"], 1)

    def test_untouched_drill_is_empty_not_broken(self):
        row = drills.standing(_db(), drills.get_drill("precision-20"))
        self.assertEqual(row["attempts"], 0)
        self.assertIsNone(row["best"])
        self.assertIsNone(row["tier"])
        self.assertEqual(row["nextTier"], "Rookie")

    def test_tier_points_sum_the_ladder(self):
        db = _db()
        _log(db, "group-10", 20.0)            # Marksman = 4 points
        pts = drills.tier_points(db)
        self.assertEqual(pts["earned"], 4)
        self.assertEqual(pts["possible"], len(drills.all_drills()) * 4)
        self.assertEqual(pts["drillsAttempted"], 1)


class TestPlan(unittest.TestCase):
    def test_untried_drills_come_first(self):
        db = _db()
        _log(db, "group-10", 20.0, date.today().isoformat())
        picks = drills.plan(db, 3)
        self.assertEqual(len(picks), 3)
        self.assertTrue(all(p["attempts"] == 0 for p in picks))
        self.assertTrue(all(p["reason"] == "never attempted" for p in picks))

    def test_stale_beats_fresh_once_everything_is_tried(self):
        today = date(2026, 6, 1)
        db = _db()
        for i, d in enumerate(drills.all_drills()):
            # Everything logged today except one, parked 90 days ago.
            when = (today - timedelta(days=90 if d["id"] == "kneeling-10" else 0))
            _log(db, d["id"], 200.0, when.isoformat(), sid="s%d" % i)
        picks = drills.plan(db, 1, today=today)
        self.assertEqual(picks[0]["id"], "kneeling-10")
        self.assertIn("90 days", picks[0]["reason"])

    def test_plan_of_zero_is_empty(self):
        self.assertEqual(drills.plan(_db(), 0), [])


if __name__ == "__main__":
    unittest.main()
