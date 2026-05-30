import os
import tempfile
import unittest

from marksman import imageio


class TestImageIO(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_png_round_trip(self):
        img = imageio.Image(4, 3, bytearray(4 * 3 * 3))
        # Set a few distinctive pixels.
        img.set(0, 0, 255, 0, 0)
        img.set(3, 0, 0, 255, 0)
        img.set(1, 2, 10, 20, 30)
        path = os.path.join(self.dir, "x.png")
        imageio.save_png(path, img)

        back = imageio.load(path)
        self.assertEqual((back.width, back.height), (4, 3))
        self.assertEqual(back.get(0, 0), (255, 0, 0))
        self.assertEqual(back.get(3, 0), (0, 255, 0))
        self.assertEqual(back.get(1, 2), (10, 20, 30))
        self.assertEqual(back.get(2, 1), (0, 0, 0))

    def test_encode_decode_in_memory(self):
        img = imageio.Image(2, 2, bytearray([
            1, 2, 3, 4, 5, 6,
            7, 8, 9, 10, 11, 12,
        ]))
        blob = imageio.encode_png(img)
        back = imageio.decode_png(blob)
        self.assertEqual(back.rgb, img.rgb)

    def test_bad_signature_raises(self):
        with self.assertRaises(ValueError):
            imageio.decode_png(b"not a png at all")


if __name__ == "__main__":
    unittest.main()
