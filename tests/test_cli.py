import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from marksman.cli import main, parse_shots
from marksman.models import Session
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

    def test_print_falls_through_to_the_sheet_catalogue(self):
        out_file = os.path.join(self.dir, "sheet.html")
        code, out = self.run_cli("targets", "--print", "dots-15",
                                 "-o", out_file, "--no-open")
        self.assertEqual(code, 0)
        with open(out_file, encoding="utf-8") as fh:
            self.assertIn("width='210.000mm'", fh.read())
        code, out = self.run_cli("targets", "--print", "nonsense-99",
                                 "--no-open")
        self.assertEqual(code, 2)
        self.assertIn("Families", out)
        # The bare listing now advertises the sheet families too.
        code, out = self.run_cli("targets")
        self.assertEqual(code, 0)
        self.assertIn("drill sheets", out)
        self.assertIn("bulls-40", out)

    def test_parse_shots(self):
        shots = parse_shots("0,0 1.5,-2; 3,4")
        self.assertEqual(len(shots), 3)
        self.assertEqual(shots[1].x_mm, 1.5)
        self.assertEqual(shots[1].y_mm, -2.0)

    def test_full_flow(self):
        code, _ = self.run_cli("tool", "add", "--id", "ap1", "--name",
                               "Test AEG", "--category", "AEG",
                               "--projectile-mm", "6.0")
        self.assertEqual(code, 0)

        code, out = self.run_cli(
            "analyze", "--tool", "ap1", "--target", "Airsoft Practice 10m",
            "--distance", "10", "--date", "2026-01-01",
            "--shots", "0,0 2,0 0,2 -1,-1",
        )
        self.assertEqual(code, 0)
        self.assertIn("Group size", out)
        self.assertIn("Saved session", out)

        # Second, tighter session later -> improvement.
        code, _ = self.run_cli(
            "analyze", "--tool", "ap1", "--target", "Airsoft Practice 10m",
            "--distance", "10", "--date", "2026-02-01",
            "--shots", "0,0 1,0 0,1 -0.5,-0.5",
        )
        self.assertEqual(code, 0)

        # Persisted?
        db = Database.load(self.db)
        self.assertEqual(len(db.sessions_for_tool("ap1")), 2)

        code, out = self.run_cli("progress", "--tool", "ap1")
        self.assertEqual(code, 0)
        self.assertIn("Group size", out)

        code, out = self.run_cli("progress", "--by-category")
        self.assertEqual(code, 0)
        self.assertIn("AEG", out)

        code, out = self.run_cli("sessions")
        self.assertEqual(code, 0)
        self.assertIn("2026-01-01", out)

    def test_analyze_unknown_tool(self):
        code, _ = self.run_cli("analyze", "--tool", "ghost", "--shots", "0,0")
        self.assertEqual(code, 2)

    def test_no_save(self):
        self.run_cli("tool", "add", "--id", "w", "--name", "W")
        code, out = self.run_cli("analyze", "--tool", "w",
                                 "--shots", "0,0 5,5", "--no-save")
        self.assertEqual(code, 0)
        db = Database.load(self.db)
        self.assertEqual(len(db.all_sessions()), 0)


class TestToolEditAndImport(TestCliFlow):
    """You must be able to fix a tool, and to get your data back."""

    def _seed(self):
        self.run_cli("tool", "add", "--id", "ap1", "--name", "AEG",
                     "--category", "AEG", "--projectile-mm", "6.0")
        self.run_cli("analyze", "--tool", "ap1", "--distance", "10",
                     "--date", "2026-01-01", "--shots", "0,0 2,0 0,2")

    def test_tool_set_can_add_a_click_value_later(self):
        # A tool with sessions can't be deleted and re-added, so it must be
        # editable in place.
        self._seed()
        code, _ = self.run_cli("tool", "set", "--id", "ap1", "--click", "1/4moa")
        self.assertEqual(code, 0)
        tool = Database.load(self.db).get_tool("ap1")
        self.assertAlmostEqual(tool.sight_click_mrad, 0.25 / 3.43774677)
        # ...and the analysis starts giving clicks.
        _, out = self.run_cli("analyze", "--tool", "ap1", "--distance", "10",
                              "--shots", "12,8 14,10 13,6", "--no-save")
        self.assertIn("clicks left", out)
        # Cleared again.
        self.run_cli("tool", "set", "--id", "ap1", "--click", "")
        self.assertIsNone(Database.load(self.db).get_tool("ap1").sight_click_mrad)

    def test_tool_set_leaves_untouched_fields_alone(self):
        self._seed()
        self.run_cli("tool", "set", "--id", "ap1", "--name", "Renamed")
        tool = Database.load(self.db).get_tool("ap1")
        self.assertEqual(tool.name, "Renamed")
        self.assertEqual(tool.category, "AEG")          # not retitled
        self.assertEqual(tool.projectile_mm, 6.0)

    def test_tool_set_rejects_nonsense(self):
        self._seed()
        self.assertEqual(self.run_cli("tool", "set", "--id", "ghost",
                                      "--name", "x")[0], 2)
        self.assertEqual(self.run_cli("tool", "set", "--id", "ap1",
                                      "--click", "bananas")[0], 2)

    def test_import_merges_and_repeats_harmlessly(self):
        self._seed()
        other = os.path.join(self.dir, "other.json")
        os.replace(self.db, other)

        code, out = self.run_cli("import", other, "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("1 new", out)
        self.assertFalse(os.path.exists(self.db))       # dry run wrote nothing

        self.assertEqual(self.run_cli("import", other)[0], 0)
        db = Database.load(self.db)
        self.assertEqual(len(db.tools), 1)
        self.assertEqual(len(db.all_sessions()), 1)

        _, again = self.run_cli("import", other)
        self.assertIn("already has it all", again)
        self.assertEqual(len(Database.load(self.db).all_sessions()), 1)

    def test_import_refuses_a_report_and_junk(self):
        self._seed()
        report = os.path.join(self.dir, "report.json")
        self.run_cli("export", "--format", "json", "--out", report)
        code, out = self.run_cli("import", report)
        self.assertEqual(code, 2)
        self.assertIn("isn't a Marksman database", out)

        junk = os.path.join(self.dir, "junk.json")
        with io.open(junk, "w", encoding="utf-8") as fh:
            fh.write("not json at all")
        self.assertEqual(self.run_cli("import", junk)[0], 2)
        self.assertEqual(self.run_cli("import",
                                      os.path.join(self.dir, "gone.json"))[0], 2)

    def test_import_skips_sessions_whose_tool_is_missing(self):
        # A session that names a tool neither file has can't be scoped to a
        # category or a tool, so it is reported and left out.
        self._seed()
        other = os.path.join(self.dir, "other.json")
        db = Database.load(self.db)
        stray = list(db.sessions.values())[0]
        db.sessions = {"stray1": Session.from_dict(
            dict(stray.to_dict(), id="stray1", tool_id="ghost"))}
        db.tools.clear()
        db.save(other)
        code, out = self.run_cli("import", other)
        self.assertEqual(code, 0)
        self.assertIn("skipped", out)
        self.assertNotIn("stray1", Database.load(self.db).sessions)


if __name__ == "__main__":
    unittest.main()
