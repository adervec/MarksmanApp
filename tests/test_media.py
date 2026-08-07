import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from marksman import imageio, render
from marksman.cli import main
from marksman.grouping import analyze_group
from marksman.models import Session, Shot, Tool
from marksman.storage import Database
from marksman.targets import get_target


def _session(date="2026-04-15", pts=((8, 12), (15, 6), (4, 18), (12, 14)),
             target=None, **kw):
    shots = [Shot(x, y) for x, y in pts]
    st = analyze_group(shots, target=target, projectile_mm=4.5)
    return Session(id=date, tool_id="ap1", date=date, shots=shots, stats=st,
                   distance_m=10.0, target_name=(target.name if target else ""),
                   **kw)


def _colors(img):
    seen = set()
    for y in range(img.height):
        for x in range(img.width):
            seen.add(img.get(x, y))
    return seen


class TestRender(unittest.TestCase):
    def test_renders_target_and_shots(self):
        tgt = get_target("Airsoft Practice 10m")
        img = render.render_session(_session(target=tgt), target=tgt,
                                    size_px=240)
        self.assertEqual((img.width, img.height), (240, 240))
        cols = _colors(img)
        self.assertIn(render._BLACK, cols)          # the aiming bull
        self.assertIn(render._SHOT, cols)           # plotted shots
        self.assertIn(render._BG, cols)             # background survives

    def test_renders_without_target(self):
        img = render.render_session(_session(target=None), target=None,
                                    size_px=200)
        self.assertEqual((img.width, img.height), (200, 200))
        # No rings/bull, but the shots are still drawn.
        self.assertIn(render._SHOT, _colors(img))

    def test_renders_with_no_shots(self):
        tgt = get_target("Airsoft CQB 7m")
        s = Session(id="empty", tool_id="ap1", date="2026-01-01", shots=[],
                    target_name=tgt.name)
        img = render.render_session(s, target=tgt, size_px=160)
        self.assertEqual((img.width, img.height), (160, 160))

    def test_save_recreation_writes_valid_png(self):
        d = tempfile.mkdtemp()
        tgt = get_target("Airsoft Practice 10m")
        out = os.path.join(d, "sub", "rec.png")          # nested dir is created
        path = render.save_recreation(_session(target=tgt), out, target=tgt,
                                      size_px=200)
        self.assertTrue(os.path.isfile(path))
        with open(path, "rb") as fh:
            decoded = imageio.decode_png(fh.read())
        self.assertEqual((decoded.width, decoded.height), (200, 200))
        # A synthetic flat-colour diagram is tiny compared to a real photo.
        self.assertLess(os.path.getsize(path), 200_000)


class TestStorageLocations(unittest.TestCase):
    def test_recreations_dir_beside_db(self):
        db = Database(path="/tmp/sub/marks.json")
        self.assertEqual(os.path.basename(db.recreations_dir), "recreations")
        self.assertEqual(os.path.dirname(db.recreations_dir),
                         os.path.dirname(os.path.abspath("/tmp/sub/marks.json")))

    def test_media_fields_round_trip(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "db.json")
        db = Database(path=path)
        db.add_tool(Tool("ap1", "AP"))
        db.add_session(_session(image_path="/x/a.png", video_path="/x/a.mp4",
                                recreation_path="/x/r.png", media_cleaned=True))
        db.save()
        s = Database.load(path).sessions["2026-04-15"]
        self.assertEqual(s.image_path, "/x/a.png")
        self.assertEqual(s.video_path, "/x/a.mp4")
        self.assertEqual(s.recreation_path, "/x/r.png")
        self.assertTrue(s.media_cleaned)


