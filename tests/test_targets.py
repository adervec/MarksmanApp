import unittest

from marksman import targets as T
from marksman.grouping import score_shot
from marksman.models import Shot


class TestTargets(unittest.TestCase):
    def test_builtins_present(self):
        names = T.list_targets()
        self.assertIn("Airsoft Practice 10m", names)
        self.assertIn("Airsoft CQB 7m", names)
        self.assertIn("Airsoft Precision 20m", names)

    def test_practice_dimensions(self):
        t = T.get_target("airsoft practice 10m")  # case-insensitive
        self.assertEqual(t.max_value, 10)
        self.assertAlmostEqual(t.ten_ring_radius_mm, 20.0)          # 40 mm dia
        self.assertAlmostEqual(t.ring_step_mm, 20.0)
        self.assertAlmostEqual(t.outer_radius_mm, 20.0 + 9 * 20.0)  # 1-ring radius
        self.assertFalse(t.decimal_scoring)

    def test_practice_scoring(self):
        t = T.get_target("Airsoft Practice 10m")
        self.assertAlmostEqual(score_shot(Shot(0, 0), t), 10.0)   # dead centre
        self.assertAlmostEqual(score_shot(Shot(20, 0), t), 10.0)  # on the 10-ring line
        self.assertAlmostEqual(score_shot(Shot(21, 0), t), 9.0)   # just outside

    def test_uniform_builder(self):
        t = T.uniform_target("X", ten_ring_diameter_mm=20.0, ring_step_mm=10.0)
        self.assertAlmostEqual(t.ten_ring_radius_mm, 10.0)
        self.assertAlmostEqual(t.ring_step_mm, 10.0)
        self.assertEqual(t.max_value, 10)

    def test_unknown_raises(self):
        with self.assertRaises(KeyError):
            T.get_target("does-not-exist")

    def test_register_custom(self):
        t = T.uniform_target("My Custom", ten_ring_diameter_mm=12.0, ring_step_mm=6.0)
        T.register(t)
        self.assertIs(T.get_target("my custom"), t)


if __name__ == "__main__":
    unittest.main()
