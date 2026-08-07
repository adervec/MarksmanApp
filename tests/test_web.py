"""Web app tests: a real server on an ephemeral port, driven with urllib."""

import base64
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from marksman import imageio, web
from marksman.grouping import analyze_group
from marksman.models import Session, Shot, Tool
from marksman.storage import Database

TOKEN = "testtok"

#: Where the marker dots sit, in mm from the point of aim.
MARKS = [(-40.0, 20.0), (30.0, -10.0), (5.0, 45.0)]


def _target_png(size=600, face_mm=400.0):
    """A photo-like target: pale paper, dark bull, red marker dots on the hits.

    This is what a phone photo pulled from Drive looks like to the vision
    layer, so the test covers the whole Drive-import path bar the OAuth.
    """
    img = imageio.Image(size, size, bytearray([240]) * (size * size * 3))
    mid = size / 2.0
    for y in range(size):                       # dark bull, for auto-centring
        for x in range(size):
            if (x - mid) ** 2 + (y - mid) ** 2 < 3600:
                img.set(x, y, 25, 25, 25)
    per_mm = size / face_mm
    for mx, my in MARKS:
        cx, cy = mid + mx * per_mm, mid - my * per_mm
        for y in range(int(cy) - 7, int(cy) + 8):
            for x in range(int(cx) - 7, int(cx) + 8):
                if 0 <= x < size and 0 <= y < size and (x - cx) ** 2 + (y - cy) ** 2 <= 49:
                    img.set(x, y, 220, 30, 30)
    return imageio.encode_png(img)


