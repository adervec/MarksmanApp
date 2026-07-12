import unittest
from datetime import date

from marksman.models import Shot, Tool, Session
from marksman.grouping import analyze_group
from marksman.storage import Database
from marksman import coach


def _db():
    db = Database()
    db.add_tool(Tool("aeg1", "Training AEG", category="AEG", bb_mm=6.0))
    for i, (d, spread) in enumerate([("2026-01-01", 60.0), ("2026-01-08", 40.0)]):
        shots = [Shot(-spread / 2, 0.0), Shot(spread / 2, 0.0)]
        db.add_session(Session("s%d" % i, "aeg1", d, shots=shots,
                               stats=analyze_group(shots), distance_m=10.0))
    return db


class TestCoach(unittest.TestCase):
    def test_streak(self):
        dates = {"2026-01-10", "2026-01-09", "2026-01-08", "2026-01-06"}
        self.assertEqual(coach.current_streak(dates, date(2026, 1, 10)), 3)
        # today empty but yesterday active -> streak still counts back from yesterday
        self.assertEqual(coach.current_streak(dates, date(2026, 1, 11)), 3)
        self.assertEqual(coach.current_streak(set(), date(2026, 1, 11)), 0)

    def test_dataset_and_digest(self):
        ds = coach.build_dataset(_db(), today=date(2026, 1, 8))
        self.assertEqual(ds["totals"]["sessions"], 2)
        self.assertEqual(ds["streakDays"], 1)
        self.assertEqual(len(ds["tools"]), 1)
        self.assertIn("aeg1", ds["byTool"])
        digest = coach.build_digest(ds, "do the thing")
        self.assertIn("do the thing", digest)
        self.assertIn("aeg1", digest)

    def test_apply_reply_and_idempotency(self):
        db = _db()
        reply = {
            "analysis": "You are grouping tighter.",
            "focus": "Trigger control",
            "drills": [{"name": "Dot drill", "why": "steady press", "how": "10 slow shots"}],
            "toolTips": [{"toolId": "aeg1", "tip": "check hop-up"},
                         {"toolId": "ghost", "tip": "dropped: unknown tool"}],
            "notes": [{"sessionId": "s0", "text": "loose first session"}],
        }
        r1 = coach.apply_reply(db, reply, "2026-01-09T10:00:00")
        self.assertTrue(r1["applied"])
        self.assertEqual(r1["drills"], 1)
        self.assertEqual(r1["toolTips"], 1)            # unknown tool dropped
        c = db.settings["coaching"]
        self.assertEqual(c["focus"], "Trigger control")
        # Applying the identical reply again is a no-op.
        r2 = coach.apply_reply(db, reply, "2026-01-09T11:00:00")
        self.assertFalse(r2["applied"])

    def test_parse_reply_from_markdown(self):
        text = 'Sure!\n```json\n{"focus": "x", "drills": []}\n```\nthanks'
        self.assertEqual(coach.parse_reply(text)["focus"], "x")


if __name__ == "__main__":
    unittest.main()
