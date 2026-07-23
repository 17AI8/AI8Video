from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from ai8video.batch.agent_task_ledger import AgentTaskLedger
from ai8video.batch.agent_task_models import (
    TASK_CANCELLED,
    TASK_FAILED,
    TASK_RECOVERY_REQUIRED,
    TASK_SUCCEEDED,
    AgentResult,
    AgentTaskSpec,
    AgentTaskTransition,
)
from ai8video.batch.agent_task_scheduler import (
    AgentTaskHandlerSpec,
    AgentTaskScheduler,
)


class AgentTaskSchedulerTest(unittest.TestCase):
    def test_scheduler_never_exceeds_max_live_handlers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            release = threading.Event()
            two_started = threading.Event()
            lock = threading.Lock()
            counters = {"active": 0, "peak": 0, "started": 0}

            def handler(task, _cancel_event) -> AgentResult:
                with lock:
                    counters["active"] += 1
                    counters["started"] += 1
                    counters["peak"] = max(counters["peak"], counters["active"])
                    if counters["started"] >= 2:
                        two_started.set()
                release.wait(timeout=2)
                with lock:
                    counters["active"] -= 1
                return AgentResult(task.task_id, {"done": True})

            scheduler = self._scheduler(ledger, handler, max_concurrency=2)
            try:
                for index in range(3):
                    scheduler.enqueue(self._spec(f"bounded-{index}"))
                self.assertTrue(two_started.wait(timeout=1))
                tasks = ledger.list_tasks("gb-scheduler")
                self.assertEqual(sum(task.state == "running" for task in tasks), 2)
                queued = [task for task in tasks if task.state == "queued"]
                self.assertEqual(len(queued), 1)
                self.assertEqual(queued[0].attempt, 0)

                release.set()
                terminal = [
                    scheduler.wait_for_terminal(f"bounded-{index}", timeout_seconds=1)
                    for index in range(3)
                ]
            finally:
                release.set()
                scheduler.shutdown()

        self.assertEqual(counters["peak"], 2)
        self.assertTrue(all(task.state == TASK_SUCCEEDED for task in terminal))

    def test_cancelled_handler_holds_slot_until_it_exits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            first_started = threading.Event()
            second_started = threading.Event()
            release_first = threading.Event()

            def handler(task, _cancel_event) -> AgentResult:
                if task.task_id == "cancel-first":
                    first_started.set()
                    release_first.wait(timeout=2)
                else:
                    second_started.set()
                return AgentResult(task.task_id, {"done": True})

            scheduler = self._scheduler(ledger, handler, max_concurrency=1)
            try:
                scheduler.enqueue(self._spec("cancel-first"))
                self.assertTrue(first_started.wait(timeout=1))
                scheduler.enqueue(self._spec("cancel-second"))
                scheduler.cancel("cancel-first")
                self.assertFalse(second_started.wait(timeout=0.1))

                release_first.set()
                first = scheduler.wait_for_terminal("cancel-first", timeout_seconds=1)
                self.assertTrue(second_started.wait(timeout=1))
                second = scheduler.wait_for_terminal("cancel-second", timeout_seconds=1)
            finally:
                release_first.set()
                scheduler.shutdown()

        self.assertEqual(first.state, TASK_CANCELLED)
        self.assertEqual(second.state, TASK_SUCCEEDED)

    def test_startup_replays_only_registered_safe_task_types(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            calls: list[str] = []
            safe = ledger.create_task(self._spec("recover-safe", max_attempts=2))
            unsafe = ledger.create_task(
                self._spec("recover-unsafe", task_type="unsafe_effect", max_attempts=2)
            )
            ledger.claim_task(safe.task_id, "old-safe", safe.version, lease_seconds=1, now=1000)
            ledger.claim_task(unsafe.task_id, "old-unsafe", unsafe.version, lease_seconds=1, now=1000)
            ledger.mark_expired_leases_for_recovery(now=1002)

            def handler(task, _cancel_event) -> AgentResult:
                calls.append(task.task_id)
                return AgentResult(task.task_id, {"recovered": True})

            scheduler = AgentTaskScheduler(ledger, poll_interval=0.01)
            scheduler.register_handler(
                "safe_compute",
                AgentTaskHandlerSpec(handler, replay_safe=True),
            )
            scheduler.register_handler(
                "unsafe_effect",
                AgentTaskHandlerSpec(handler, replay_safe=False),
            )
            try:
                scheduler.start()
                recovered = scheduler.wait_for_terminal("recover-safe", timeout_seconds=1)
                unsafe_after = ledger.get_task("recover-unsafe")
            finally:
                scheduler.shutdown()

        self.assertEqual(recovered.state, TASK_SUCCEEDED)
        self.assertEqual(unsafe_after.state, TASK_RECOVERY_REQUIRED)
        self.assertEqual(calls, ["recover-safe"])

    def test_heartbeat_keeps_long_running_task_out_of_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            started = threading.Event()
            release = threading.Event()

            def handler(task, _cancel_event) -> AgentResult:
                started.set()
                release.wait(timeout=2)
                return AgentResult(task.task_id, {"done": True})

            scheduler = self._scheduler(ledger, handler, max_concurrency=1)
            try:
                scheduler.enqueue(self._spec("heartbeat-task", max_attempts=2))
                self.assertTrue(started.wait(timeout=1))
                time.sleep(0.35)
                expired = ledger.mark_expired_leases_for_recovery()
                release.set()
                completed = scheduler.wait_for_terminal("heartbeat-task", timeout_seconds=1)
            finally:
                release.set()
                scheduler.shutdown()

        self.assertEqual(expired, [])
        self.assertEqual(completed.state, TASK_SUCCEEDED)

    def test_shutdown_timeout_keeps_cancellation_sticky_after_lease_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            started = threading.Event()
            release = threading.Event()
            finished = threading.Event()

            def handler(task, _cancel_event) -> AgentResult:
                started.set()
                release.wait(timeout=2)
                finished.set()
                return AgentResult(task.task_id, {"done": True})

            scheduler = self._scheduler(ledger, handler, max_concurrency=1)
            scheduler.enqueue(self._spec("shutdown-task", max_attempts=2))
            self.assertTrue(started.wait(timeout=1))
            scheduler.shutdown(grace_seconds=0.03)
            after_shutdown = ledger.get_task("shutdown-task")
            recovered = ledger.mark_expired_leases_for_recovery(now=time.time() + 1)
            release.set()
            self.assertTrue(finished.wait(timeout=1))

        self.assertEqual(after_shutdown.state, "cancel_requested")
        self.assertEqual([task.state for task in recovered], [TASK_CANCELLED])

    def test_shutdown_collects_done_future_before_requesting_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            returned = threading.Event()

            def handler(task, _cancel_event) -> AgentResult:
                returned.set()
                return AgentResult(task.task_id, {"completed": True})

            scheduler = self._scheduler(ledger, handler, max_concurrency=1)
            ledger.create_task(self._spec("done-before-shutdown"))
            scheduler.run_once()
            self.assertTrue(returned.wait(timeout=1))
            before = ledger.get_task("done-before-shutdown")
            scheduler.shutdown(grace_seconds=0.1)
            after = ledger.get_task("done-before-shutdown")

        self.assertEqual(before.state, "running")
        self.assertEqual(after.state, TASK_SUCCEEDED)
        self.assertEqual(after.output_snapshot, {"completed": True})

    def test_shutdown_rechecks_future_that_finishes_before_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            started = threading.Event()
            release = threading.Event()

            def handler(task, _cancel_event) -> AgentResult:
                started.set()
                release.wait(timeout=2)
                return AgentResult(task.task_id, {"completed": "during-shutdown"})

            scheduler = self._scheduler(ledger, handler, max_concurrency=1)
            scheduler.enqueue(self._spec("done-before-cancel"))
            self.assertTrue(started.wait(timeout=1))
            original_cancel = scheduler.cancel

            def cancel_after_handler_returns(task_id: str):
                with scheduler._state_lock:
                    future = scheduler._active[task_id].future
                release.set()
                future.result(timeout=1)
                return original_cancel(task_id)

            scheduler.cancel = cancel_after_handler_returns
            scheduler.shutdown(grace_seconds=0.1)
            after = ledger.get_task("done-before-cancel")

        self.assertEqual(after.state, TASK_SUCCEEDED)
        self.assertEqual(after.output_snapshot, {"completed": "during-shutdown"})

    def test_cancelled_replay_safe_task_is_not_revived_on_startup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            created = ledger.create_task(self._spec("cancelled-recovery", max_attempts=2))
            claimed = ledger.claim_task(
                created.task_id,
                "old-worker",
                created.version,
                lease_seconds=1,
                now=1000,
            )
            requested = ledger.request_cancel(claimed.task_id, now=1000.5)
            expired = ledger.mark_expired_leases_for_recovery(now=1002)
            calls: list[str] = []

            def handler(task, _cancel_event) -> AgentResult:
                calls.append(task.task_id)
                return AgentResult(task.task_id, {"unexpected": True})

            scheduler = self._scheduler(ledger, handler)
            try:
                scheduler.start()
                time.sleep(0.05)
                final = ledger.get_task(created.task_id)
            finally:
                scheduler.shutdown()

        self.assertEqual(requested.state, "cancel_requested")
        self.assertEqual([task.state for task in expired], [TASK_CANCELLED])
        self.assertEqual(final.state, TASK_CANCELLED)
        self.assertEqual(calls, [])

    def test_active_task_id_is_excluded_from_recovery_reclaim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            started = threading.Event()
            release = threading.Event()
            starts: list[str] = []

            def handler(task, _cancel_event) -> AgentResult:
                starts.append(task.task_id)
                started.set()
                release.wait(timeout=2)
                return AgentResult(task.task_id, {"done": True})

            scheduler = self._scheduler(ledger, handler, max_concurrency=2)
            ledger.create_task(self._spec("active-recovery", max_attempts=2))
            scheduler.run_once()
            self.assertTrue(started.wait(timeout=1))
            ledger.mark_expired_leases_for_recovery(now=time.time() + 10)
            scheduler.run_once()
            during = ledger.get_task("active-recovery")
            active_count = scheduler.active_count
            release.set()
            self.assertTrue(
                self._wait_until(
                    lambda: scheduler.run_once(allow_dispatch=False)["completed"] == 1
                )
            )
            scheduler.shutdown()

        self.assertEqual(during.state, "queued")
        self.assertEqual(during.attempt, 1)
        self.assertEqual(active_count, 1)
        self.assertEqual(starts, ["active-recovery"])

    def test_shutdown_waits_for_in_progress_dispatch_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            scheduler = self._scheduler(
                ledger,
                lambda task, _cancel: AgentResult(task.task_id, {"done": True}),
            )
            ledger.create_task(self._spec("shutdown-race"))
            dispatch_entered = threading.Event()
            release_dispatch = threading.Event()
            shutdown_done = threading.Event()

            def blocked_dispatch() -> int:
                dispatch_entered.set()
                release_dispatch.wait(timeout=2)
                return 0

            scheduler._dispatch_ready = blocked_dispatch
            scheduler.start()
            self.assertTrue(dispatch_entered.wait(timeout=1))
            shutdown_thread = threading.Thread(
                target=lambda: (scheduler.shutdown(0.1), shutdown_done.set())
            )
            shutdown_thread.start()
            self.assertFalse(shutdown_done.wait(timeout=0.05))
            release_dispatch.set()
            self.assertTrue(shutdown_done.wait(timeout=1))
            shutdown_thread.join(timeout=1)
            task = ledger.get_task("shutdown-race")

        self.assertEqual(task.state, "queued")

    def test_shutdown_waits_for_concurrent_enqueue_and_closes_permanently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            scheduler = self._scheduler(
                ledger,
                lambda task, _cancel: AgentResult(task.task_id, {"done": True}),
            )
            create_entered = threading.Event()
            release_create = threading.Event()
            shutdown_done = threading.Event()
            enqueue_errors: list[BaseException] = []
            original_create = ledger.create_task_with_dependency

            def blocked_create(spec, dependency_task_id):
                create_entered.set()
                release_create.wait(timeout=2)
                return original_create(spec, dependency_task_id)

            ledger.create_task_with_dependency = blocked_create
            enqueue_thread = threading.Thread(
                target=lambda: self._capture_error(
                    enqueue_errors,
                    lambda: scheduler.enqueue(self._spec("enqueue-race")),
                )
            )
            enqueue_thread.start()
            self.assertTrue(create_entered.wait(timeout=1))
            shutdown_thread = threading.Thread(
                target=lambda: (scheduler.shutdown(0.1), shutdown_done.set())
            )
            shutdown_thread.start()
            self.assertFalse(shutdown_done.wait(timeout=0.05))
            release_create.set()
            enqueue_thread.join(timeout=1)
            shutdown_thread.join(timeout=1)

            self.assertTrue(shutdown_done.is_set())
            self.assertEqual(enqueue_errors, [])
            with self.assertRaisesRegex(RuntimeError, "shutting down"):
                scheduler.enqueue(self._spec("enqueue-after-shutdown"))

    def test_heartbeat_interval_must_be_shorter_than_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            with self.assertRaisesRegex(ValueError, "shorter than lease_seconds"):
                AgentTaskScheduler(
                    ledger,
                    lease_seconds=0.1,
                    heartbeat_interval=0.1,
                )

    def test_poll_interval_must_be_shorter_than_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            with self.assertRaisesRegex(ValueError, "shorter than heartbeat_interval"):
                AgentTaskScheduler(
                    ledger,
                    lease_seconds=0.3,
                    heartbeat_interval=0.1,
                    poll_interval=0.1,
                )

    def test_failed_dependency_cancels_descendant_without_handler_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            planner = ledger.create_task(self._spec("dep-planner", task_type="plan"))
            reviewer = ledger.create_task(self._spec("dep-reviewer"))
            ledger.add_dependency(reviewer.task_id, planner.task_id)
            ledger.transition_task(
                AgentTaskTransition(
                    planner.task_id,
                    planner.version,
                    TASK_FAILED,
                    AgentResult(planner.task_id, error_type="PlannerFailed"),
                )
            )
            calls: list[str] = []

            def handler(task, _cancel_event) -> AgentResult:
                calls.append(task.task_id)
                return AgentResult(task.task_id, {"done": True})

            scheduler = self._scheduler(ledger, handler)
            try:
                scheduler.start()
                cancelled = scheduler.wait_for_terminal(reviewer.task_id, timeout_seconds=1)
            finally:
                scheduler.shutdown()

        self.assertEqual(cancelled.state, TASK_CANCELLED)
        self.assertEqual(calls, [])

    @staticmethod
    def _ledger(temporary_directory: str) -> AgentTaskLedger:
        return AgentTaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")

    @staticmethod
    def _spec(
        task_id: str,
        *,
        task_type: str = "safe_compute",
        max_attempts: int = 1,
    ) -> AgentTaskSpec:
        return AgentTaskSpec(
            task_id=task_id,
            generation_batch_id="gb-scheduler",
            session_id="session-scheduler",
            task_type=task_type,
            agent_role="specialist",
            max_attempts=max_attempts,
        )

    @staticmethod
    def _scheduler(
        ledger: AgentTaskLedger,
        handler,
        *,
        max_concurrency: int = 2,
    ) -> AgentTaskScheduler:
        scheduler = AgentTaskScheduler(
            ledger,
            max_concurrency=max_concurrency,
            lease_seconds=0.3,
            heartbeat_interval=0.05,
            poll_interval=0.01,
            worker_prefix="scheduler-test",
        )
        scheduler.register_handler(
            "safe_compute",
            AgentTaskHandlerSpec(handler, replay_safe=True),
        )
        return scheduler

    @staticmethod
    def _wait_until(predicate, timeout: float = 1) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return False

    @staticmethod
    def _capture_error(errors: list[BaseException], operation) -> None:
        try:
            operation()
        except BaseException as exc:
            errors.append(exc)


if __name__ == "__main__":
    unittest.main()
