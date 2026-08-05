import unittest

from marksman.models import Shot
from marksman.grouping import (
    analyze_group, score_shot, score_shots, centroid, extreme_spread,
)
from marksman.targets import uniform_target


class TestGeometry(unittest.TestCase):
    def test_extreme_spread_and_centroid(self):
        shots = [Shot(0, 0), Shot(3, 4)]
        self.assertAlmostEqual(extreme_spread(shots), 5.0)
        cx, cy = centroid(shots)
        self.assertAlmostEqual(cx, 1.5)
        self.assertAlmostEqual(cy, 2.0)

    def test_full_stats(self):
        shots = [Shot(0, 0), Shot(3, 4)]
        st = analyze_group(shots)
        self.assertEqual(st.shot_count, 2)
        self.assertAlmostEqual(st.extreme_spread_mm, 5.0)
        self.assertAlmostEqual(st.mean_radius_mm, 2.5)
        self.assertAlmostEqual(st.rms_radius_mm, 2.5)
        self.assertAlmostEqual(st.std_x_mm, 1.5)
        self.assertAlmostEqual(st.std_y_mm, 2.0)
        self.assertAlmostEqual(st.center_x_mm, 1.5)
        self.assertAlmostEqual(st.center_y_mm, 2.0)
        self.assertAlmostEqual(st.poa_offset_mm, 2.5)
        self.assertAlmostEqual(st.bounding_width_mm, 3.0)
        self.assertAlmostEqual(st.bounding_height_mm, 4.0)

    def test_single_shot(self):
        st = analyze_group([Shot(3, 4)])
        self.assertEqual(st.shot_count, 1)
        self.assertEqual(st.extreme_spread_mm, 0.0)
        self.assertEqual(st.mean_radius_mm, 0.0)
        self.assertAlmostEqual(st.poa_offset_mm, 5.0)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            analyze_group([])

    def test_poa_angle(self):
        st = analyze_group([Shot(0, 5)])   # straight up
        self.assertAlmostEqual(st.poa_offset_angle_deg, 90.0)
        st = analyze_group([Shot(5, 0)])   # right
        self.assertAlmostEqual(st.poa_offset_angle_deg, 0.0)


class TestScoring(unittest.TestCase):
    def setUp(self):
        # 10-ring diameter 10mm (r=5), each ring +5mm radius.
        self.tgt = uniform_target("UnitTest", ten_ring_diameter_mm=10.0, ring_step_mm=5.0)

    def test_integer_scoring(self):
        self.assertEqual(score_shot(Shot(0, 0), self.tgt), 10.0)
        self.assertEqual(score_shot(Shot(5, 0), self.tgt), 10.0)    # on the line
        self.assertEqual(score_shot(Shot(5.1, 0), self.tgt), 9.0)
        self.assertEqual(score_shot(Shot(50, 0), self.tgt), 1.0)
        self.assertEqual(score_shot(Shot(50.1, 0), self.tgt), 0.0)  # miss

    def test_bb_edge_scoring(self):
        # Shot centre at r=6, but a 4mm projectile's edge reaches r=4 -> 10.
        self.assertEqual(score_shot(Shot(6, 0), self.tgt, projectile_mm=4.0), 10.0)
        # Without bb it's a 9.
        self.assertEqual(score_shot(Shot(6, 0), self.tgt), 9.0)

    def test_score_shots_total_and_annotate(self):
        shots = [Shot(0, 0), Shot(6, 0)]  # 10 + 9
        total = score_shots(shots, self.tgt)
        self.assertEqual(total, 19.0)
        self.assertEqual(shots[0].score, 10.0)
        self.assertEqual(shots[1].score, 9.0)

    def test_decimal_scoring(self):
        d = uniform_target("Dec", ten_ring_diameter_mm=10.0, ring_step_mm=5.0,
                            decimal_scoring=True)
        self.assertAlmostEqual(score_shot(Shot(0, 0), d), 10.9)
        self.assertAlmostEqual(score_shot(Shot(5, 0), d), 10.0)   # on ten line
        self.assertAlmostEqual(score_shot(Shot(2.5, 0), d), 10.4)
        self.assertAlmostEqual(score_shot(Shot(10, 0), d), 9.0)   # on nine line
        self.assertAlmostEqual(score_shot(Shot(7.5, 0), d), 9.5)

    def test_analyze_with_target_scores(self):
        shots = [Shot(0, 0), Shot(6, 0)]
        st = analyze_group(shots, target=self.tgt)
        self.assertEqual(st.total_score, 19.0)
        self.assertEqual(st.max_possible_score, 20.0)
        self.assertEqual(st.average_score, 9.5)


if __name__ == "__main__":
    unittest.main()
