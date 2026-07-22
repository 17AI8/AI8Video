from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai8video.batch.batch_report_store import BatchReportStore


class BatchReportStoreTest(unittest.TestCase):
    def test_save_creates_daily_json_and_recent_index(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store = BatchReportStore(Path(tempdir) / "batch_reports")
            report = {
                "generatedAt": "2026-06-13T02:03:04+00:00",
                "dryRun": True,
                "targetPassCount": 6,
                "seedMessages": 8,
                "totalVideoAttempts": 9,
                "passCount": 6,
                "retryCount": 2,
                "rejectCount": 1,
                "retryScheduledCount": 2,
                "expansionRoundCount": 1,
                "expandedSeedCount": 3,
                "goalMet": True,
                "topFailureReasons": [{"reason": "画面不稳", "count": 2}],
            }

            saved = store.save(
                report,
                trigger="unit_test",
                source="tests",
                session_id="batch-unit",
                style_hint="商务",
                seed_messages=["老板开会", "私域承接"],
            )

            self.assertEqual(saved["reportDate"], "2026-06-13")
            self.assertTrue(saved["reportPath"].endswith(".json"))
            self.assertTrue(Path(saved["reportPath"]).exists())
            recent = store.read_recent(limit=5)
            self.assertEqual(len(recent), 1)
            self.assertEqual(recent[0]["reportId"], saved["reportId"])
            self.assertEqual(recent[0]["seedMessageSamples"], ["老板开会", "私域承接"])
            self.assertEqual(recent[0]["expansionRoundCount"], 1)
            self.assertEqual(recent[0]["expandedSeedCount"], 3)


if __name__ == "__main__":
    unittest.main()
