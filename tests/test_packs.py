"""Equipment pack tests: validation, the sensitivity ceiling, and the split
between the app (engine) and packs (content)."""

import io
import json
import os
import shutil
import tempfile
import unittest

from marksman import drills, packs
from marksman.storage import Database

GOOD = {
    "schema_version": 1,
    "id": "testpack",
    "name": "Test Pack",
    "sensitivity": 1,
    "terms": {"tool": "slingshot", "projectile": "pebble"},
    "categories": ["Handheld"],
    "targets": [{"name": "Test Face 5m", "ten_ring_mm": 60.0,
                 "ring_step_mm": 30.0, "face_mm": 600.0}],
    "drills": [{"id": "test-5", "name": "Test Five", "family": "Precision",
                "distance_m": 5.0, "shots": 5, "target": "Test Face 5m",
                "metric": "group_size", "cutoffs": [200.0, 150.0, 100.0, 60.0],
                "why": "because", "how": ["shoot it"], "cues": ["aim"]}],
}


def _copy(**changes):
    d = json.loads(json.dumps(GOOD))
    d.update(changes)
    return d


class TestValidation(unittest.TestCase):

    def test_a_good_pack_passes(self):
        p = packs.validate(_copy(), "t.json")
        self.assertEqual(p["id"], "testpack")
        self.assertEqual(p["drills"][0]["pack"], "testpack")

    def test_bad_packs_are_rejected_with_a_reason(self):
        cases = {
            "wrong schema": _copy(schema_version=2),
            "no id": {k: v for k, v in GOOD.items() if k != "id"},
            "path-ish id": _copy(id="../../etc"),
            "sensitivity out of range": _copy(sensitivity=0),
            "sensitivity missing": {k: v for k, v in GOOD.items()
                                    if k != "sensitivity"},
            "not an object": [],
        }
        for why, raw in cases.items():
            with self.assertRaises(packs.PackError, msg=why):
                packs.validate(raw, "t.json")

    def test_drill_rules(self):
        def drill(**ch):
            d = _copy()
            d["drills"][0].update(ch)
            return d
        for why, raw in {
            "unknown metric": drill(metric="vibes"),
            "too few cutoffs": drill(cutoffs=[1.0, 2.0, 3.0]),
            "non-numeric cutoffs": drill(cutoffs=["a", "b", "c", "d"]),
            "cutoffs get easier": drill(cutoffs=[60.0, 100.0, 150.0, 200.0]),
            "no distance": drill(distance_m=0),
            "silly shot count": drill(shots=0),
        }.items():
            with self.assertRaises(packs.PackError, msg=why):
                packs.validate(raw, "t.json")

    def test_higher_is_better_metrics_order_the_other_way(self):
        ok = _copy()
        ok["drills"][0].update(metric="score", cutoffs=[50.0, 65.0, 80.0, 92.0])
        self.assertTrue(packs.validate(ok, "t.json")["drills"])
        bad = _copy()
        bad["drills"][0].update(metric="score", cutoffs=[92.0, 80.0, 65.0, 50.0])
        with self.assertRaises(packs.PackError):
            packs.validate(bad, "t.json")

    def test_duplicate_drill_ids_are_caught(self):
        dupe = _copy()
        dupe["drills"].append(dict(dupe["drills"][0]))
        with self.assertRaises(packs.PackError):
            packs.validate(dupe, "t.json")


class TestBundled(unittest.TestCase):

    def test_the_bundled_packs_are_valid(self):
        found = packs.discover()
        self.assertGreaterEqual(len(found), 2)
        for p in found:
            self.assertIsNone(p.get("error"), (p["name"], p.get("error")))
        ids = [p["id"] for p in found]
        self.assertIn("airsoft", ids)
        self.assertIn("foam", ids)

    def test_bundled_packs_stay_within_the_default_ceiling(self):
        # A fresh install must never load anything above recreational content.
        for p in packs.discover():
            self.assertLessEqual(p["sensitivity"], packs.DEFAULT_MAX_SENSITIVITY,
                                 p["id"])

    def test_the_engine_carries_no_content_of_its_own(self):
        # Every drill must come from a pack -- nothing hardcoded in the app.
        for d in drills.all_drills():
            self.assertTrue(d["pack"], d["id"])


