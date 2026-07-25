from __future__ import annotations

import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ai8video.application import ai8video_chat_service
from ai8video.application import runtime
from ai8video.application.conversation_controller import AI8VideoConversationController
from ai8video.core.models import ConversationState


class RuntimeExecutionStateTest(unittest.TestCase):
    def test_runtime_refresh_preserves_conversation_sessions(self) -> None:
        sessions = {"chat-1": object()}
        previous = SimpleNamespace(
            conversation_controller=SimpleNamespace(sessions=sessions)
        )
        replacement = SimpleNamespace(
            conversation_controller=SimpleNamespace(sessions={})
        )
        original_runtime = runtime._RUNTIME
        runtime._RUNTIME = previous
        try:
            with patch.object(runtime, "AI8VideoRuntime", return_value=replacement):
                refreshed = runtime.get_runtime(refresh=True)
        finally:
            runtime._RUNTIME = original_runtime

        self.assertIs(refreshed, replacement)
        self.assertIs(refreshed.conversation_controller.sessions, sessions)

    def test_targeted_runtime_session_reset_does_not_replace_other_sessions(self) -> None:
        controller = AI8VideoConversationController(pipeline=SimpleNamespace())
        stale_state = ConversationState(session_id="chat-stale")
        stale_state.draft.video_count = 1
        other_state = ConversationState(session_id="chat-other")
        controller.sessions = {
            "chat-stale": stale_state,
            "chat-other": other_state,
        }
        current = runtime.AI8VideoRuntime.__new__(runtime.AI8VideoRuntime)
        current.conversation_controller = controller

        self.assertTrue(current.reset_conversation_session("chat-stale"))
        self.assertIsNot(controller.sessions["chat-stale"], stale_state)
        self.assertIsNone(controller.sessions["chat-stale"].draft.video_count)
        self.assertIs(controller.sessions["chat-other"], other_state)

    def test_chat_service_refresh_retires_wrapper_and_resets_runtime_state(self) -> None:
        previous = MagicMock()
        replacement = MagicMock()
        current_runtime = MagicMock()

        with patch.dict(
            ai8video_chat_service._SESSIONS,
            {"chat-stale": previous},
            clear=True,
        ), patch.object(
            ai8video_chat_service,
            "_AI8VideoSession",
            return_value=replacement,
        ) as session_factory, patch.object(
            ai8video_chat_service,
            "get_runtime",
            return_value=current_runtime,
        ), patch.object(
            ai8video_chat_service,
            "clear_chat_snapshot",
        ) as clear_snapshot:
            refreshed = ai8video_chat_service._get_session("chat-stale", refresh=True)

        self.assertIs(refreshed, replacement)
        previous.retire.assert_called_once_with("新的基础需求已开始，上一轮后台任务已停止")
        current_runtime.reset_conversation_session.assert_called_once_with("chat-stale")
        clear_snapshot.assert_called_once_with("chat-stale")
        session_factory.assert_called_once_with(session_id="chat-stale")

    def test_error_payload_is_persisted_as_failed_execution(self) -> None:
        session = ai8video_chat_service._AI8VideoSession.__new__(
            ai8video_chat_service._AI8VideoSession
        )
        session.session_id = "session-error-payload"
        session.lock = threading.Lock()
        session.current_generation_batch_id = "gb-error-payload"
        session.latest_ai8video_payload = None
        session.latest_error = None
        display_queue = queue.Queue()
        payload = {
            "status": "failed",
            "reply": {"stage": "error", "text": "上游生成失败"},
            "error": {"type": "ProviderError", "message": "上游生成失败"},
        }

        with patch.object(
            ai8video_chat_service,
            "handle_chat_message",
            return_value=payload,
        ), patch.object(ai8video_chat_service, "record_generation_execution") as record:
            session._run_runtime_chat(
                "生成一条视频",
                display_queue,
                "gb-error-payload",
                worker_id="worker-error-payload",
            )

        call = record.call_args.kwargs
        self.assertEqual(call["execution_state"], "failed")
        self.assertIsInstance(call["error"], ai8video_chat_service._RuntimePayloadError)
        self.assertEqual(call["result_snapshot"]["stage"], "error")
        self.assertEqual(display_queue.get_nowait()["payload"], payload)

    def test_cancel_smart_split_confirmation_clears_cached_plan_payload(self) -> None:
        session = ai8video_chat_service._AI8VideoSession.__new__(
            ai8video_chat_service._AI8VideoSession
        )
        session.session_id = "session-plan-cancel"
        session.lock = threading.Lock()
        session.latest_ai8video_payload = {"reply": {"awaiting": "smart_split_confirmation"}}
        session.latest_error = RuntimeError("stale")
        session.background_delivery_pending = True
        session.background_final_payload = {"reply": {"awaiting": "smart_split_confirmation"}}
        session.background_completed_at = 1.0
        session.current_display_queue = queue.Queue()
        session.current_generation_batch_id = "gb-plan-cancel"
        session.current_message = "生成 2 条视频"
        session.current_started_at = 1.0

        with patch.object(
            ai8video_chat_service,
            "cancel_smart_split_confirmation_in_runtime",
            return_value=True,
        ) as cancel_runtime, patch.object(
            ai8video_chat_service,
            "clear_chat_snapshot",
        ) as clear_snapshot, patch.object(
            ai8video_chat_service,
            "cancel_generation_progress",
        ) as cancel_progress, patch.object(
            ai8video_chat_service,
            "record_generation_execution",
        ) as record_execution, patch.object(
            session,
            "_ensure_task_runner",
        ) as ensure_runner:
            ensure_runner.return_value.cancel.return_value = True
            cancelled = session.cancel_smart_split_confirmation()

        self.assertTrue(cancelled)
        self.assertIsNone(session.latest_ai8video_payload)
        self.assertIsNone(session.latest_error)
        self.assertFalse(session.background_delivery_pending)
        self.assertIsNone(session.background_final_payload)
        self.assertIsNone(session.current_display_queue)
        self.assertIsNone(session.current_generation_batch_id)
        self.assertIsNone(session.current_message)
        cancel_runtime.assert_called_once_with("session-plan-cancel")
        clear_snapshot.assert_called_once_with("session-plan-cancel")
        ensure_runner.return_value.cancel.assert_called_once_with("gb-plan-cancel")
        cancel_progress.assert_called_once_with("session-plan-cancel", "用户取消智能分集确认")
        record_execution.assert_called_once_with(
            session_id="session-plan-cancel",
            generation_batch_id="gb-plan-cancel",
            execution_state="cancel_requested",
            cancel_requested=True,
        )

    def test_cancel_smart_split_confirmation_is_idempotent_when_runtime_state_is_missing(self) -> None:
        session = ai8video_chat_service._AI8VideoSession.__new__(
            ai8video_chat_service._AI8VideoSession
        )
        session.session_id = "session-plan-already-reset"
        session.lock = threading.Lock()
        session.latest_ai8video_payload = {"reply": {"awaiting": "smart_split_confirmation"}}
        session.latest_error = None
        session.background_delivery_pending = False
        session.background_final_payload = None
        session.background_completed_at = None
        session.current_display_queue = queue.Queue()
        session.current_generation_batch_id = None
        session.current_message = "旧确认卡"
        session.current_started_at = 1.0

        with patch.object(
            ai8video_chat_service,
            "cancel_smart_split_confirmation_in_runtime",
            return_value=False,
        ), patch.object(
            ai8video_chat_service,
            "clear_chat_snapshot",
        ), patch.object(
            ai8video_chat_service,
            "cancel_generation_progress",
        ), patch.object(
            session,
            "_ensure_task_runner",
        ) as ensure_runner:
            ensure_runner.return_value.cancel.return_value = False
            cancelled = session.cancel_smart_split_confirmation()

        self.assertTrue(cancelled)
        self.assertIsNone(session.latest_ai8video_payload)
        self.assertIsNone(session.current_display_queue)
        self.assertIsNone(session.current_message)


if __name__ == "__main__":
    unittest.main()
