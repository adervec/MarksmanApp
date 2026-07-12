import unittest

from marksman.models import Shot, Tool, Session
from marksman.grouping import analyze_group
from marksman.storage import Database
from marksman import goals


def _db(spread_mm):
    db = Database()
    db.add_tool(Tool("aeg1", "Training AEG", category="AEG", bb_mm=6.0))
    shots = [Shot(-spread_mm / 2, 0.0), Shot(spread_mm / 2, 0.0)]
    db.add_session(Session("s0", "aeg1", "2026-01-01", shots=shots,
                           stats=analyze_group(shots), distance_m=10.0))
    return db


class TestGoals(unittest.TestCase):
    def test_new_goal_validates_metric(self):
        with self.assertRaises(ValueError):
            goals.new_goal("nonsense", 10.0)
        g = goals.new_goal("group_size", 30.0, tool_id="aeg1", note="tighten up")
        self.assertEqual(g["metric"], "group_size")
        self.assertEqual(g["tool_id"], "aeg1")

    def test_evaluate_met_and_working(self):
        db = _db(20.0)                       # best group = 20 mm
        db.settings["goals"] = [goals.new_goal("group_size", 30.0)]  # <= 30 mm
        r = goals.summary(db)[0]
        self.assertTrue(r["met"])
        self.assertAlmostEqual(r["best"], 20.0)

        db2 = _db(50.0)                      # best group = 50 mm, target 30 -> not met
        db2.settings["goals"] = [goals.new_goal("group_size", 30.0)]
        self.assertFalse(goals.summary(db2)[0]["met"])

    def test_evaluate_no_data(self):
        db = Database()
        db.add_tool(Tool("aeg1", "Training AEG", category="AEG"))
        db.settings["goals"] = [goals.new_goal("score", 80.0, tool_id="aeg1")]
        self.assertIsNone(goals.summary(db)[0]["met"])   # no analysed sessions


if __name__ == "__main__":
    unittest.main()