class TestWeb(unittest.TestCase):

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(self.path)
        db = Database(path=self.path)
        db.add_tool(Tool("aeg1", "Training AEG", category="AEG", projectile_mm=6.0))
        shots = [Shot(-15.0, 0.0), Shot(15.0, 0.0)]
        db.add_session(Session("s1", "aeg1", "2026-01-01", shots=shots,
                               stats=analyze_group(shots), distance_m=10.0,
                               drill_id="group-10"))
        db.save()
        self.httpd = web.make_server(self.path, "127.0.0.1", 0, token=TOKEN)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        if os.path.exists(self.path):
            os.remove(self.path)

    def _call(self, path, body=None, token=TOKEN):
        url = self.base + path
        if token:
            url += ("&" if "?" in url else "?") + "k=" + token
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data)
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def _json(self, path, body=None, token=TOKEN):
        status, raw = self._call(path, body, token)
        return status, json.loads(raw)

    # -- auth --------------------------------------------------------------- #
    def test_token_required_everywhere(self):
        for path in ("/", "/api/state"):
            status, _ = self._call(path, token=None)
            self.assertEqual(status, 403, path)
        status, _ = self._call("/api/session", body={}, token="wrong")
        self.assertEqual(status, 403)

    # -- reads -------------------------------------------------------------- #
    def test_page_serves(self):
        status, raw = self._call("/")
        self.assertEqual(status, 200)
        self.assertIn(b"MARKSMAN", raw)

    def test_page_has_the_orientation_setting(self):
        # Orientation is controlled by a saved setting, not the accelerometer.
        _, raw = self._call("/")
        for needle in (b'id="orient"', b"mk_orient", b"rotcw", b"rotccw"):
            self.assertIn(needle, raw)

    def test_state(self):
        status, s = self._json("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(s["tools"][0]["id"], "aeg1")
        self.assertGreaterEqual(len(s["targets"]), 3)
        self.assertEqual(len(s["plan"]), 3)
        group10 = next(d for d in s["drills"] if d["id"] == "group-10")
        self.assertEqual(group10["tier"], "Sharp")        # 30 mm PR from setUp
        # Content comes from packs, and the page is told about them.
        self.assertTrue(s["packs"])
        self.assertIn("airsoft", [p["id"] for p in s["packs"]])
        self.assertEqual(len(s["drills"]),
                         sum(p["drills"] for p in s["packs"] if p["active"]))
        # Two packs are active, so the UI keeps the app's neutral vocabulary.
        self.assertEqual(s["terms"]["projectile"], "projectile")

    def test_page_offers_drive_with_the_shared_oauth_client(self):
        _, raw = self._call("/")
        self.assertIn(b"547617739897-br6dj2facmsc34qnkjb5u4dbfhju39pu", raw)
        # Its own sync file: the app-data folder is shared per OAuth client.
        self.assertIn(b"marksman-sessions.json", raw)
        self.assertIn(b"drive.appdata", raw)

    # -- live stats --------------------------------------------------------- #
    def test_stats(self):
        status, s = self._json("/api/stats", body={
            "shots": [{"x_mm": -20, "y_mm": 0}, {"x_mm": 20, "y_mm": 0}],
            "target": "Airsoft Practice 10m", "distance_m": 10,
        })
        self.assertEqual(status, 200)
        self.assertAlmostEqual(s["group_mm"], 40.0)
        self.assertAlmostEqual(s["group_mrad"], 4.0)
        self.assertIsNotNone(s["score_pct"])

    # -- writes ------------------------------------------------------------- #
    def test_save_session_scores_the_drill(self):
        status, r = self._json("/api/session", body={
            "tool_id": "aeg1", "drill_id": "group-10",
            "target": "Airsoft Practice 10m", "distance_m": 10,
            "shots": [{"x_mm": -10, "y_mm": 0}, {"x_mm": 10, "y_mm": 0}],
        })
        self.assertEqual(status, 200)
        self.assertEqual(r["attemptTier"], "Marksman")    # 20 mm
        self.assertEqual(r["tier"], "Marksman")
        reloaded = Database.load(self.path)
        saved = [s for s in reloaded.all_sessions() if s.id != "s1"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].drill_id, "group-10")
        self.assertAlmostEqual(saved[0].stats.extreme_spread_mm, 20.0)

    def test_save_rejects_bad_input(self):
        cases = [
            {"tool_id": "aeg1", "shots": []},                       # no shots
            {"tool_id": "nope", "shots": [{"x_mm": 0, "y_mm": 0}]},  # bad tool
            {"tool_id": "aeg1", "shots": [{"x_mm": 1e9, "y_mm": 0}]},
            {"tool_id": "aeg1", "drill_id": "nope",
             "shots": [{"x_mm": 0, "y_mm": 0}]},
            {"tool_id": "aeg1", "date": "not-a-date",
             "shots": [{"x_mm": 0, "y_mm": 0}]},
        ]
        for body in cases:
            status, r = self._json("/api/session", body=body)
            self.assertEqual(status, 400, body)
            self.assertIn("error", r)
        self.assertEqual(len(Database.load(self.path).sessions), 1)

    def test_drive_photo_is_remembered_on_the_session(self):
        status, _ = self._json("/api/session", body={
            "tool_id": "aeg1", "target": "Airsoft Practice 10m", "distance_m": 10,
            "shots": [{"x_mm": -10, "y_mm": 0}, {"x_mm": 10, "y_mm": 0}],
            "source_ref": "drive:FILEID123",
        })
        self.assertEqual(status, 200)
        # The picker greys out photos already logged, from the sessions
        # themselves -- so it survives sync rather than living in a side list.
        _, s = self._json("/api/state")
        self.assertIn("FILEID123", s["imported"])
        status, _ = self._json("/api/session", body={
            "tool_id": "aeg1", "shots": [{"x_mm": 0, "y_mm": 0}],
            "source_ref": "ftp://elsewhere",
        })
        self.assertEqual(status, 400)

    def test_sync_merges_a_remote_bundle(self):
        remote = {
            "app": "marksman", "version": 1,
            "tools": [{"id": "other", "name": "Phone-logged", "category": "AEG"}],
            "sessions": [{"id": "remote1", "tool_id": "other", "date": "2026-02-02",
                          "shots": [{"x_mm": 0.0, "y_mm": 0.0}], "stats": None}],
            "settings": {},
        }
        status, r = self._json("/api/sync", body={"remote": remote})
        self.assertEqual(status, 200)
        self.assertEqual(r["addedSessions"], 1)
        self.assertEqual(r["addedTools"], 1)
        db = Database.load(self.path)
        self.assertIn("remote1", db.sessions)
        # Merging is a union by id, so replaying the same bundle changes nothing.
        _, again = self._json("/api/sync", body={"remote": remote})
        self.assertEqual(again["addedSessions"], 0)
        status, _ = self._json("/api/sync", body={"remote": {"app": "tachyread"}})
        self.assertEqual(status, 400)

    def test_analyze_image_rejects_junk(self):
        status, _ = self._json("/api/analyze-image",
                               body={"image_b64": "not base64!!"})
        self.assertEqual(status, 400)
        status, _ = self._json("/api/analyze-image", body={})
        self.assertEqual(status, 400)
        # No target face and no width means no way to convert pixels to mm.
        status, _ = self._json("/api/analyze-image", body={
            "image_b64": base64.b64encode(_target_png()).decode()})
        self.assertEqual(status, 400)

    def test_analyze_image_finds_the_hits_a_drive_photo_would_carry(self):
        status, r = self._json("/api/analyze-image", body={
            "image_b64": base64.b64encode(_target_png()).decode(),
            "target": "Airsoft Practice 10m", "mode": "marker", "color": "red",
        })
        self.assertEqual(status, 200)
        got = sorted((s["x_mm"], s["y_mm"]) for s in r["shots"])
        self.assertEqual(len(got), len(MARKS))
        for (ex, ey), (gx, gy) in zip(sorted(MARKS), got):
            self.assertAlmostEqual(gx, ex, delta=3.0)
            self.assertAlmostEqual(gy, ey, delta=3.0)

    def test_add_tool(self):
        status, r = self._json("/api/tool", body={"name": "Backup Pistol",
                                                  "category": "GBB Pistol"})
        self.assertEqual(status, 200)
        db = Database.load(self.path)
        self.assertIn(r["id"], db.tools)
        self.assertEqual(db.tools[r["id"]].category, "GBB Pistol")
        status, _ = self._json("/api/tool", body={"name": ""})
        self.assertEqual(status, 400)