class TestCeilingAndToggles(unittest.TestCase):

    def setUp(self):
        packs.reset()
        self.db = Database(path=os.devnull)
        self.tmp = tempfile.mkdtemp()
        self._old_env = os.environ.get("MARKSMAN_PACKS")
        os.environ["MARKSMAN_PACKS"] = self.tmp

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("MARKSMAN_PACKS", None)
        else:
            os.environ["MARKSMAN_PACKS"] = self._old_env
        shutil.rmtree(self.tmp, ignore_errors=True)
        packs.reset()

    def _write(self, raw):
        with open(os.path.join(self.tmp, raw["id"] + ".json"), "w",
                  encoding="utf-8") as fh:
            json.dump(raw, fh)

    def test_a_spicy_pack_is_found_but_not_loaded_until_allowed(self):
        self._write(_copy(id="spicy", name="Spicy", sensitivity=4))
        rows = {r["id"]: r for r in packs.status(self.db)}
        self.assertIn("spicy", rows)                    # discovered...
        self.assertFalse(rows["spicy"]["active"])       # ...but not loaded
        self.assertIn("ceiling", rows["spicy"]["reason"])
        self.assertNotIn("spicy", [p["id"] for p in packs.load(self.db)])

        self.db.settings["packs"] = {"max_sensitivity": 4}
        packs.reset()
        self.assertIn("spicy", [p["id"] for p in packs.load(self.db)])

    def test_disabling_a_pack_removes_its_drills(self):
        self.db.settings["packs"] = {"disabled": ["airsoft"]}
        packs.reset()
        ids = [d["id"] for d in drills.all_drills(self.db)]
        self.assertNotIn("group-10", ids)
        self.assertIn("foam-group-5", ids)

    def test_a_broken_pack_does_not_stop_the_others(self):
        with open(os.path.join(self.tmp, "broken.json"), "w", encoding="utf-8") as fh:
            fh.write("{ not json at all")
        rows = {r["id"]: r for r in packs.status(self.db)}
        self.assertIn("airsoft", rows)
        self.assertTrue(rows["airsoft"]["active"])
        broken = [r for r in rows.values() if r.get("error")]
        self.assertEqual(len(broken), 1)
        self.assertFalse(broken[0]["active"])

    def test_a_user_pack_overrides_a_bundled_one_by_id(self):
        self._write(_copy(id="foam", name="My Own Foam", sensitivity=1,
                          drills=[], targets=[]))
        rows = {r["id"]: r for r in packs.status(self.db)}
        self.assertEqual(rows["foam"]["name"], "My Own Foam")
        self.assertFalse(rows["foam"]["bundled"])

    def test_terminology_only_applies_when_one_pack_is_active(self):
        self.db.settings["packs"] = {"disabled": ["airsoft"]}
        packs.reset()
        self.assertEqual(packs.term("projectile", self.db), "dart")
        self.db.settings["packs"] = {}
        packs.reset()
        self.assertEqual(packs.term("projectile", self.db), "projectile")

    def test_install_validates_before_copying(self):
        src = os.path.join(self.tmp, "candidate.txt")
        with open(src, "w", encoding="utf-8") as fh:
            json.dump(_copy(schema_version=99), fh)
        with self.assertRaises(packs.PackError):
            packs.install(src, self.db)
        with open(src, "w", encoding="utf-8") as fh:
            json.dump(_copy(id="fresh"), fh)
        got = packs.install(src, self.db)
        self.assertEqual(got["id"], "fresh")
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "fresh.json")))


class TestBrandIndependence(unittest.TestCase):
    """The project ships its own look and names no one else's products.

    The aesthetic is foam-dart, which is a *generic* thing -- a soft cylinder
    with a rounded head. Trademarked brands, product lines and models belong to
    their owners and appear nowhere in what we ship. A user's own pack is their
    content and is not scanned here.
    """

    #: Marks that must not appear in shipped code, assets or documentation.
    FORBIDDEN = (
        "nerf", "hasbro", "n-strike", "nstrike", "accustrike",
        "zombie strike", "elite dart", "mega dart", "ultra dart",
        "rival ball", "x-shot", "adventure force",
    )

    SHIPPED = ("marksman", "README.md", "DISCLAIMER.md",
               "THIRD_PARTY_NOTICES.md", "CONTRIBUTING.md", "SECURITY.md",
               "pyproject.toml", "demo.py")

    def _files(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for entry in self.SHIPPED:
            path = os.path.join(root, entry)
            if os.path.isfile(path):
                yield path
            for base, dirs, names in os.walk(path):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for name in names:
                    if name.endswith((".py", ".json", ".md", ".toml")):
                        yield os.path.join(base, name)

    def test_no_third_party_marks_anywhere(self):
        hits = []
        for path in self._files():
            with io.open(path, encoding="utf-8") as fh:
                low = fh.read().lower()
            for mark in self.FORBIDDEN:
                if mark in low:
                    hits.append("%s: %r" % (os.path.basename(path), mark))
        self.assertEqual(hits, [], "third-party marks in shipped files: %s" % hits)

    def test_bundled_packs_name_no_brand_or_model(self):
        for pack in packs.discover():
            if not pack.get("bundled"):
                continue
            blob = json.dumps(pack).lower()
            for mark in self.FORBIDDEN:
                self.assertNotIn(mark, blob, pack.get("id"))

    def test_the_house_skin_exists_and_stays_plain_by_default(self):
        from marksman import theme
        self.assertIn("foam", theme.THEMES)
        # The terminal default stays the colourless skin: piped output must not
        # change just because the app got a look.
        self.assertEqual(theme.DEFAULT_THEME, "mono")
        self.assertEqual(theme.get_theme(None).key, "mono")
        # A window has no such constraint, so it wears the house style.
        from marksman import gui
        self.assertEqual(gui.palette(None), gui.palette("foam"))


if __name__ == "__main__":
    unittest.main()
