import os
import tempfile
import unittest

from marksman.models import Shot, Tool, Session
from marksman.grouping import analyze_group
from marksman.targets import uniform_target
from marksman.storage import Database


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "db.json")

    def test_round_trip(self):
        db = Database(path=self.path)
        db.add_tool(Tool("w1", "Rental AEG", category="aeg",
                             bb_mm=6.0, is_gas=False))
        tgt = uniform_target("T", ten_ring_diameter_mm=10.0, ring_step_mm=5.0)
        shots = [Shot(0, 0), Shot(3, 4)]
        st = analyze_group(shots, target=tgt)
        db.add_session(Session("s1", "w1", "2026-05-01", shots=shots, stats=st,
                               distance_m=10.0, target_name="T"))
        db.save()

        self.assertTrue(os.path.exists(self.path))

        db2 = Database.load(self.path)
        self.assertEqual(len(db2.tools), 1)
        self.assertEqual(len(db2.sessions), 1)
        w = db2.get_tool("w1")
        self.assertEqual(w.name, "Rental AEG")
        self.assertEqual(w.category, "AEG")   # normalised on load
        self.assertEqual(w.bb_mm, 6.0)
        s = db2.sessions["s1"]
        self.assertEqual(s.stats.shot_count, 2)
        self.assertAlmostEqual(s.stats.extreme_spread_mm, 5.0)
        self.assertEqual(s.shots[1].x_mm, 3)

    def test_load_missing_returns_empty(self):
        db = Database.load(os.path.join(self.dir, "nope.json"))
        self.assertEqual(len(db.tools), 0)

    def test_add_session_unknown_tool_raises(self):
        db = Database(path=self.path)
        with self.assertRaises(ValueError):
            db.add_session(Session("s1", "ghost", "2026-05-01"))

    def test_duplicate_tool_raises(self):
        db = Database(path=self.path)
        db.add_tool(Tool("w1", "A"))
        with self.assertRaises(ValueError):
            db.add_tool(Tool("w1", "B"))

    def test_find_tool_by_name(self):
        db = Database(path=self.path)
        db.add_tool(Tool("w1", "Training AEG"))
        self.assertIsNotNone(db.find_tool("w1"))
        self.assertIsNotNone(db.find_tool("training aeg"))
        self.assertIsNotNone(db.find_tool("training"))   # unique substring

    def test_sessions_for_category(self):
        db = Database(path=self.path)
        db.add_tool(Tool("w1", "A", category="AEG"))
        db.add_tool(Tool("w2", "B", category="GBB Rifle"))
        db.add_session(Session("s1", "w1", "2026-05-01"))
        db.add_session(Session("s2", "w2", "2026-05-02"))
        self.assertEqual(len(db.sessions_for_category("AEG")), 1)


if __name__ == "__main__":
    unittest.main()
