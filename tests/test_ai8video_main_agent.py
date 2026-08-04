from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ai8video.application.agent_context import (
    build_agent_state_snapshot,
    get_run_context,
    set_run_queued,
    update_run_context,
)
from ai8video.application.agent_controller import AI8VideoMainAgent
from ai8video.application.agent_journal import AgentJournal
from ai8video.application.conversation_store import ConversationStore
from ai8video.agent_runtime.action_policy import ActionPolicyGuard, AgentPolicyContext
from ai8video.agent_runtime.generation_observations import (
    aggregate_generation_observations,
    build_failed_generation_observation,
    retryable_failed_video_indexes,
)
from ai8video.agent_runtime.pi_agent_client import PiAgentDecision, PiAgentClient


class _FakePiClient:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.calls = 0

    def decide(self, *, tool_handler, **_kwargs):
        self.calls += 1
        decision = self.decisions.pop(0)
        if isinstance(decision, str):
            return PiAgentDecision(decision, None, "stop", None)
        name, arguments = decision
        tool_handler(name, arguments, f"tool-{self.calls}")
        return PiAgentDecision("", {"name": name, "arguments": arguments}, "toolUse", None)


class _FakeCompositeTools:
    def execute(self, tool_name, arguments, **_kwargs):
        if tool_name == "prepare_video_plan":
            return {
                "status": "completed",
                "request": {"raw_text": arguments["goal"], "mode": "batch_videos", "video_count": 2},
                "videos": [
                    {"index": 1, "title": "视频 1", "prompt": "方案 1"},
                    {"index": 2, "title": "视频 2", "prompt": "方案 2"},
                ],
                "videoCount": 2,
            }
        if tool_name == "review_video_plan":
            return {
                "status": "completed",
                "verdict": "accept",
                "request": {"raw_text": "生成 2 条", "mode": "batch_videos", "video_count": 2},
                "videos": [
                    {"index": 1, "title": "视频 1", "prompt": "方案 1"},
                    {"index": 2, "title": "视频 2", "prompt": "方案 2"},
                ],
                "videoCount": 2,
            }
        if tool_name == "generate_video_batch":
            return {"status": "pending", "generationBatchId": "batch-1", "videoCount": 2}
        if tool_name == "inspect_generation_result":
            return {
                "status": "completed",
                "generation": {"status": "succeeded", "successCount": 2, "failedCount": 0},
            }
        if tool_name == "archive_and_deliver":
            return {
                "status": "completed",
                "deliveryState": "complete",
                "successCount": 2,
                "failedCount": 0,
                "summary": "2 条视频已完成并归档。",
            }
        raise AssertionError(tool_name)


