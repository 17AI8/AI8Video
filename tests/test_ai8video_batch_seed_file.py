from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai8video.batch.batch_report_store import BatchReportStore
from ai8video.batch.batch_seed_file import (
    build_batch_seed_file_from_recent_reports,
    inspect_batch_seed_file,
)
from ai8video.core.config import AI8VideoConfig


class AI8VideoBatchSeedFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.report_store = BatchReportStore(self.root / "reports")
        self.seed_file = self.root / "batch_supervisor" / "seed_messages.txt"
        self.config = AI8VideoConfig(
            batch_seed_file=str(self.seed_file),
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_build_seed_file_from_recent_reports_writes_deduped_messages(self) -> None:
        self.report_store.save(
            {
                "generatedAt": "2026-06-13T09:00:00+08:00",
                "seedMessages": 3,
            },
            seed_messages=["老板讲封号风险", "老板讲私域承接", "老板讲封号风险"],
        )
        self.report_store.save(
            {
                "generatedAt": "2026-06-13T10:00:00+08:00",
                "seedMessages": 2,
            },
            seed_messages=["老板讲私域承接", "老板讲客户复购"],
        )

        payload = build_batch_seed_file_from_recent_reports(
            config=self.config,
            report_store=self.report_store,
            report_limit=5,
            max_messages=10,
        )

        self.assertTrue(self.seed_file.exists())
        self.assertEqual(
            self.seed_file.read_text(encoding="utf-8").splitlines(),
            ["老板讲私域承接", "老板讲客户复购", "老板讲封号风险"],
        )
        self.assertEqual(payload["lineCount"], 3)
        self.assertEqual(payload["reportCount"], 2)
        self.assertEqual(payload["source"], "config")

    def test_inspect_batch_seed_file_reports_missing_file(self) -> None:
        payload = inspect_batch_seed_file(self.config)

        self.assertFalse(payload["exists"])
        self.assertEqual(payload["lineCount"], 0)
        self.assertEqual(payload["preview"], [])
        self.assertEqual(payload["path"], str(self.seed_file.resolve()))

    def test_build_seed_file_requires_recent_report_samples(self) -> None:
        with self.assertRaisesRegex(ValueError, "最近日报里还没有可用候选内容"):
            build_batch_seed_file_from_recent_reports(
                config=self.config,
                report_store=self.report_store,
            )


if __name__ == "__main__":
    unittest.main()
