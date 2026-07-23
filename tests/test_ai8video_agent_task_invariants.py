from __future__ import annotations

from contextlib import redirect_stdout
import io
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ai8video.batch import task_ledger as task_ledger_module
from ai8video.batch.agent_task_ledger import AgentTaskLedger
from ai8video.batch.agent_task_models import (
    TASK_CANCEL_REQUESTED,
    TASK_CANCELLED,
    TASK_FAILED,
    TASK_RUNNING,
    TASK_SUCCEEDED,
    AgentResult,
    AgentTaskSpec,
    AgentTaskTransition,
    LegacyTaskUpdate,
)
from ai8video.batch.agent_task_scheduler import (
    AgentTaskHandlerSpec,
    AgentTaskScheduler,
)
from ai8video.batch.task_ledger import TaskLedger


class AgentTaskInvariantTest(unittest.TestCase):
    def test_generation_batch_is_mirrored_as_supervisor_root_task(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")
            ledger.ensure_generation_batch(
                session_id="session-root",
                generation_batch_id="gb-root",
                request_snapshot={"message": "生成两条视频"},
            )
            queued = ledger.agent_tasks.get_task("gb-root")
            ledger.update_generation_execution(
                "gb-root",
                execution_state="running",
                worker_id="runtime-root",
            )
            running = ledger.agent_tasks.get_task("gb-root")
            ledger.update_generation_execution(
                "gb-root",
                execution_state="completed",
                result_snapshot={"videoCount": 2},
            )
            completed = ledger.agent_tasks.get_task("gb-root")
            ledger.update_generation_execution("gb-root", execution_state="running")
            late = ledger.agent_tasks.get_task("gb-root")

        self.assertEqual(queued.agent_role, "supervisor")
        self.assertEqual(queued.input_snapshot["snapshotMode"], "redacted")
        self.assertNotIn("生成两条视频", str(queued.input_snapshot))
        self.assertEqual(running.state, TASK_RUNNING)
        self.assertEqual(running.attempt, 1)
        self.assertEqual(completed.state, TASK_SUCCEEDED)
        self.assertEqual(completed.output_snapshot, {"videoCount": 2})
        self.assertEqual(late.version, completed.version)

    def test_generic_transition_cannot_enter_leased_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._agent_ledger(temporary_directory)
            created = ledger.create_task(self._spec("leased-target"))
            for target_state in (TASK_RUNNING, TASK_CANCEL_REQUESTED):
                with self.subTest(target_state=target_state):
                    with self.assertRaisesRegex(ValueError, "leased states"):
                        ledger.transition_task(
                            AgentTaskTransition(
                                created.task_id,
                                created.version,
                                target_state,
                            )
                        )
            unchanged = ledger.get_task(created.task_id)

        self.assertEqual(unchanged.state, "queued")
        self.assertIsNone(unchanged.worker_id)
        self.assertIsNone(unchanged.lease_expires_at)

    def test_cancel_requested_legacy_completion_stays_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._task_ledger(temporary_directory, "cancel-sticky")
            ledger.update_generation_execution(
                "cancel-sticky",
                execution_state="running",
                worker_id="runtime-worker",
            )
            ledger.update_generation_execution(
                "cancel-sticky",
                execution_state="cancel_requested",
                cancel_requested=True,
            )
            ledger.update_generation_execution(
                "cancel-sticky",
                execution_state="completed",
                result_snapshot={"late": True},
            )
            record = ledger.get_generation_batch("cancel-sticky")
            root = ledger.agent_tasks.get_task("cancel-sticky")

        self.assertEqual(record["executionState"], "cancelled")
        self.assertTrue(record["cancelRequested"])
        self.assertIsNone(record["resultSnapshot"])
        self.assertEqual(root.state, TASK_CANCELLED)
        self.assertIsNone(root.output_snapshot)

    def test_repeated_terminal_update_preserves_first_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._task_ledger(temporary_directory, "terminal-first")
            ledger.update_generation_execution(
                "terminal-first",
                execution_state="completed",
                worker_id="winner-worker",
                result_snapshot={"winner": "first"},
            )
            first = ledger.agent_tasks.get_task("terminal-first")
            ledger.update_generation_execution(
                "terminal-first",
                execution_state="completed",
                worker_id="late-worker",
                result_snapshot={"winner": "late"},
            )
            record = ledger.get_generation_batch("terminal-first")
            root = ledger.agent_tasks.get_task("terminal-first")

        self.assertEqual(record["workerId"], "winner-worker")
        self.assertEqual(record["resultSnapshot"], {"winner": "first"})
        self.assertEqual(root.output_snapshot, {"winner": "first"})
        self.assertEqual(root.version, first.version)

    def test_agent_legacy_terminal_sync_is_first_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._agent_ledger(temporary_directory)
            ledger.create_task(self._spec("agent-terminal-first"))
            first = ledger.sync_legacy_execution(
                LegacyTaskUpdate(
                    "agent-terminal-first",
                    execution_state="completed",
                    result_snapshot={"winner": "first"},
                )
            )
            late = ledger.sync_legacy_execution(
                LegacyTaskUpdate(
                    "agent-terminal-first",
                    execution_state="completed",
                    result_snapshot={"winner": "late"},
                )
            )

        self.assertEqual(late.version, first.version)
        self.assertEqual(late.output_snapshot, {"winner": "first"})

    def test_cancel_after_failed_future_prevents_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._agent_ledger(temporary_directory)
            scheduler = self._scheduler(
                ledger,
                lambda task, _cancel: AgentResult(
                    task.task_id,
                    error_type="TemporaryFailure",
                    error_message="retryable",
                ),
            )
            ledger.create_task(self._spec("cancel-failed", max_attempts=2))
            scheduler.run_once()
            self.assertTrue(self._wait_for_future(scheduler, "cancel-failed"))
            cancelled = scheduler.cancel("cancel-failed")
            events = ledger.list_events("cancel-failed")
            scheduler.shutdown()

        self.assertEqual(cancelled.state, TASK_CANCELLED)
        self.assertIsNone(cancelled.next_retry_at)
        self.assertNotIn("task_retry_scheduled", [event.event_type for event in events])

    def test_shutdown_closes_runtime_when_persistence_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._agent_ledger(temporary_directory)
            started = threading.Event()
            release = threading.Event()

            def handler(task, _cancel_event) -> AgentResult:
                started.set()
                release.wait(timeout=2)
                return AgentResult(task.task_id, {"done": True})

            scheduler = self._scheduler(ledger, handler)
            scheduler.enqueue(self._spec("shutdown-storage-error"))
            self.assertTrue(started.wait(timeout=1))
            dispatcher = scheduler._dispatcher
            with patch.object(
                ledger,
                "finish_claimed_task",
                side_effect=sqlite3.OperationalError("database is locked"),
            ), patch.object(
                ledger,
                "request_cancel",
                side_effect=sqlite3.OperationalError("database is locked"),
            ), self.assertLogs(level="WARNING"):
                release.set()
                self.assertTrue(self._wait_for_future(scheduler, "shutdown-storage-error"))
                scheduler.shutdown(grace_seconds=0.05)

        self.assertTrue(scheduler._stop_event.is_set())
        self.assertTrue(scheduler._shutdown_complete)
        self.assertFalse(dispatcher.is_alive())

    def test_shutdown_converts_unserializable_result_to_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._agent_ledger(temporary_directory)
            scheduler = self._scheduler(
                ledger,
                lambda task, _cancel: AgentResult(task.task_id, {"bad": object()}),
            )
            ledger.create_task(self._spec("bad-result"))
            scheduler.run_once()
            self.assertTrue(self._wait_for_future(scheduler, "bad-result"))
            with self.assertLogs(level="WARNING"):
                scheduler.run_once(allow_dispatch=False)
            failed = ledger.get_task("bad-result")
            scheduler.shutdown()

        self.assertEqual(failed.state, TASK_FAILED)
        self.assertEqual(failed.error_type, "TypeError")
        self.assertTrue(scheduler._shutdown_complete)

    def test_deeply_nested_result_converges_to_explicit_failure(self) -> None:
        nested: dict = {}
        for _index in range(1500):
            nested = {"nested": nested}
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._agent_ledger(temporary_directory)
            scheduler = self._scheduler(
                ledger,
                lambda task, _cancel: AgentResult(task.task_id, nested),
            )
            ledger.create_task(self._spec("recursive-result"))
            scheduler.run_once()
            self.assertTrue(self._wait_for_future(scheduler, "recursive-result"))
            with self.assertLogs(level="WARNING"):
                scheduler.run_once(allow_dispatch=False)
            failed = ledger.get_task("recursive-result")
            scheduler.shutdown()

        self.assertEqual(failed.state, TASK_FAILED)
        self.assertEqual(failed.error_type, "RecursionError")
        self.assertEqual(scheduler.active_count, 0)

    def test_wait_for_terminal_does_not_spin_after_shutdown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._agent_ledger(temporary_directory)
            started = threading.Event()
            release = threading.Event()
            first_read = threading.Event()
            reads = 0

            def handler(task, _cancel_event) -> AgentResult:
                started.set()
                release.wait(timeout=2)
                return AgentResult(task.task_id, {"done": True})

            scheduler = self._scheduler(ledger, handler)
            scheduler.enqueue(self._spec("wait-after-stop"))
            self.assertTrue(started.wait(timeout=1))
            original_get = ledger.get_task

            def counted_get(task_id: str):
                nonlocal reads
                reads += 1
                first_read.set()
                return original_get(task_id)

            with patch.object(ledger, "get_task", side_effect=counted_get):
                waiter = threading.Thread(
                    target=lambda: scheduler.wait_for_terminal("wait-after-stop", 0.15)
                )
                waiter.start()
                self.assertTrue(first_read.wait(timeout=1))
                scheduler.shutdown(grace_seconds=0.01)
                waiter.join(timeout=1)
            release.set()

        self.assertFalse(waiter.is_alive())
        self.assertLess(reads, 10)

    def test_cancel_and_completion_updates_are_transactionally_serialized(self) -> None:
        cases = (
            ("cancel_requested", "completed", "cancelled", TASK_CANCELLED),
            ("cancel_requested", "failed", "cancelled", TASK_CANCELLED),
            ("completed", "cancel_requested", "completed", TASK_SUCCEEDED),
            ("failed", "cancel_requested", "failed", TASK_FAILED),
            ("completed", "failed", "completed", TASK_SUCCEEDED),
            ("failed", "completed", "failed", TASK_FAILED),
        )
        for first_state, second_state, expected, expected_root in cases:
            with self.subTest(first_state=first_state, second_state=second_state):
                record, root, errors = self._run_execution_race(first_state, second_state)
                self.assertEqual(errors, [])
                self.assertEqual(record["executionState"], expected)
                self.assertEqual(root.state, expected_root)
                if expected == "cancelled":
                    self.assertIsNone(record["errorType"])
                    self.assertIsNone(root.error_type)

    @staticmethod
    def _agent_ledger(temporary_directory: str) -> AgentTaskLedger:
        return AgentTaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")

    @staticmethod
    def _task_ledger(temporary_directory: str, task_id: str) -> TaskLedger:
        ledger = TaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")
        ledger.ensure_generation_batch(
            session_id="session-invariants",
            generation_batch_id=task_id,
        )
        return ledger

    @staticmethod
    def _spec(task_id: str, *, max_attempts: int = 1) -> AgentTaskSpec:
        return AgentTaskSpec(
            task_id=task_id,
            generation_batch_id="gb-invariants",
            session_id="session-invariants",
            task_type="safe_compute",
            agent_role="specialist",
            max_attempts=max_attempts,
        )

    @staticmethod
    def _scheduler(ledger: AgentTaskLedger, handler) -> AgentTaskScheduler:
        scheduler = AgentTaskScheduler(
            ledger,
            lease_seconds=0.3,
            heartbeat_interval=0.05,
            poll_interval=0.01,
            worker_prefix="invariant-test",
        )
        scheduler.register_handler(
            "safe_compute",
            AgentTaskHandlerSpec(handler, replay_safe=True),
        )
        return scheduler

    @staticmethod
    def _wait_for_future(
        scheduler: AgentTaskScheduler,
        task_id: str,
        timeout: float = 1,
    ) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with scheduler._state_lock:
                active = scheduler._active.get(task_id)
                if active is not None and active.future.done():
                    return True
            time.sleep(0.005)
        return False

    def _run_execution_race(self, first_state: str, second_state: str):
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._task_ledger(temporary_directory, f"race-{first_state}")
            ledger.update_generation_execution(
                f"race-{first_state}",
                execution_state="running",
            )
            first_resolved = threading.Event()
            release_first = threading.Event()
            errors: list[BaseException] = []
            original_resolve = task_ledger_module._resolve_execution_update

            def delayed_resolve(connection, update):
                resolved = original_resolve(connection, update)
                if update.execution_state == first_state:
                    first_resolved.set()
                    release_first.wait(timeout=2)
                return resolved

            def update(state: str) -> None:
                try:
                    ledger.update_generation_execution(
                        f"race-{first_state}",
                        execution_state=state,
                        cancel_requested=state == "cancel_requested",
                        result_snapshot={"winner": state} if state == "completed" else None,
                        error=RuntimeError("late failure") if state == "failed" else None,
                    )
                except BaseException as exc:
                    errors.append(exc)

            with patch.object(task_ledger_module, "_resolve_execution_update", side_effect=delayed_resolve):
                first = threading.Thread(target=update, args=(first_state,))
                second = threading.Thread(target=update, args=(second_state,))
                first.start()
                self.assertTrue(first_resolved.wait(timeout=1))
                second.start()
                time.sleep(0.03)
                release_first.set()
                first.join(timeout=2)
                second.join(timeout=2)
            record = ledger.get_generation_batch(f"race-{first_state}")
            root = ledger.agent_tasks.get_task(f"race-{first_state}")
            return record, root, errors


class WebRuntimeSchedulerLifecycleTest(unittest.TestCase):
    def test_main_always_attempts_scheduler_shutdown(self) -> None:
        from ai8video.interfaces.web import app as web_app

        health = {
            "dryRun": True,
            "hasLLM": False,
            "assetStorePath": "/tmp/assets",
            "archiveBackend": "local",
            "archiveLocalDir": "/tmp/archive",
        }
        with patch.object(sys, "argv", ["ai8video-web", "--port", "18720"]), patch.object(
            web_app,
            "migrate_legacy_result_layout",
            return_value={},
        ), patch.object(web_app, "get_health_payload", return_value=health), patch.object(
            web_app,
            "start_specialist_agent_scheduler",
            side_effect=RuntimeError("startup failed"),
        ), patch.object(web_app, "run"), patch.object(
            web_app,
            "shutdown_specialist_agent_scheduler",
        ) as shutdown, redirect_stdout(io.StringIO()), self.assertLogs(level="WARNING"):
            exit_code = web_app.main()

        self.assertEqual(exit_code, 0)
        shutdown.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
