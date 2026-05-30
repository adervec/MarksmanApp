import os
import tempfile
import unittest

from marksman.models import Shot, Weapon, Session
from marksman.grouping import analyze_group
from marksman.targets import uniform_target
from marksman.storage import Database


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "db.json")

    def test_round_trip(self):
        db = Database(path=self.path)
        db.add_weapon(Weapon("w1", "Pistol A", category="air pistol",
                             caliber_mm=4.5, is_airgun=True))
        tgt = uniform_target("T", ten_ring_diameter_mm=10.0, ring_step_mm=5.0)
        shots = [Shot(0, 0), Shot(3, 4)]
        st = analyze_group(shots, target=tgt)
        db.add_session(Session("s1", "w1", "2026-05-01", shots=shots, stats=st,
                               distance_m=10.0, target_name="T"))
        db.save()

        self.assertTrue(os.path.exists(self.path))

        db2 = Database.load(self.path)
        self.assertEqual(len(db2.weapons), 1)
        self.assertEqual(len(db2.sessions), 1)
        w = db2.get_weapon("w1")
        self.assertEqual(w.name, "Pistol A")
        self.assertEqual(w.category, "Air Pistol")   # normalised on load
        self.assertEqual(w.caliber_mm, 4.5)
        s = db2.sessions["s1"]
        self.assertEqual(s.stats.shot_count, 2)
        self.assertAlmostEqual(s.stats.extreme_spread_mm, 5.0)
        self.assertEqual(s.shots[1].x_mm, 3)

    def test_load_missing_returns_empty(self):
        db = Database.load(os.path.join(self.dir, "nope.json"))
        self.assertEqual(len(db.weapons), 0)

    def test_add_session_unknown_weapon_raises(self):
        db = Database(path=self.path)
        with self.assertRaises(ValueError):
            db.add_session(Session("s1", "ghost", "2026-05-01"))

    def test_duplicate_weapon_raises(self):
        db = Database(path=self.path)
        db.add_weapon(Weapon("w1", "A"))
        with self.assertRaises(ValueError):
            db.add_weapon(Weapon("w1", "B"))

    def test_find_weapon_by_name(self):
        db = Database(path=self.path)
        db.add_weapon(Weapon("w1", "Walther LP500"))
        self.assertIsNotNone(db.find_weapon("w1"))
        self.assertIsNotNone(db.find_weapon("walther lp500"))
        self.assertIsNotNone(db.find_weapon("walther"))   # unique substring

    def test_sessions_for_category(self):
        db = Database(path=self.path)
        db.add_weapon(Weapon("w1", "A", category="Air Pistol"))
        db.add_weapon(Weapon("w2", "B", category="Air Rifle"))
        db.add_session(Session("s1", "w1", "2026-05-01"))
        db.add_session(Session("s2", "w2", "2026-05-02"))
        self.assertEqual(len(db.sessions_for_category("Air Pistol")), 1)


if __name__ == "__main__":
    unittest.main()