class AI8VideoMainAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "agent.sqlite3"
        self.store = ConversationStore(self.path)
        self.journal = AgentJournal(self.path)
        conversation = self.store.create_conversation(execution_mode="agent")
        locked = self.store.lock_for_message(
            conversation["id"],
            "生成 2 条产品视频",
            execution_mode="agent",
            expected_revision=0,
            client_message_id="message-1",
            model_binding_factory=lambda: {"configurationRevision": "test"},
        )
        self.conversation = locked["conversation"]
        self.run_id = locked["agentRun"]["id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _waiting_runtime_controller(self):
        self.journal.start_decision(self.run_id)
        action = self.journal.request_action(
            self.run_id,
            tool_name="generate_video_batch",
            idempotency_key="generation:recovery",
            input_payload={"count": 2},
            side_effects=True,
            replay_safe=True,
            cost_units=2,
        )
        self.journal.mark_action_running(action["id"])
        self.journal.wait_for_runtime(action["id"], {"generationBatchId": "batch-recovery"})
        update_run_context(
            self.path,
            self.run_id,
            {"generationBatchId": "batch-recovery", "activeActionId": action["id"]},
        )
        controller = AI8VideoMainAgent(
            self.store,
            self.journal,
            pi_client=_FakePiClient([]),
            composite_tools=_FakeCompositeTools(),
        )
        return controller, action

    def test_only_decides_at_meaningful_observations(self) -> None:
        pi = _FakePiClient([
            ("prepare_video_plan", {"goal": "生成 2 条产品视频", "videoCount": 2}),
            ("review_video_plan", {}),
            ("generate_video_batch", {"count": 2}),
            ("inspect_generation_result", {}),
            ("archive_and_deliver", {"includePartialSuccess": False}),
            "2 条视频已完成并归档。",
        ])
        controller = AI8VideoMainAgent(
            self.store,
            self.journal,
            pi_client=pi,
            composite_tools=_FakeCompositeTools(),
        )
        with patch(
            "ai8video.application.agent_controller.bound_llm_config",
            return_value={"baseUrl": "https://example.invalid/v1", "apiKey": "secret", "model": "test"},
        ):
            pending = controller.handle_message(
                conversation=self.conversation,
                run_id=self.run_id,
                message="生成 2 条产品视频",
            )

            self.assertEqual(pending["status"], "pending")
            self.assertEqual(pi.calls, 3)
            self.assertEqual(self.journal.get_run(self.run_id)["state"], "waiting_runtime")
            controller.run_status(self.run_id)
            controller.run_status(self.run_id)
            self.assertEqual(pi.calls, 3, "读取进度不能触发主 Agent 决策")

            actions = build_agent_state_snapshot(self.path, self.run_id)["actions"]
            generation_action = actions[-1]
            result = {"status": "succeeded", "successCount": 2, "failedCount": 0}
            self.journal.complete_action(generation_action["id"], result)
            self.journal.record_observation(
                self.run_id,
                action_id=generation_action["id"],
                kind="generation_terminal",
                state="succeeded",
                payload=result,
                terminal=True,
            )
            update_run_context(
                self.path,
                self.run_id,
                {"latestGenerationObservation": result, "generationStatus": "succeeded"},
            )
            set_run_queued(self.path, self.run_id)
            completed = controller.resume(self.run_id)

        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(pi.calls, 6)
        self.assertEqual(completed["reply"]["text"], "2 条视频已完成并归档。")

    def test_policy_blocks_generation_above_user_count(self) -> None:
        guard = ActionPolicyGuard()
        with self.assertRaisesRegex(Exception, "超过用户明确要求"):
            guard.authorize(
                "generate_video_batch",
                {"count": 3},
                AgentPolicyContext(requested_video_count=2),
            )

    def test_two_identical_high_level_observations_stop_the_agent_loop(self) -> None:
        self.journal.start_decision(self.run_id)
        action = self.journal.request_action(
            self.run_id,
            tool_name="prepare_video_plan",
            idempotency_key="same-observation",
            input_payload={"goal": "相同状态"},
            side_effects=False,
            replay_safe=True,
        )
        self.journal.mark_action_running(action["id"])
        self.journal.complete_action(action["id"], {"status": "completed"})
        for _index in range(2):
            self.journal.record_observation(
                self.run_id,
                action_id=action["id"],
                kind="action_terminal",
                state="completed",
                payload={"status": "completed", "result": "unchanged"},
                terminal=True,
            )
        pi = _FakePiClient([])
        controller = AI8VideoMainAgent(
            self.store,
            self.journal,
            pi_client=pi,
            composite_tools=_FakeCompositeTools(),
        )

        response = controller.resume(self.run_id)

        self.assertEqual(response["status"], "waiting_user")
        self.assertEqual(pi.calls, 0)
        self.assertEqual(self.journal.get_run(self.run_id)["noProgressCount"], 1)

    def test_terminal_runtime_ledger_is_reconciled_once_after_restart(self) -> None:
        controller, action = self._waiting_runtime_controller()
        status = {
            "readOnlyRecovery": True,
            "generationBatchId": "batch-recovery",
            "generationProgress": {
                "status": "completed_with_error",
                "totalRequested": 2,
                "succeededCount": 1,
                "failedCount": 1,
                "items": [
                    {"videoIndex": 1, "status": "succeeded", "assetRecord": {"videoIndex": 1}},
                    {"videoIndex": 2, "status": "failed", "retryable": True, "error": "审核失败"},
                ],
            },
        }

        with patch("ai8video.application.facade.get_chat_status", return_value=status):
            self.assertTrue(controller._reconcile_waiting_runtime(self.run_id))

        self.assertEqual(self.journal.get_run(self.run_id)["state"], "queued")
        actions = build_agent_state_snapshot(self.path, self.run_id)["actions"]
        self.assertEqual(actions[-1]["id"], action["id"])
        self.assertEqual(actions[-1]["state"], "succeeded")
        observations = self.journal.list_observations(self.run_id)
        self.assertEqual(observations[-1]["kind"], "generation_terminal_recovered")
        self.assertEqual(observations[-1]["payload"]["status"], "partial_success")

    def test_nonterminal_recovery_never_fakes_a_worker_resume(self) -> None:
        controller, _action = self._waiting_runtime_controller()
        status = {
            "readOnlyRecovery": True,
            "generationBatchId": "batch-recovery",
            "generationProgress": {
                "status": "running",
                "totalRequested": 2,
                "items": [{"videoIndex": 1, "status": "running"}],
            },
        }

        with patch("ai8video.application.facade.get_chat_status", return_value=status):
            self.assertFalse(controller._reconcile_waiting_runtime(self.run_id))

        run = self.journal.get_run(self.run_id)
        self.assertEqual(run["state"], "failed")
        self.assertEqual(run["error"]["code"], "agent_runtime_worker_lost")
        latest = get_run_context(self.path, self.run_id)["latestResponse"]
        self.assertEqual(latest["status"], "failed")

    def test_runtime_ledger_inspection_error_keeps_existing_wait_state(self) -> None:
        controller, _action = self._waiting_runtime_controller()

        with patch("ai8video.application.facade.get_chat_status", side_effect=RuntimeError("ledger unavailable")):
            self.assertFalse(controller._reconcile_waiting_runtime(self.run_id))

        self.assertEqual(self.journal.get_run(self.run_id)["state"], "waiting_runtime")

    def test_tail_frame_checkpoint_resumes_without_progress_driven_agent_calls(self) -> None:
        controller, _action = self._waiting_runtime_controller()
        awaiting = {
            "phase": "awaiting_tail_frame_continue",
            "readOnlyRecovery": False,
            "generationBatchId": "batch-recovery",
            "generationProgress": {"status": "awaiting_tail_frame_continue"},
        }
        with patch("ai8video.application.facade.get_chat_status", return_value=awaiting):
            self.assertFalse(controller._reconcile_waiting_runtime(self.run_id))
        self.assertEqual(self.journal.get_run(self.run_id)["state"], "waiting_user")
        controller._apply_user_message(
            self.run_id,
            get_run_context(self.path, self.run_id),
            "我已经在结果卡确认继续",
        )
        self.assertEqual(self.journal.get_run(self.run_id)["state"], "waiting_user")

        live = {
            "phase": "generating",
            "readOnlyRecovery": False,
            "generationBatchId": "batch-recovery",
            "generationProgress": {
                "status": "running",
                "totalRequested": 2,
                "items": [{"videoIndex": 1, "status": "succeeded"}, {"videoIndex": 2, "status": "running"}],
            },
        }
        with patch("ai8video.application.facade.get_chat_status", return_value=live):
            self.assertFalse(controller._reconcile_waiting_runtime(self.run_id))
        self.assertEqual(self.journal.get_run(self.run_id)["state"], "waiting_runtime")

        terminal = {
            **live,
            "phase": "completed",
            "generationProgress": {
                "status": "completed",
                "totalRequested": 2,
                "succeededCount": 2,
                "items": [
                    {"videoIndex": 1, "status": "succeeded"},
                    {"videoIndex": 2, "status": "succeeded"},
                ],
            },
        }
        with patch("ai8video.application.facade.get_chat_status", return_value=terminal):
            self.assertTrue(controller._reconcile_waiting_runtime(self.run_id))
        self.assertEqual(self.journal.get_run(self.run_id)["state"], "queued")
        self.assertIsNone(get_run_context(self.path, self.run_id)["pendingUserQuestion"])

    def test_retry_observations_replace_only_failed_video_items(self) -> None:
        first = {
            "kind": "generation_terminal",
            "terminal": True,
            "payload": {
                "status": "partial_success",
                "generationBatchId": "batch-1",
                "items": [
                    {"videoIndex": 1, "status": "succeeded", "retryable": False},
                    {"videoIndex": 2, "status": "failed", "retryable": True, "reason": "审核失败"},
                ],
                "assets": [{"videoIndex": 1, "path": "one.mp4"}],
            },
        }
        self.assertEqual(retryable_failed_video_indexes([first]), {2})
        retry = {
            "kind": "generation_terminal",
            "terminal": True,
            "payload": {
                "status": "succeeded",
                "generationBatchId": "batch-2",
                "items": [
                    {
                        "videoIndex": 2,
                        "status": "succeeded",
                        "retryable": False,
                        "assetRecord": {"videoIndex": 2, "path": "two.mp4"},
                    },
                ],
                "assets": [{"videoIndex": 2, "path": "two.mp4"}],
            },
        }

        merged = aggregate_generation_observations([first, retry])
        self.assertEqual(merged["status"], "succeeded")
        self.assertEqual(merged["successCount"], 2)
        self.assertEqual(merged["failedCount"], 0)
        self.assertEqual([item["videoIndex"] for item in merged["items"]], [1, 2])

        failed = build_failed_generation_observation(
            batch_id="batch-error",
            video_indexes=[1, 2],
            error="Runtime 异常",
        )
        self.assertEqual(failed["failedCount"], 2)
        self.assertTrue(all(item["retryable"] for item in failed["items"]))

    def test_pi_sidecar_health_handshake(self) -> None:
        client = PiAgentClient(timeout_seconds=5)
        try:
            health = client.health()
        finally:
            client.close()
        self.assertTrue(health["ok"])
        self.assertEqual(health["protocol"], 1)

    def test_pi_bridge_propagates_policy_error_after_tool_result_is_returned(self) -> None:
        client = PiAgentClient(timeout_seconds=5)
        payloads = iter([
            {
                "type": "tool_call",
                "requestId": "decision-test",
                "toolCallId": "tool-test",
                "name": "generate_video_batch",
                "arguments": {"count": 99},
            },
            {
                "type": "decision_result",
                "requestId": "decision-test",
                "text": "",
                "action": {"name": "generate_video_batch", "arguments": {"count": 99}},
            },
        ])

        with patch("ai8video.agent_runtime.pi_agent_client.uuid4", return_value=type("U", (), {"hex": "test"})()), patch.object(
            client,
            "_ensure_started",
        ), patch.object(client, "_send") as send, patch.object(
            client,
            "_read_payload",
            side_effect=lambda **_kwargs: next(payloads),
        ):
            with self.assertRaisesRegex(ValueError, "policy blocked"):
                client.decide(
                    session_id="session",
                    model_config={},
                    system_prompt="",
                    messages=[],
                    prompt="",
                    tool_handler=lambda *_args: (_ for _ in ()).throw(ValueError("policy blocked")),
                )

        self.assertTrue(any(call.args[0].get("type") == "tool_result" for call in send.call_args_list))


if __name__ == "__main__":
    unittest.main()
