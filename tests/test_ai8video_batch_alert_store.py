from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai8video.batch.batch_alert_store import BatchAlertStore


class BatchAlertStoreTest(unittest.TestCase):
    def test_save_creates_daily_json_and_recent_index(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store = BatchAlertStore(Path(tempdir) / "batch_alerts")
            alert = {
                "createdAt": "2026-06-13T09:03:04+08:00",
                "kind": "goal_not_met",
                "level": "warn",
                "message": "日报未达标：通过 12/30",
                "reportId": "20260613-090304-a1b2c3d4",
                "reportPath": "/tmp/report.json",
                "goalMet": False,
                "passRate": 0.4,
                "consecutiveLowPassRuns": 1,
            }

            saved = store.save(alert)

            self.assertEqual(saved["alertDate"], "2026-06-13")
            self.assertTrue(saved["alertPath"].endswith(".json"))
            self.assertTrue(Path(saved["alertPath"]).exists())
            recent = store.read_recent(limit=5)
            self.assertEqual(len(recent), 1)
            self.assertEqual(recent[0]["alertId"], saved["alertId"])
            self.assertEqual(recent[0]["kind"], "goal_not_met")


if __name__ == "__main__":
    unittest.main()
