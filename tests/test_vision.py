import os
import tempfile
import unittest

from marksman import imageio
from marksman import vision


def new_white(size):
    return imageio.Image(size, size, bytearray([255] * (size * size * 3)))


def draw_disk(img, cx, cy, r, color):
    rr = r * r
    for y in range(cy - r, cy + r + 1):
        if y < 0 or y >= img.height:
            continue
        for x in range(cx - r, cx + r + 1):
            if x < 0 or x >= img.width:
                continue
            if (x - cx) ** 2 + (y - cy) ** 2 <= rr:
                img.set(x, y, *color)


class TestVision(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _save(self, img, name="t.png"):
        path = os.path.join(self.dir, name)
        imageio.save_png(path, img)
        return path

    def test_rgb_to_hsv_basic(self):
        self.assertAlmostEqual(vision._rgb_to_hsv(255, 0, 0)[0], 0.0, delta=1)
        self.assertAlmostEqual(vision._rgb_to_hsv(0, 255, 0)[0], 120.0, delta=1)
        self.assertAlmostEqual(vision._rgb_to_hsv(0, 0, 255)[0], 240.0, delta=1)

    def test_detect_red_markers_to_mm(self):
        img = new_white(200)
        for (x, y) in [(100, 100), (140, 100), (100, 60)]:
            draw_disk(img, x, y, 6, (220, 20, 20))
        path = self._save(img)
        res = vision.analyze_image(
            path, mode="marker", color="red",
            center_px=(100, 100), mm_per_px=0.5,
        )
        self.assertEqual(len(res.shots), 3)
        coords = sorted((round(s.x_mm), round(s.y_mm)) for s in res.shots)
        # (100,100)->(0,0); (140,100)->(20,0); (100,60)->(0,+20) [y up]
        self.assertEqual(coords, [(0, 0), (0, 20), (20, 0)])

    def test_color_mask_counts_two_blobs(self):
        img = new_white(200)
        draw_disk(img, 50, 50, 6, (220, 20, 20))
        draw_disk(img, 150, 150, 6, (220, 20, 20))
        mask = vision.color_mask(img, "red")
        blobs = vision.find_blobs(mask, img.width, img.height, min_size=10)
        self.assertEqual(len(blobs), 2)

    def test_auto_center_from_dark_bull(self):
        img = new_white(200)
        draw_disk(img, 100, 100, 25, (10, 10, 10))   # bull
        draw_disk(img, 130, 100, 6, (220, 20, 20))   # a red shot
        cx, cy = vision.detect_bull_center(img)
        self.assertAlmostEqual(cx, 100, delta=3)
        self.assertAlmostEqual(cy, 100, delta=3)

    def test_holes_mode(self):
        img = new_white(200)
        draw_disk(img, 90, 100, 6, (15, 15, 15))
        draw_disk(img, 110, 100, 6, (15, 15, 15))
        path = self._save(img)
        res = vision.analyze_image(
            path, mode="holes", center_px=(100, 100), mm_per_px=1.0,
            dark_threshold=80,
        )
        self.assertEqual(len(res.shots), 2)
        coords = sorted((round(s.x_mm), round(s.y_mm)) for s in res.shots)
        self.assertEqual(coords, [(-10, 0), (10, 0)])

    def test_scale_helpers(self):
        self.assertAlmostEqual(vision.mm_per_px_from_face(400, 170.0), 0.425)
        self.assertAlmostEqual(
            vision.mm_per_px_from_reference((0, 0), (100, 0), 50.0), 0.5)

    def test_face_width_calibration(self):
        img = new_white(200)
        draw_disk(img, 100, 100, 6, (220, 20, 20))
        path = self._save(img)
        # 200 px wide spans a 100 mm face -> 0.5 mm/px.
        res = vision.analyze_image(
            path, mode="marker", color="red",
            center_px=(100, 100), face_width_mm=100.0,
        )
        self.assertAlmostEqual(res.mm_per_px, 0.5)

    def test_no_scale_raises(self):
        img = new_white(60)
        draw_disk(img, 30, 30, 5, (220, 20, 20))
        path = self._save(img)
        with self.assertRaises(ValueError):
            vision.analyze_image(path, center_px=(30, 30))


if __name__ == "__main__":
    unittest.main()
