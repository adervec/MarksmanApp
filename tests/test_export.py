import json
import unittest

from marksman.models import Shot, Tool, Session
from marksman.grouping import analyze_group
from marksman.storage import Database
from marksman import exporter, logo, imageio


def _db():
    db = Database()
    db.add_tool(Tool("aeg1", "Training AEG", category="AEG", bb_mm=6.0))
    shots = [Shot(-20.0, 0.0), Shot(20.0, 0.0)]
    db.add_session(Session("s0", "aeg1", "2026-01-01", shots=shots,
                           stats=analyze_group(shots), distance_m=10.0))
    return db


class TestExport(unittest.TestCase):
    def test_csv_has_header_and_row(self):
        text = exporter.to_csv(_db())
        lines = text.strip().splitlines()
        self.assertEqual(lines[0].split(",")[0], "session_id")
        self.assertEqual(len(lines), 2)                 # header + one session
        self.assertIn("aeg1", lines[1])

    def test_json_roundtrips(self):
        rows = json.loads(exporter.to_json(_db()))
        self.assertEqual(rows[0]["tool_name"], "Training AEG")
        self.assertAlmostEqual(rows[0]["extreme_spread_mm"], 40.0)
        self.assertAlmostEqual(rows[0]["group_mrad"], 4.0)   # 40mm @ 10m


class TestLogo(unittest.TestCase):
    def test_png_and_ico_signatures(self):
        img = logo.make_logo(96)
        png = imageio.encode_png(img)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(logo._ico_from_png(png)[:4], b"\x00\x00\x01\x00")


if __name__ == "__main__":
    unittest.main()
