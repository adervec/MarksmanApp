import unittest

from marksman import targets as T
from marksman.grouping import score_shot
from marksman.models import Shot


class TestTargets(unittest.TestCase):
    def test_builtins_present(self):
        names = T.list_targets()
        self.assertIn("ISSF 10m Air Pistol", names)
        self.assertIn("ISSF 10m Air Rifle", names)
        self.assertIn("NRA B-8", names)

    def test_air_pistol_dimensions(self):
        ap = T.get_target("issf 10m air pistol")  # case-insensitive
        self.assertEqual(ap.max_value, 10)
        self.assertAlmostEqual(ap.ten_ring_radius_mm, 11.5 / 2)
        self.assertAlmostEqual(ap.outer_radius_mm, 155.5 / 2)
        # ring step radial = 8mm (diameters 16mm apart)
        self.assertAlmostEqual(ap.ring_step_mm, 8.0)
        self.assertTrue(ap.decimal_scoring)

    def test_air_pistol_scoring(self):
        ap = T.get_target("ISSF 10m Air Pistol")
        self.assertAlmostEqual(score_shot(Shot(0, 0), ap), 10.9)        # dead centre
        # On the 10-ring line -> exactly 10.0
        self.assertAlmostEqual(score_shot(Shot(11.5 / 2, 0), ap), 10.0)

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
