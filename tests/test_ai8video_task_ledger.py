from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ai8video.batch.task_ledger import (
    DEFAULT_TASK_LEDGER_PATH,
    PROJECT_ROOT,
    TaskLedger,
)


class TaskLedgerTest(unittest.TestCase):
    def test_default_path_is_stable_across_working_directories(self) -> None:
        original_working_directory = Path.cwd()
        with tempfile.TemporaryDirectory() as temporary_directory:
            try:
                os.chdir(temporary_directory)
                ledger = TaskLedger()
            finally:
                os.chdir(original_working_directory)

        expected_path = PROJECT_ROOT / "temp" / "ai8video" / "task_ledger.sqlite3"
        self.assertTrue(DEFAULT_TASK_LEDGER_PATH.is_absolute())
        self.assertEqual(ledger.path, expected_path.resolve())

    def test_upsert_generation_batch_creates_and_updates_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")

            ledger.upsert_generation_batch(
                session_id="session-a",
                generation_batch_id="gb-session-a-001",
                status="active",
                phase="submitting",
                progress={"totalRequested": 2, "items": [{"status": "submitted"}]},
            )
            ledger.upsert_generation_batch(
                session_id="session-a",
                generation_batch_id="gb-session-a-001",
                status="completed",
                phase="completed",
                progress={"totalRequested": 2, "items": [{"status": "succeeded"}]},
            )

            record = ledger.get_generation_batch("gb-session-a-001")

        self.assertIsNotNone(record)
        self.assertEqual(record["generationBatchId"], "gb-session-a-001")
        self.assertEqual(record["sessionId"], "session-a")
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["phase"], "completed")
        self.assertEqual(record["progress"]["totalRequested"], 2)
        self.assertEqual(record["progress"]["items"][0]["status"], "succeeded")
        self.assertIsNotNone(record["completedAt"])

    def test_list_active_generation_batches_excludes_terminal_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")
            ledger.upsert_generation_batch(
                session_id="session-a",
                generation_batch_id="gb-active",
                status="active",
            )
            ledger.upsert_generation_batch(
                session_id="session-b",
                generation_batch_id="gb-failed",
                status="failed",
            )

            active_records = ledger.list_active_generation_batches()

        self.assertEqual([record["generationBatchId"] for record in active_records], ["gb-active"])

    def test_terminal_record_rejects_late_active_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")
            ledger.upsert_generation_batch(
                session_id="session-terminal",
                generation_batch_id="gb-terminal",
                status="completed",
                phase="completed",
                progress={"status": "completed", "items": [{"status": "succeeded"}]},
            )
            completed_record = ledger.get_generation_batch("gb-terminal")

            ledger.upsert_generation_batch(
                session_id="session-terminal",
                generation_batch_id="gb-terminal",
                status="active",
                phase="polling",
                progress={"status": "active", "items": [{"status": "polling"}]},
            )
            record_after_late_update = ledger.get_generation_batch("gb-terminal")
            active_records = ledger.list_active_generation_batches()

        self.assertEqual(record_after_late_update, completed_record)
        self.assertEqual(record_after_late_update["status"], "completed")
        self.assertEqual(record_after_late_update["progress"]["items"][0]["status"], "succeeded")
        self.assertEqual(active_records, [])

    def test_get_latest_generation_batch_for_session_returns_most_recent_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")
            ledger.upsert_generation_batch(
                session_id="session-a",
                generation_batch_id="gb-session-a-old",
                status="active",
                progress={"items": [{"status": "submitted"}]},
            )
            ledger.upsert_generation_batch(
                session_id="session-b",
                generation_batch_id="gb-session-b-newer-but-other-session",
                status="active",
            )
            ledger.upsert_generation_batch(
                session_id="session-a",
                generation_batch_id="gb-session-a-new",
                status="active",
                progress={"items": [{"status": "polling"}]},
            )

            record = ledger.get_latest_generation_batch_for_session("session-a")

        self.assertIsNotNone(record)
        self.assertEqual(record["generationBatchId"], "gb-session-a-new")
        self.assertEqual(record["sessionId"], "session-a")
        self.assertEqual(record["progress"]["items"][0]["status"], "polling")

    def test_upsert_generation_batch_rejects_missing_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")

            with self.assertRaisesRegex(ValueError, "generation_batch_id is required"):
                ledger.upsert_generation_batch(
                    session_id="session-a",
                    generation_batch_id=" ",
                    status="active",
                )

    def test_execution_metadata_is_migrated_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")
            ledger.ensure_generation_batch(
                session_id="session-execution",
                generation_batch_id="gb-execution-001",
                request_snapshot={"message": "生成一条视频"},
            )
            ledger.update_generation_execution(
                "gb-execution-001",
                execution_state="running",
                worker_id="ai8video-runtime-001",
                cancel_requested=False,
            )
            record = ledger.get_generation_batch("gb-execution-001")

        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "queued")
        self.assertEqual(record["taskType"], "chat_generation")
        self.assertEqual(record["executionState"], "running")
        self.assertEqual(record["workerId"], "ai8video-runtime-001")
        self.assertFalse(record["cancelRequested"])
        self.assertEqual(record["requestSnapshot"]["message"], "生成一条视频")

    def test_existing_legacy_schema_is_migrated_without_losing_batches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "task_ledger.sqlite3"
            with sqlite3.connect(path) as connection:
                connection.execute(
                    """
                    CREATE TABLE generation_batches (
                        generation_batch_id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        phase TEXT,
                        progress_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        completed_at TEXT
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO generation_batches VALUES
                    ('gb-legacy', 'session-legacy', 'active', 'generating', '{}',
                     '2026-07-17T00:00:00Z', '2026-07-17T00:00:00Z', NULL)
                    """
                )

            record = TaskLedger(path).get_generation_batch("gb-legacy")

        self.assertIsNotNone(record)
        self.assertEqual(record["sessionId"], "session-legacy")
        self.assertEqual(record["status"], "active")
        self.assertFalse(record["cancelRequested"])
        self.assertIsNone(record["executionState"])


if __name__ == "__main__":
    unittest.main()
