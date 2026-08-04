from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ai8video.application.agent_journal import AgentJournal
from ai8video.application.agent_context import set_run_waiting_user
from ai8video.application.conversation_store import ConversationStore, ConversationStoreError
from ai8video.application.conversation_store_schema import stable_payload_hash
from ai8video.application.conversation_store_schema import transaction


class ConversationStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "conversations.sqlite3"
        self.store = ConversationStore(self.path)
        self.journal = AgentJournal(self.path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_first_message_atomically_locks_agent_mode_and_model_binding(self) -> None:
        conversation = self.store.create_conversation(execution_mode="agent")

        locked = self.store.lock_for_message(
            conversation["id"],
            "生成三条产品视频",
            execution_mode="agent",
            expected_revision=conversation["revision"],
            client_message_id="client-message-1",
            model_binding_factory=lambda: {"llm": {"profileId": "profile-1", "revision": "abc"}},
        )

        self.assertTrue(locked["conversation"]["modeLocked"])
        self.assertEqual(locked["conversation"]["executionMode"], "agent")
        self.assertEqual(locked["conversation"]["messageCount"], 1)
        self.assertEqual(locked["conversation"]["title"], "生成三条产品视频")
        self.assertEqual(locked["conversation"]["modelBinding"]["llm"]["profileId"], "profile-1")
        self.assertEqual(locked["agentRun"]["state"], "queued")
        with self.assertRaises(ConversationStoreError) as raised:
            self.store.set_execution_mode(conversation["id"], "workflow")
        self.assertEqual(raised.exception.code, "conversation_busy")

    def test_client_message_id_is_idempotent(self) -> None:
        conversation = self.store.create_conversation()
        first = self.store.lock_for_message(
            conversation["id"],
            "同一条消息",
            execution_mode="workflow",
            expected_revision=0,
            client_message_id="same-client-id",
        )
        second = self.store.lock_for_message(
            conversation["id"],
            "同一条消息",
            execution_mode="workflow",
            expected_revision=first["conversation"]["revision"],
            client_message_id="same-client-id",
        )

        self.assertEqual(first["messageId"], second["messageId"])
        self.assertEqual(self.store.get_conversation(conversation["id"])["messageCount"], 1)

    def test_legacy_reconciliation_preserves_more_than_limit_and_blocks_new(self) -> None:
        legacy = [
            {"id": f"legacy-{index}", "title": f"旧对话 {index}", "messages": []}
            for index in range(4)
        ]

        first = self.store.reconcile_legacy(legacy)
        second = self.store.reconcile_legacy(legacy)

        self.assertEqual(len(first), 4)
        self.assertEqual(len(second), 4)
        self.assertTrue(all(item["legacyAdopted"] for item in second))
        with self.assertRaisesRegex(ConversationStoreError, "最多保留 3 个"):
            self.store.create_conversation()

    def test_three_conversation_limit_requires_explicit_delete_before_reuse(self) -> None:
        first = self.store.create_conversation()
        second = self.store.create_conversation()
        third = self.store.create_conversation()

        with self.assertRaises(ConversationStoreError) as raised:
            self.store.create_conversation()
        self.assertEqual(raised.exception.code, "conversation_limit_reached")
        self.assertEqual(len(self.store.list_conversations()), 3)

        self.store.delete_conversation(second["id"])
        replacement = self.store.create_conversation()
        ids = {item["id"] for item in self.store.list_conversations()}
        self.assertEqual(len(ids), 3)
        self.assertIn(first["id"], ids)
        self.assertIn(third["id"], ids)
        self.assertIn(replacement["id"], ids)
        self.assertEqual(replacement["executionMode"], "workflow")

    def test_empty_conversation_mode_switch_is_persisted(self) -> None:
        conversation = self.store.create_conversation()

        switched = self.store.set_execution_mode(
            conversation["id"],
            "agent",
            expected_revision=conversation["revision"],
        )

        self.assertEqual(switched["executionMode"], "agent")
        self.assertFalse(switched["modeLocked"])
        self.assertEqual(self.store.get_conversation(conversation["id"])["executionMode"], "agent")

    def test_reset_keeps_history_but_opens_a_new_epoch(self) -> None:
        conversation = self.store.create_conversation()
        locked = self.store.lock_for_message(
            conversation["id"],
            "第一纪元",
            execution_mode="workflow",
            expected_revision=0,
            client_message_id="epoch-0",
        )

        reset = self.store.reset_conversation(conversation["id"])

        self.assertEqual(reset["epoch"], 1)
        self.assertEqual(reset["messageCount"], 0)
        self.assertFalse(reset["modeLocked"])
        self.assertEqual(self.store.list_messages(conversation["id"]), [])
        self.assertGreater(reset["revision"], locked["conversation"]["revision"])

    def test_last_conversation_cannot_be_deleted_but_an_extra_conversation_can(self) -> None:
        first = self.store.create_conversation(title="保留对话")

        self.assertFalse(self.store.get_conversation(first["id"])["canDelete"])
        with self.assertRaisesRegex(ConversationStoreError, "最后一个对话不能删除"):
            self.store.delete_conversation(first["id"])

        second = self.store.create_conversation(title="可删除对话")
        self.assertTrue(self.store.get_conversation(first["id"])["canDelete"])
        self.store.delete_conversation(second["id"])

        items = self.store.list_conversations()
        self.assertEqual([item["id"] for item in items], [first["id"]])
        self.assertFalse(items[0]["canDelete"])

    def test_agent_feature_flag_fallback_does_not_create_a_stuck_run(self) -> None:
        conversation = self.store.create_conversation(execution_mode="agent")

        locked = self.store.lock_for_message(
            conversation["id"],
            "关闭 Agent 后仍走标准流程",
            execution_mode="agent",
            expected_revision=0,
            client_message_id="feature-flag-fallback",
            create_agent_run=False,
        )

        self.assertIsNone(locked["agentRun"])
        self.assertIsNone(locked["conversation"]["activeRunId"])
        self.assertEqual(locked["conversation"]["lifecycleState"], "idle")
        self.assertTrue(locked["conversation"]["canReset"])

    def test_legacy_assistant_payload_survives_server_adoption(self) -> None:
        payload = {
            "text": "旧版富消息",
            "stage": "completed",
            "result": {"videos": [{"index": 1, "path": "kept.mp4"}]},
        }
        self.store.reconcile_legacy([{
            "id": "legacy-rich",
            "title": "旧对话",
            "messages": [{"role": "assistant", "payload": payload}],
        }])

        messages = self.store.list_messages("legacy-rich")
        self.assertEqual(messages[0]["content"], "旧版富消息")
        self.assertEqual(messages[0]["metadata"]["legacyPayload"], payload)

    def test_busy_agent_run_blocks_reset_and_delete(self) -> None:
        conversation = self.store.create_conversation(execution_mode="agent")
        locked = self.store.lock_for_message(
            conversation["id"],
            "开始执行",
            execution_mode="agent",
            expected_revision=0,
            client_message_id="busy-1",
        )

        self.journal.start_decision(locked["agentRun"]["id"])

        with self.assertRaisesRegex(ConversationStoreError, "正在执行"):
            self.store.reset_conversation(conversation["id"])
        with self.assertRaisesRegex(ConversationStoreError, "正在执行"):
            self.store.delete_conversation(conversation["id"])

    def test_waiting_user_agent_run_still_protects_conversation(self) -> None:
        conversation = self.store.create_conversation(execution_mode="agent")
        locked = self.store.lock_for_message(
            conversation["id"],
            "等待确认",
            execution_mode="agent",
            expected_revision=0,
            client_message_id="waiting-user-1",
        )
        set_run_waiting_user(
            self.path,
            locked["agentRun"]["id"],
            code="approval_required",
            message="等待用户确认",
        )

        with self.assertRaisesRegex(ConversationStoreError, "正在执行"):
            self.store.reset_conversation(conversation["id"])
        with self.assertRaisesRegex(ConversationStoreError, "正在执行"):
            self.store.delete_conversation(conversation["id"])

    def test_agent_action_is_idempotent_and_observation_ignores_progress_noise(self) -> None:
        conversation = self.store.create_conversation(execution_mode="agent")
        locked = self.store.lock_for_message(
            conversation["id"],
            "执行视频批次",
            execution_mode="agent",
            expected_revision=0,
            client_message_id="agent-1",
        )
        run_id = locked["agentRun"]["id"]
        self.journal.start_decision(run_id)
        action = self.journal.request_action(
            run_id,
            tool_name="generate_video_batch",
            idempotency_key="video-batch:request-1",
            input_payload={"count": 3},
            side_effects=True,
            replay_safe=True,
        )
        replay = self.journal.request_action(
            run_id,
            tool_name="generate_video_batch",
            idempotency_key="video-batch:request-1",
            input_payload={"count": 3},
            side_effects=True,
            replay_safe=True,
        )
        self.journal.mark_action_running(action["id"])
        self.journal.wait_for_runtime(action["id"], {"batchId": "batch-1"})
        first = self.journal.record_observation(
            run_id,
            action_id=action["id"],
            kind="runtime",
            state="running",
            payload={"batchId": "batch-1", "status": "running", "progress": 10},
            terminal=False,
        )
        second = self.journal.record_observation(
            run_id,
            action_id=action["id"],
            kind="runtime",
            state="running",
            payload={"batchId": "batch-1", "status": "running", "progress": 90},
            terminal=False,
        )

        self.assertEqual(action["id"], replay["id"])
        self.assertEqual(first["progressHash"], second["progressHash"])
        self.assertEqual(self.journal.get_run(run_id)["noProgressCount"], 1)
        self.assertEqual(
            stable_payload_hash({"status": "running", "percent": 1}),
            stable_payload_hash({"status": "running", "percent": 99}),
        )

    def test_non_replay_safe_side_effect_does_not_retry(self) -> None:
        conversation = self.store.create_conversation(execution_mode="agent")
        locked = self.store.lock_for_message(
            conversation["id"],
            "发布视频",
            execution_mode="agent",
            expected_revision=0,
            client_message_id="publish-1",
        )
        run_id = locked["agentRun"]["id"]
        self.journal.start_decision(run_id)
        action = self.journal.request_action(
            run_id,
            tool_name="publish_video",
            idempotency_key="publish:1",
            input_payload={"assetId": "asset-1"},
            side_effects=True,
            replay_safe=False,
            max_attempts=2,
        )

        self.assertEqual(action["maxAttempts"], 1)

    def test_paid_action_cost_is_idempotent_and_each_retry_is_charged_once(self) -> None:
        conversation = self.store.create_conversation(execution_mode="agent")
        locked = self.store.lock_for_message(
            conversation["id"],
            "生成两条视频",
            execution_mode="agent",
            expected_revision=0,
            client_message_id="paid-retry-1",
        )
        run_id = locked["agentRun"]["id"]
        self.journal.start_decision(run_id)
        action = self.journal.request_action(
            run_id,
            tool_name="generate_video_batch",
            idempotency_key="paid-video-batch:1",
            input_payload={"count": 2},
            side_effects=True,
            replay_safe=True,
            cost_units=2,
        )

        self.journal.charge_action_cost(action["id"])
        self.journal.charge_action_cost(action["id"])
        self.assertEqual(self.journal.get_run(run_id)["costUnits"], 2)
        self.journal.mark_action_running(action["id"])
        self.journal.fail_action(
            action["id"],
            error_code="provider_failed",
            error_message="首次失败",
        )
        waiting = self.journal.schedule_retry(action["id"], requires_approval=True)
        self.assertEqual(waiting["state"], "waiting_approval")
        self.journal.approve_action(action["id"], approved=True)
        self.journal.charge_action_cost(action["id"])
        self.journal.charge_action_cost(action["id"])
        self.assertEqual(self.journal.get_run(run_id)["costUnits"], 4)
        self.journal.mark_action_running(action["id"])
        self.journal.fail_action(
            action["id"],
            error_code="provider_failed_again",
            error_message="第二次失败",
        )

        exhausted = self.journal.schedule_retry(action["id"], requires_approval=True)
        self.assertEqual(exhausted["state"], "failed")
        self.assertEqual(exhausted["attempt"], 2)

    def test_decision_limit_failure_is_committed(self) -> None:
        conversation = self.store.create_conversation(execution_mode="agent")
        locked = self.store.lock_for_message(
            conversation["id"],
            "测试决策上限",
            execution_mode="agent",
            expected_revision=0,
            client_message_id="limit-1",
        )
        run_id = locked["agentRun"]["id"]
        with transaction(self.path) as connection:
            connection.execute("UPDATE agent_runs SET max_decisions = 1 WHERE run_id = ?", (run_id,))
        self.journal.start_decision(run_id)

        with self.assertRaisesRegex(ConversationStoreError, "最大决策次数"):
            self.journal.start_decision(run_id)

        run = self.journal.get_run(run_id)
        self.assertEqual(run["state"], "failed")
        self.assertEqual(run["error"]["code"], "agent_decision_limit")


if __name__ == "__main__":
    unittest.main()
