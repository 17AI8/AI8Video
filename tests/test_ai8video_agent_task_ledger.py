from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import get_type_hints

from ai8video.batch.agent_task_ledger import AgentTaskLedger
from ai8video.batch.agent_task_models import (
    TASK_CANCEL_REQUESTED,
    TASK_CANCELLED,
    TASK_FAILED,
    TASK_RECOVERY_REQUIRED,
    TASK_RETRY_WAIT,
    TASK_RUNNING,
    TASK_SUCCEEDED,
    AgentResult,
    AgentTaskSpec,
    AgentTaskTransition,
)
from ai8video.batch.task_ledger import TaskLedger


class AgentTaskLedgerTest(unittest.TestCase):
    def test_public_inline_result_annotations_are_resolvable(self) -> None:
        hints = get_type_hints(AgentTaskLedger.record_inline_result)

        self.assertIs(hints["result"], AgentResult)

    def test_create_task_is_idempotent_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            first = ledger.create_task(
                self._spec(
                    task_id="planner-1",
                    task_type="video_plan",
                    agent_role="planner",
                    idempotency_key="plan:1",
                )
            )
            duplicate = ledger.create_task(
                self._spec(
                    task_id="planner-duplicate",
                    task_type="video_plan",
                    agent_role="planner",
                    idempotency_key="plan:1",
                )
            )
            events = ledger.list_events(first.task_id)

        self.assertEqual(duplicate.task_id, first.task_id)
        self.assertEqual(first.state, "queued")
        self.assertEqual(first.version, 0)
        self.assertEqual([event.event_type for event in events], ["task_created"])

    def test_dependencies_gate_readiness_and_reject_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            planner = ledger.create_task(self._spec("planner", "video_plan", "planner"))
            reviewer = ledger.create_task(self._spec("reviewer", "semantic_review", "reviewer"))

            self.assertTrue(ledger.add_dependency(reviewer.task_id, planner.task_id))
            self.assertFalse(ledger.add_dependency(reviewer.task_id, planner.task_id))
            self.assertEqual(
                [task.task_id for task in ledger.list_ready_tasks("gb-agent-test")],
                [planner.task_id],
            )
            with self.assertRaisesRegex(ValueError, "cycle"):
                ledger.add_dependency(planner.task_id, reviewer.task_id)

            claimed = ledger.claim_task(planner.task_id, "worker-planner", planner.version)
            completed = ledger.finish_claimed_task(
                planner.task_id,
                "worker-planner",
                claimed.version,
                AgentResult(planner.task_id, {"videos": 2}),
            )
            ready_after_completion = ledger.list_ready_tasks("gb-agent-test")

        self.assertEqual(completed.output_snapshot, {"videos": 2})
        self.assertEqual([task.task_id for task in ready_after_completion], [reviewer.task_id])

    def test_claim_transition_and_lease_use_cas_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            created = ledger.create_task(self._spec("planner", "video_plan", "planner"))

            claimed = ledger.claim_task(created.task_id, "worker-a", created.version)
            stale_claim = ledger.claim_task(created.task_id, "worker-b", created.version)
            stale_renewal = ledger.renew_lease(created.task_id, "worker-a", created.version)
            renewed = ledger.renew_lease(created.task_id, "worker-a", claimed.version)
            wrong_owner = ledger.finish_claimed_task(
                created.task_id,
                "worker-b",
                renewed.version,
                AgentResult(created.task_id, {"ok": True}),
            )
            with self.assertRaisesRegex(ValueError, "ownership-aware"):
                ledger.transition_task(
                    AgentTaskTransition(created.task_id, renewed.version, TASK_SUCCEEDED)
                )
            completed = ledger.finish_claimed_task(
                created.task_id,
                "worker-a",
                renewed.version,
                AgentResult(created.task_id, {"ok": True}),
            )

        self.assertEqual(claimed.state, TASK_RUNNING)
        self.assertEqual(claimed.attempt, 1)
        self.assertIsNone(stale_claim)
        self.assertIsNone(stale_renewal)
        self.assertIsNone(wrong_owner)
        self.assertEqual(completed.state, TASK_SUCCEEDED)

    def test_concurrent_claim_allows_only_one_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            created = ledger.create_task(self._spec("planner", "video_plan", "planner"))
            barrier = threading.Barrier(3)
            claimed = []

            def claim(worker_id: str) -> None:
                barrier.wait(timeout=2)
                claimed.append(ledger.claim_task(created.task_id, worker_id, created.version))

            threads = [
                threading.Thread(target=claim, args=("worker-a",)),
                threading.Thread(target=claim, args=("worker-b",)),
            ]
            for thread in threads:
                thread.start()
            barrier.wait(timeout=2)
            for thread in threads:
                thread.join(timeout=2)

        winners = [task for task in claimed if task is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0].state, TASK_RUNNING)

    def test_expired_lease_requires_explicit_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            created = ledger.create_task(self._spec("reviewer", "semantic_review", "reviewer"))
            claimed = ledger.claim_task(
                created.task_id,
                "worker-reviewer",
                created.version,
                lease_seconds=1,
            )

            recovered = ledger.mark_expired_leases_for_recovery(now=time.time() + 2)
            recovered_again = ledger.mark_expired_leases_for_recovery(now=time.time() + 3)
            events = ledger.list_events(created.task_id)

        self.assertEqual(claimed.state, TASK_RUNNING)
        self.assertEqual([task.state for task in recovered], [TASK_RECOVERY_REQUIRED])
        self.assertEqual(recovered_again, [])
        self.assertEqual(events[-1].event_type, "lease_expired")

    def test_expired_owner_cannot_renew_or_finish_before_reaper_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            created = ledger.create_task(
                self._spec("lease-fence", "safe_compute", "worker", max_attempts=2)
            )
            claimed = ledger.claim_task(
                created.task_id,
                "worker-old",
                created.version,
                lease_seconds=1,
                now=1000,
            )
            renewed = ledger.renew_lease(
                created.task_id,
                "worker-old",
                claimed.version,
                lease_seconds=1,
                now=1002,
            )
            finished = ledger.finish_claimed_task(
                created.task_id,
                "worker-old",
                claimed.version,
                AgentResult(created.task_id, {"late": True}),
                now=1002,
            )

        self.assertIsNone(renewed)
        self.assertIsNone(finished)

    def test_cancel_request_keeps_lease_until_owned_handler_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            created = ledger.create_task(self._spec("cancel-owned", "safe_compute", "worker"))
            claimed = ledger.claim_task(created.task_id, "worker-owned", created.version)
            requested = ledger.request_cancel(created.task_id)
            completed = ledger.finish_claimed_task(
                created.task_id,
                "worker-owned",
                requested.version,
                AgentResult(created.task_id, {"ignored": True}),
            )

        self.assertEqual(requested.state, TASK_CANCEL_REQUESTED)
        self.assertEqual(requested.worker_id, "worker-owned")
        self.assertEqual(requested.lease_expires_at, claimed.lease_expires_at)
        self.assertEqual(completed.state, TASK_CANCELLED)
        self.assertIsNone(completed.output_snapshot)

    def test_failed_dependency_cancels_all_blocked_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            planner = ledger.create_task(self._spec("planner-blocked", "plan", "planner"))
            reviewer = ledger.create_task(self._spec("reviewer-blocked", "review", "reviewer"))
            delivery = ledger.create_task(self._spec("delivery-blocked", "deliver", "supervisor"))
            ledger.add_dependency(reviewer.task_id, planner.task_id)
            ledger.add_dependency(delivery.task_id, reviewer.task_id)
            ledger.transition_task(
                AgentTaskTransition(
                    planner.task_id,
                    planner.version,
                    TASK_FAILED,
                    AgentResult(planner.task_id, error_type="PlannerFailed"),
                )
            )
            settled = ledger.settle_blocked_tasks()

        self.assertEqual([task.task_id for task in settled], [reviewer.task_id, delivery.task_id])
        self.assertTrue(all(task.state == TASK_CANCELLED for task in settled))

    def test_safe_recovery_requeues_and_fences_late_old_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            created = ledger.create_task(
                self._spec("recover-safe", "safe_compute", "worker", max_attempts=2)
            )
            first = ledger.claim_task(
                created.task_id,
                "worker-old",
                created.version,
                lease_seconds=1,
                now=1000,
            )
            ledger.mark_expired_leases_for_recovery(now=1002)
            requeued = ledger.requeue_replay_safe_tasks(["safe_compute"], now=1002)
            second = ledger.claim_task(
                created.task_id,
                "worker-new",
                requeued[0].version,
                lease_seconds=10,
                now=1003,
            )
            completed = ledger.finish_claimed_task(
                created.task_id,
                "worker-new",
                second.version,
                AgentResult(created.task_id, {"winner": "new"}),
                now=1004,
            )
            late = ledger.finish_claimed_task(
                created.task_id,
                "worker-old",
                first.version,
                AgentResult(created.task_id, {"winner": "old"}),
                now=1004,
            )

        self.assertEqual(first.attempt, 1)
        self.assertEqual(second.attempt, 2)
        self.assertEqual(completed.output_snapshot, {"winner": "new"})
        self.assertIsNone(late)

    def test_claim_version_fences_old_attempt_even_when_worker_id_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            created = ledger.create_task(
                self._spec("same-worker-fence", "safe_compute", "worker", max_attempts=2)
            )
            first = ledger.claim_task(
                created.task_id,
                "stable-worker-id",
                created.version,
                lease_seconds=1,
                now=1000,
            )
            ledger.mark_expired_leases_for_recovery(now=1002)
            requeued = ledger.requeue_replay_safe_tasks(["safe_compute"], now=1002)[0]
            second = ledger.claim_task(
                created.task_id,
                "stable-worker-id",
                requeued.version,
                lease_seconds=10,
                now=1003,
            )
            stale = ledger.finish_claimed_task(
                created.task_id,
                "stable-worker-id",
                first.version,
                AgentResult(created.task_id, {"attempt": "old"}),
                now=1004,
            )
            completed = ledger.finish_claimed_task(
                created.task_id,
                "stable-worker-id",
                second.version,
                AgentResult(created.task_id, {"attempt": "new"}),
                now=1004,
            )

        self.assertIsNone(stale)
        self.assertEqual(completed.output_snapshot, {"attempt": "new"})

    def test_retry_wait_respects_schedule_and_attempt_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            created = ledger.create_task(
                self._spec("researcher", "hotspot_research", "researcher", max_attempts=2)
            )
            claimed = ledger.claim_task(created.task_id, "worker-research", created.version)
            waiting = ledger.finish_claimed_task(
                created.task_id,
                "worker-research",
                claimed.version,
                AgentResult(
                    created.task_id,
                    error_type="TimeoutError",
                    error_message="temporary timeout",
                ),
                retry_delay_seconds=60,
            )

            ready = ledger.list_ready_tasks("gb-agent-test")

        self.assertEqual(waiting.state, TASK_RETRY_WAIT)
        self.assertEqual(ready, [])

    def test_successful_retry_clears_previous_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            created = ledger.create_task(
                self._spec("researcher", "hotspot_research", "researcher", max_attempts=2)
            )
            first_claim = ledger.claim_task(created.task_id, "worker-a", created.version)
            waiting = ledger.finish_claimed_task(
                created.task_id,
                "worker-a",
                first_claim.version,
                AgentResult(
                    created.task_id,
                    error_type="TimeoutError",
                    error_message="temporary timeout",
                ),
                retry_delay_seconds=0,
                now=time.time() - 1,
            )
            second_claim = ledger.claim_task(created.task_id, "worker-b", waiting.version)
            completed = ledger.finish_claimed_task(
                created.task_id,
                "worker-b",
                second_claim.version,
                AgentResult(created.task_id, {"items": 3}),
            )

        self.assertEqual(second_claim.attempt, 2)
        self.assertEqual(completed.output_snapshot, {"items": 3})
        self.assertIsNone(completed.error_type)
        self.assertIsNone(completed.error_message)

    def test_inline_result_commits_task_and_terminal_state_together(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            completed = ledger.record_inline_result(
                self._spec("planner-inline", "video_plan_shadow", "planner"),
                AgentResult("planner-inline", {"videoCount": 2}),
                worker_id="shadow-worker",
            )
            events = ledger.list_events("planner-inline")

        self.assertEqual(completed.state, TASK_SUCCEEDED)
        self.assertEqual(completed.attempt, 1)
        self.assertEqual(completed.worker_id, "shadow-worker")
        self.assertEqual(completed.output_snapshot, {"videoCount": 2})
        self.assertEqual(
            [event.event_type for event in events],
            ["task_created", "task_transitioned"],
        )

    def test_inline_result_with_error_cannot_be_recorded_as_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            failed = ledger.record_inline_result(
                self._spec("inline-error", "safe_compute", "worker"),
                AgentResult(
                    "inline-error",
                    error_type="InlineFailure",
                    error_message="inline task failed",
                ),
            )

        self.assertEqual(failed.state, TASK_FAILED)
        self.assertEqual(failed.error_type, "InlineFailure")

    def test_inline_result_rolls_back_new_task_for_nonterminal_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            planner = ledger.create_task(
                self._spec("planner-pending", "video_plan_shadow", "planner")
            )

            with self.assertRaisesRegex(RuntimeError, "dependency is not terminal"):
                ledger.record_inline_result(
                    self._spec("reviewer-pending", "semantic_review_shadow", "reviewer"),
                    AgentResult("reviewer-pending", {"passesCount": 1}),
                    dependency_task_id=planner.task_id,
                )
            reviewer = ledger.get_task("reviewer-pending")

        self.assertIsNone(reviewer)

    def test_inline_result_cancels_when_dependency_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            ledger = self._ledger(temporary_directory)
            planner = ledger.create_task(
                self._spec("planner-failed", "video_plan_shadow", "planner")
            )
            failed = ledger.transition_task(
                AgentTaskTransition(
                    task_id=planner.task_id,
                    expected_version=planner.version,
                    target_state=TASK_FAILED,
                    result=AgentResult(
                        planner.task_id,
                        error_type="PlannerFailed",
                        error_message="planner failed",
                    ),
                )
            )
            reviewer = ledger.record_inline_result(
                self._spec("reviewer-cancelled", "semantic_review_shadow", "reviewer"),
                AgentResult("reviewer-cancelled", {"passesCount": 1}),
                dependency_task_id=failed.task_id,
            )

        self.assertEqual(reviewer.state, TASK_CANCELLED)
        self.assertEqual(reviewer.error_type, "DependencyNotSucceeded")
        self.assertIsNone(reviewer.output_snapshot)

    @staticmethod
    def _ledger(temporary_directory: str) -> AgentTaskLedger:
        return AgentTaskLedger(Path(temporary_directory) / "task_ledger.sqlite3")

    @staticmethod
    def _spec(
        task_id: str,
        task_type: str,
        agent_role: str,
        idempotency_key: str | None = None,
        max_attempts: int = 1,
    ) -> AgentTaskSpec:
        return AgentTaskSpec(
            task_id=task_id,
            generation_batch_id="gb-agent-test",
            session_id="session-agent-test",
            task_type=task_type,
            agent_role=agent_role,
            idempotency_key=idempotency_key,
            max_attempts=max_attempts,
        )


if __name__ == "__main__":
    unittest.main()
