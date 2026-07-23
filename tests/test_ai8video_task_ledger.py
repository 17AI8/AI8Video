from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_first_terminal_batch_status_wins_over_late_different_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")
            ledger.upsert_generation_batch(
                session_id="session-terminal-first",
                generation_batch_id="gb-terminal-first",
                status="completed",
                phase="completed",
                progress={"status": "completed"},
            )
            first = ledger.get_generation_batch("gb-terminal-first")
            ledger.upsert_generation_batch(
                session_id="session-terminal-first",
                generation_batch_id="gb-terminal-first",
                status="failed",
                phase="failed",
                progress={"status": "failed"},
            )
            after_late_failure = ledger.get_generation_batch("gb-terminal-first")

        self.assertEqual(after_late_failure, first)
        self.assertEqual(after_late_failure["status"], "completed")

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
        self.assertEqual(record["requestSnapshot"]["snapshotMode"], "redacted")
        self.assertEqual(record["requestSnapshot"]["fields"]["message"]["length"], 6)
        self.assertNotIn("生成一条视频", str(record["requestSnapshot"]))

    def test_ensure_existing_batch_does_not_repeat_queued_agent_sync(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")
            for _ in range(2):
                ledger.ensure_generation_batch(
                    session_id="session-idempotent",
                    generation_batch_id="gb-idempotent",
                    request_snapshot={"message": "生成一条视频"},
                )
            record = ledger.get_generation_batch("gb-idempotent")
            root = ledger.agent_tasks.get_task("gb-idempotent")
            events = ledger.agent_tasks.list_events("gb-idempotent")

        self.assertEqual(record["executionState"], "queued")
        self.assertEqual(root.version, 0)
        self.assertEqual([event.event_type for event in events], ["task_created"])

    def test_terminal_execution_metadata_can_arrive_after_batch_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")
            ledger.ensure_generation_batch(
                session_id="session-terminal-metadata",
                generation_batch_id="gb-terminal-metadata",
            )
            ledger.update_generation_execution(
                "gb-terminal-metadata",
                execution_state="running",
                worker_id="runtime-worker",
            )
            ledger.upsert_generation_batch(
                session_id="session-terminal-metadata",
                generation_batch_id="gb-terminal-metadata",
                status="completed",
                phase="completed",
                progress={"status": "completed"},
            )
            ledger.update_generation_execution(
                "gb-terminal-metadata",
                execution_state="completed",
                result_snapshot={"videoCount": 1},
            )
            record = ledger.get_generation_batch("gb-terminal-metadata")
            root = ledger.agent_tasks.get_task("gb-terminal-metadata")

        self.assertIsNotNone(record["completedAt"])
        self.assertEqual(record["executionState"], "completed")
        self.assertEqual(record["resultSnapshot"], {"videoCount": 1})
        self.assertEqual(root.state, "succeeded")

    def test_terminal_execution_rejects_late_nonterminal_regression(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")
            ledger.ensure_generation_batch(
                session_id="session-execution-terminal",
                generation_batch_id="gb-execution-terminal",
            )
            ledger.update_generation_execution(
                "gb-execution-terminal",
                execution_state="completed",
                worker_id="runtime-worker",
                result_snapshot={"videoCount": 1},
            )
            completed_root = ledger.agent_tasks.get_task("gb-execution-terminal")
            ledger.update_generation_execution(
                "gb-execution-terminal",
                execution_state="running",
                worker_id="late-worker",
            )
            record = ledger.get_generation_batch("gb-execution-terminal")
            root = ledger.agent_tasks.get_task("gb-execution-terminal")

        self.assertEqual(record["executionState"], "completed")
        self.assertEqual(record["workerId"], "runtime-worker")
        self.assertEqual(root.state, "succeeded")
        self.assertEqual(root.version, completed_root.version)

    def test_ensure_existing_batch_cannot_regress_active_running_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")
            ledger.ensure_generation_batch(
                session_id="session-initial-race",
                generation_batch_id="gb-initial-race",
            )
            ledger.upsert_generation_batch(
                session_id="session-initial-race",
                generation_batch_id="gb-initial-race",
                status="active",
                phase="generating",
            )
            ledger.update_generation_execution(
                "gb-initial-race",
                execution_state="running",
                worker_id="worker-running",
            )
            ledger.ensure_generation_batch(
                session_id="session-initial-race",
                generation_batch_id="gb-initial-race",
                task_type="chat_generation",
            )
            record = ledger.get_generation_batch("gb-initial-race")

        self.assertEqual(record["status"], "active")
        self.assertEqual(record["phase"], "generating")
        self.assertEqual(record["executionState"], "running")
        self.assertEqual(record["workerId"], "worker-running")

    def test_late_queued_execution_cannot_regress_running_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")
            ledger.ensure_generation_batch(
                session_id="session-late-queued",
                generation_batch_id="gb-late-queued",
            )
            ledger.update_generation_execution(
                "gb-late-queued",
                execution_state="running",
                worker_id="worker-running",
            )
            running_root = ledger.agent_tasks.get_task("gb-late-queued")
            ledger.update_generation_execution(
                "gb-late-queued",
                execution_state="queued",
            )
            record = ledger.get_generation_batch("gb-late-queued")
            root = ledger.agent_tasks.get_task("gb-late-queued")

        self.assertEqual(record["executionState"], "running")
        self.assertEqual(record["workerId"], "worker-running")
        self.assertEqual(root.state, "running")
        self.assertEqual(root.version, running_root.version)

    def test_final_cancelled_state_wins_over_cancel_requested_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")
            ledger.ensure_generation_batch(
                session_id="session-final-cancel",
                generation_batch_id="gb-final-cancel",
            )
            ledger.update_generation_execution(
                "gb-final-cancel",
                execution_state="running",
                worker_id="worker-cancel",
            )
            ledger.update_generation_execution(
                "gb-final-cancel",
                execution_state="cancelled",
                cancel_requested=True,
            )
            record = ledger.get_generation_batch("gb-final-cancel")
            root = ledger.agent_tasks.get_task("gb-final-cancel")

        self.assertEqual(record["executionState"], "cancelled")
        self.assertTrue(record["cancelRequested"])
        self.assertEqual(root.state, "cancelled")
        self.assertIsNotNone(root.completed_at)

    def test_generation_and_agent_execution_updates_roll_back_together(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")
            ledger.ensure_generation_batch(
                session_id="session-atomic-sync",
                generation_batch_id="gb-atomic-sync",
            )
            ledger.update_generation_execution(
                "gb-atomic-sync",
                execution_state="running",
                worker_id="worker-running",
            )
            with patch.object(
                ledger.agent_tasks,
                "sync_legacy_execution_in_connection",
                side_effect=RuntimeError("injected sync failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "injected sync failure"):
                    ledger.update_generation_execution(
                        "gb-atomic-sync",
                        execution_state="completed",
                        result_snapshot={"videoCount": 1},
                    )
            record = ledger.get_generation_batch("gb-atomic-sync")
            root = ledger.agent_tasks.get_task("gb-atomic-sync")

        self.assertEqual(record["executionState"], "running")
        self.assertIsNone(record["resultSnapshot"])
        self.assertEqual(root.state, "running")

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

    def test_concurrent_legacy_schema_initialization_is_serialized(self) -> None:
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
            barrier = threading.Barrier(9)
            errors: list[BaseException] = []

            def initialize(index: int) -> None:
                try:
                    barrier.wait(timeout=2)
                    TaskLedger(path).ensure_generation_batch(
                        session_id=f"session-concurrent-{index}",
                        generation_batch_id=f"gb-concurrent-{index}",
                    )
                except BaseException as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=initialize, args=(index,)) for index in range(8)]
            for thread in threads:
                thread.start()
            barrier.wait(timeout=2)
            for thread in threads:
                thread.join(timeout=5)
            records = [
                TaskLedger(path).get_generation_batch(f"gb-concurrent-{index}")
                for index in range(8)
            ]

        self.assertEqual(errors, [])
        self.assertTrue(all(record is not None for record in records))


if __name__ == "__main__":
    unittest.main()
