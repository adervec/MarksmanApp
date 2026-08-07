"""The parametric drill-sheet catalogue: every family generates true-scale
SVG, sizes actually change the geometry, and the name parser is a real gate
(designs arrive from the web query string)."""

import unittest

from marksman import sheets


class TestSheets(unittest.TestCase):

    def test_every_catalog_entry_prints(self):
        for name, blurb in sheets.catalog():
            page = sheets.make(name)
            self.assertIn("width='210.000mm'", page, name)      # a4, mm units
            self.assertIn("reprint at 100%", page, name)        # ruler
            self.assertTrue(blurb)

    def test_size_is_not_decorative(self):
        # Smaller dots pack more of them onto the same paper.
        small = sheets.make("dots-5").count("<circle")
        big = sheets.make("dots-40").count("<circle")
        self.assertGreater(small, big)
        # The stated millimetre size appears verbatim in the drawing.
        self.assertIn("r='20.000'", sheets.make("dots-40"))

    def test_face_family_is_a_real_scored_target(self):
        spec = sheets.scoring_face(60.0)
        self.assertEqual(len(spec.rings), 5)                    # small: 5 rings
        self.assertAlmostEqual(spec.outer_radius_mm, 30.0)
        self.assertEqual(len(sheets.scoring_face(200.0).rings), 10)
        # Big faces tile across sheets instead of shrinking.
        self.assertGreater(sheets.make("face-600").count("class='sheet'"), 1)

    def test_bare_family_name_takes_its_default(self):
        self.assertEqual(sheets.parse("dots"), ("dots", 15.0))
        self.assertEqual(sheets.parse("Bulls-25"), ("bulls", 25.0))

    def test_nonsense_is_refused(self):
        for bad in ("silhouette", "dots-1", "bulls-9999", "", "face-5000",
                    "dots-15-3", "<script>"):
            with self.assertRaises(KeyError, msg=bad):
                sheets.make(bad)

    def test_too_big_for_the_paper_says_so(self):
        sheets.make("clock-50", paper="a4")
        with self.assertRaises(KeyError):
            sheets.make("clock-50", paper="a5")
        sheets.make("bulls-150", paper="400x600")               # custom paper


if __name__ == "__main__":
    unittest.main()
