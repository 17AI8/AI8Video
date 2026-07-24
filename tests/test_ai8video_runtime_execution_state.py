from __future__ import annotations

import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ai8video.application import ai8video_chat_service
from ai8video.application import runtime


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


if __name__ == "__main__":
    unittest.main()
