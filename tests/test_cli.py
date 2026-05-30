import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from marksman.cli import main, parse_shots
from marksman.storage import Database


class TestCliFlow(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "db.json")

    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--db", self.db] + list(args))
        return code, buf.getvalue()

    def test_parse_shots(self):
        shots = parse_shots("0,0 1.5,-2; 3,4")
        self.assertEqual(len(shots), 3)
        self.assertEqual(shots[1].x_mm, 1.5)
        self.assertEqual(shots[1].y_mm, -2.0)

    def test_full_flow(self):
        code, _ = self.run_cli("weapon", "add", "--id", "ap1", "--name",
                               "Test AP", "--category", "Air Pistol",
                               "--caliber-mm", "4.5", "--airgun")
        self.assertEqual(code, 0)

        code, out = self.run_cli(
            "analyze", "--weapon", "ap1", "--target", "ISSF 10m Air Pistol",
            "--distance", "10", "--date", "2026-01-01",
            "--shots", "0,0 2,0 0,2 -1,-1",
        )
        self.assertEqual(code, 0)
        self.assertIn("Group size", out)
        self.assertIn("Saved session", out)

        # Second, tighter session later -> improvement.
        code, _ = self.run_cli(
            "analyze", "--weapon", "ap1", "--target", "ISSF 10m Air Pistol",
            "--distance", "10", "--date", "2026-02-01",
            "--shots", "0,0 1,0 0,1 -0.5,-0.5",
        )
        self.assertEqual(code, 0)

        # Persisted?
        db = Database.load(self.db)
        self.assertEqual(len(db.sessions_for_weapon("ap1")), 2)

        code, out = self.run_cli("progress", "--weapon", "ap1")
        self.assertEqual(code, 0)
        self.assertIn("Group size", out)

        code, out = self.run_cli("progress", "--by-category")
        self.assertEqual(code, 0)
        self.assertIn("Air Pistol", out)

        code, out = self.run_cli("sessions")
        self.assertEqual(code, 0)
        self.assertIn("2026-01-01", out)

    def test_analyze_unknown_weapon(self):
        code, _ = self.run_cli("analyze", "--weapon", "ghost", "--shots", "0,0")
        self.assertEqual(code, 2)

    def test_no_save(self):
        self.run_cli("weapon", "add", "--id", "w", "--name", "W")
        code, out = self.run_cli("analyze", "--weapon", "w",
                                 "--shots", "0,0 5,5", "--no-save")
        self.assertEqual(code, 0)
        db = Database.load(self.db)
        self.assertEqual(len(db.all_sessions()), 0)


if __name__ == "__main__":
    unittest.main()
