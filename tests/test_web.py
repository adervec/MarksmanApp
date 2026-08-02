"""Web app tests: a real server on an ephemeral port, driven with urllib."""

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from marksman import web
from marksman.grouping import analyze_group
from marksman.models import Session, Shot, Tool
from marksman.storage import Database

TOKEN = "testtok"


class TestWeb(unittest.TestCase):

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(self.path)
        db = Database(path=self.path)
        db.add_tool(Tool("aeg1", "Training AEG", category="AEG", bb_mm=6.0))
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
        self.assertEqual(len(s["drills"]), 12)
        self.assertEqual(s["tools"][0]["id"], "aeg1")
        self.assertGreaterEqual(len(s["targets"]), 3)
        self.assertEqual(len(s["plan"]), 3)
        group10 = next(d for d in s["drills"] if d["id"] == "group-10")
        self.assertEqual(group10["tier"], "Sharp")        # 30 mm PR from setUp

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

    def test_add_tool(self):
        status, r = self._json("/api/tool", body={"name": "Backup Pistol",
                                                  "category": "GBB Pistol"})
        self.assertEqual(status, 200)
        db = Database.load(self.path)
        self.assertIn(r["id"], db.tools)
        self.assertEqual(db.tools[r["id"]].category, "GBB Pistol")
        status, _ = self._json("/api/tool", body={"name": ""})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