class TestCleanupCli(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.dir, "db.json")
        db = Database(path=self.db_path)
        db.add_tool(Tool(id="ap1", name="Training AEG",
                             category="AEG", projectile_mm=6.0))
        self.tgt = get_target("Airsoft Practice 10m")
        self.img = os.path.join(self.dir, "s.png")
        self.vid = os.path.join(self.dir, "s.mp4")
        with open(self.img, "wb") as fh:
            fh.write(b"\x00" * 50_000)
        with open(self.vid, "wb") as fh:
            fh.write(b"\x00" * 120_000)
        db.add_session(_session(target=self.tgt, image_path=self.img,
                                video_path=self.vid))
        db.save()

    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--db", self.db_path] + list(args))
        return code, buf.getvalue()

    def test_render_writes_recreation_without_mutating_db(self):
        code, out = self.run_cli("render", "--session", "2026-04-15")
        self.assertEqual(code, 0)
        rec = os.path.join(Database.load(self.db_path).recreations_dir,
                           "2026-04-15.png")
        self.assertTrue(os.path.isfile(rec))
        # render is an export: it must not touch the stored record.
        s = Database.load(self.db_path).sessions["2026-04-15"]
        self.assertEqual(s.recreation_path, "")
        self.assertEqual(s.image_path, self.img)

    def test_dry_run_is_default_and_deletes_nothing(self):
        code, out = self.run_cli("cleanup")
        self.assertEqual(code, 0)
        self.assertIn("dry run", out.lower())
        self.assertTrue(os.path.isfile(self.img))     # untouched
        self.assertTrue(os.path.isfile(self.vid))
        s = Database.load(self.db_path).sessions["2026-04-15"]
        self.assertFalse(s.media_cleaned)
        self.assertEqual(s.image_path, self.img)

    def test_apply_frees_space_keeps_recreation_and_data(self):
        code, out = self.run_cli("cleanup", "--apply")
        self.assertEqual(code, 0)
        # Source media gone...
        self.assertFalse(os.path.exists(self.img))
        self.assertFalse(os.path.exists(self.vid))
        db = Database.load(self.db_path)
        s = db.sessions["2026-04-15"]
        self.assertTrue(s.media_cleaned)
        self.assertEqual(s.image_path, "")
        self.assertEqual(s.video_path, "")
        # ...but the result is preserved: a recreation file + the shot data.
        self.assertTrue(s.recreation_path and os.path.isfile(s.recreation_path))
        self.assertEqual(len(s.shots), 4)

    def test_apply_no_recreate_still_keeps_recreatable_data(self):
        code, _ = self.run_cli("cleanup", "--apply", "--no-recreate")
        self.assertEqual(code, 0)
        db = Database.load(self.db_path)
        s = db.sessions["2026-04-15"]
        self.assertFalse(os.path.exists(self.img))
        self.assertEqual(s.recreation_path, "")        # none saved
        # The guarantee: a recreation can still be produced on demand.
        code, _ = self.run_cli("render", "--session", "2026-04-15")
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(os.path.join(db.recreations_dir,
                                                    "2026-04-15.png")))

    def test_nothing_to_clean(self):
        self.run_cli("cleanup", "--apply")             # remove everything first
        code, out = self.run_cli("cleanup")
        self.assertEqual(code, 0)
        self.assertIn("Nothing to clean up", out)

    def test_filters_scope_selection(self):
        # A second tool/session whose media should NOT be touched by a filter.
        db = Database.load(self.db_path)
        db.add_tool(Tool(id="r1", name="Recon GBB", category="GBB Rifle",
                             projectile_mm=6.0))
        other = os.path.join(self.dir, "other.png")
        with open(other, "wb") as fh:
            fh.write(b"\x00" * 10_000)
        db.add_session(_session(date="2026-01-01", target=self.tgt,
                                image_path=other))
        db.sessions["2026-01-01"].tool_id = "r1"
        db.save()

        code, out = self.run_cli("cleanup", "--apply", "--tool", "ap1")
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(self.img))     # ap1's media gone
        self.assertTrue(os.path.exists(other))         # r1's media untouched


if __name__ == "__main__":
    unittest.main()


class TestPrintableFace(unittest.TestCase):
    """A face is only useful if it prints at the size the app scores against."""

    def setUp(self):
        self.spec = get_target("Practice Face")

    def test_page_is_declared_in_millimetres(self):
        page = render.target_html(self.spec, distance_m=10.0, paper="a4")
        # True scale depends entirely on mm units reaching the print engine.
        self.assertIn("width='210.000mm'", page)
        self.assertIn("viewBox='0 0 210.000 297.000'", page)
        self.assertIn("@page{size:210.000mm 297.000mm;margin:0}", page)
        self.assertIn("100 mm", page)              # the calibration ruler

    def test_rings_come_out_at_their_real_radius(self):
        page = render.target_html(self.spec, paper="a3")
        for ring in self.spec.rings:
            self.assertIn("r='%.3f'" % ring.radius_mm, page)

    def test_big_faces_are_tiled_not_shrunk(self):
        small = render.target_html(self.spec, paper="a3").count("class='sheet'")
        big = render.target_html(self.spec, paper="a5").count("class='sheet'")
        self.assertGreater(big, small)
        self.assertIn("sheet 1 of", render.target_html(self.spec, paper="a5"))
        # Whatever the paper, a millimetre stays a millimetre.
        self.assertIn("r='250.000'", render.target_html(self.spec, paper="a5"))

    def test_paper_sizes(self):
        self.assertEqual(render.paper_size("A4"), (210.0, 297.0))
        self.assertEqual(render.paper_size("200x250"), (200.0, 250.0))
        self.assertEqual(render.paper_size(""), (210.0, 297.0))   # empty = a4
        for bad in ("bogus", "10x10", "5000x5000", "200x"):
            with self.assertRaises(KeyError, msg=bad):
                render.paper_size(bad)

    def test_a_pack_cannot_inject_markup_through_a_face_name(self):
        spec = get_target("Practice Face")
        spec = type(spec)(name="<script>alert(1)</script>", rings=spec.rings)
        page = render.target_html(spec)
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)

    def test_refuses_to_emit_a_thousand_sheets(self):
        spec = get_target("Practice Face")
        huge = type(spec)(name="Huge", rings=spec.rings, face_width_mm=20000.0)
        with self.assertRaises(ValueError):
            render.target_html(huge, paper="a5")


if __name__ == "__main__":
    unittest.main()