class TestWebExtras(TestWeb):
    """Goals, deletion, export, printing and installing -- from the phone."""

    def test_goals_can_be_set_and_removed(self):
        status, out = self._json("/api/goal", {"action": "add",
                                               "metric": "group_size",
                                               "target": 40})
        self.assertEqual(status, 200)
        self.assertEqual(len(out["goals"]), 1)
        gid = out["goals"][0]["id"]
        self.assertEqual(self._json("/api/state")[1]["goals"][0]["target"], 40.0)
        self.assertEqual(self._json("/api/goal", {"action": "rm", "id": gid})[0], 200)
        self.assertEqual(self._json("/api/state")[1]["goals"], [])

    def test_goal_input_is_checked(self):
        for body in ({"action": "add", "metric": "evil", "target": 1},
                     {"action": "add", "metric": "group_size"},
                     {"action": "add", "metric": "group_size", "target": 1,
                      "tool_id": "ghost"},
                     {"action": "rm", "id": "nope"},
                     {"action": "hack"}):
            self.assertEqual(self._json("/api/goal", body)[0], 400, body)

    def test_session_can_be_deleted(self):
        self.assertEqual(self._json("/api/session/delete", {"id": "s1"})[0], 200)
        self.assertEqual(self._json("/api/state")[1]["sessions"], [])
        self.assertEqual(self._json("/api/session/delete", {"id": "s1"})[0], 400)

    def test_printable_face(self):
        status, raw = self._call("/target.html?face=Practice%20Face&paper=a4")
        self.assertEqual(status, 200)
        self.assertIn(b"210.000mm", raw)
        for bad in ("?face=Nope", "?face=Practice%20Face&paper=zzz",
                    "?face=Practice%20Face&distance=abc",
                    "?face=Practice%20Face&distance=99999"):
            self.assertEqual(self._call("/target.html" + bad)[0], 400, bad)

    def test_export_downloads(self):
        status, raw = self._call("/export.csv")
        self.assertEqual(status, 200)
        self.assertIn(b"s1", raw)
        self.assertEqual(self._call("/export.json")[0], 200)

    def test_installable_on_a_phone(self):
        status, man = self._json("/manifest.webmanifest")
        self.assertEqual(status, 200)
        self.assertEqual(man["display"], "standalone")
        # The key rides in start_url or the installed icon opens a 403.
        self.assertIn(TOKEN, man["start_url"])
        _, page = self._call("/")
        self.assertIn(b'rel="manifest"', page)
        self.assertIn(b'crossorigin="use-credentials"', page)

    def test_icon_is_open_but_nothing_else_is(self):
        # The manifest fetches the icon without credentials, so it has to be.
        self.assertEqual(self._call("/icon.png", token=None)[0], 200)
        self.assertEqual(self._call("/export.csv", token=None)[0], 403)
        self.assertEqual(self._call("/target.html?face=Practice%20Face",
                                    token=None)[0], 403)

    def test_stats_say_what_to_do_about_the_zero_error(self):
        db = Database.load(self.path)
        db.add_tool(Tool("scoped", "Scoped", category="Other",
                         sight_click_mrad=0.1))
        db.save()
        _, out = self._json("/api/stats", {
            "shots": [{"x_mm": 12, "y_mm": 8}, {"x_mm": 14, "y_mm": 10}],
            "distance_m": 10, "tool_id": "scoped"})
        self.assertIn("clicks left", out["correction"])
        # No tool, no clicks -- but still an answer.
        _, plain = self._json("/api/stats", {
            "shots": [{"x_mm": 12, "y_mm": 8}], "distance_m": 10})
        self.assertIn("MOA", plain["correction"])

    def test_access_key_survives_a_restart_but_never_syncs(self):
        # A key that changed every run would break the phone's home-screen icon.
        srv = web.make_server(self.path, "127.0.0.1", 0)
        try:
            first = srv.token
        finally:
            srv.server_close()
        srv2 = web.make_server(self.path, "127.0.0.1", 0)
        try:
            self.assertEqual(srv2.token, first)
        finally:
            srv2.server_close()
        bundle = web._bundle(Database.load(self.path))
        self.assertNotIn("web_key", bundle["settings"])


if __name__ == "__main__":
    unittest.main()
