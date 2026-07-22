from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from ai8video.interfaces import cli as cli_module
from ai8video.application.ai8video_chat_service import (
    handle_chat_via_ai8video,
)
from ai8video.application.runtime import get_runtime, handle_chat_message


class AI8VideoAI8VideoBridgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.env_backup = {key: os.environ.get(key) for key in self._env_keys()}
        os.environ["AI8VIDEO_DRY_RUN"] = "1"
        os.environ["AI8VIDEO_ASSET_STORE_PATH"] = os.path.join(self.tempdir.name, "assets.jsonl")
        os.environ["AI8VIDEO_ARCHIVE_LOCAL_DIR"] = os.path.join(self.tempdir.name, "archive")
        self.default_script_reference_patcher = patch(
            "ai8video.knowledge.default_script_reference.load_default_script_reference",
            return_value=None,
        )
        self.ai_interpreter_patcher = patch(
            "ai8video.application.conversation_controller.AI8VideoConversationController._interpret_request_with_ai",
            return_value=None,
        )
        self.default_script_reference_patcher.start()
        self.ai_interpreter_patcher.start()
        get_runtime(refresh=True)

    def tearDown(self) -> None:
        self.ai_interpreter_patcher.stop()
        self.default_script_reference_patcher.stop()
        for key, value in self.env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_runtime(refresh=True)
        self.tempdir.cleanup()

    @staticmethod
    def _env_keys() -> list[str]:
        return [
            "AI8VIDEO_DRY_RUN",
            "AI8VIDEO_ASSET_STORE_PATH",
            "AI8VIDEO_ARCHIVE_LOCAL_DIR",
        ]

    def test_runtime_chat_returns_structured_collecting_reply(self) -> None:
        payload = handle_chat_message(
            session_id="employee-a",
            message="给我一条老板在会议室开会风格的短视频提示词，10秒。",
            refresh=True,
        )

        self.assertEqual(payload["reply"]["stage"], "collecting")
        self.assertEqual(payload["reply"]["awaiting"], "reference_image")
        self.assertNotIn("result", payload)

    @patch("ai8video.application.ai8video_chat_service.handle_chat_message")
    def test_chat_service_dispatches_directly_to_runtime(self, runtime_chat) -> None:
        runtime_chat.return_value = {
            "reply": {
                "text": "已接收",
                "stage": "collecting",
                "awaiting": "reference_image",
                "draft": None,
                "meta": {},
                "result": None,
            }
        }

        payload = handle_chat_via_ai8video(
            session_id="employee-b",
            message="直接来一条职场老板风提示词。",
            refresh=True,
            timeout_seconds=5,
        )

        self.assertEqual(payload["reply"]["stage"], "collecting")
        self.assertEqual(payload["chatBackend"], "ai8video-runtime")
        call = runtime_chat.call_args.kwargs
        self.assertEqual(call["session_id"], "employee-b")
        self.assertEqual(call["message"], "直接来一条职场老板风提示词。")
        self.assertFalse(call["refresh"])

    @patch("ai8video.application.ai8video_chat_service.handle_chat_via_ai8video")
    def test_cli_chat_calls_project_runtime_without_web(self, handle_chat) -> None:
        handle_chat.return_value = {
            "reply": {"text": "已接收任务", "stage": "collecting"},
            "chatBackend": "ai8video-runtime",
        }
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = cli_module.main([
                "chat",
                "生成一条产品视频",
                "--session",
                "cli-test",
                "--timeout",
                "30",
                "--text",
            ])

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue().strip(), "已接收任务")
        handle_chat.assert_called_once_with(
            session_id="cli-test",
            message="生成一条产品视频",
            timeout_seconds=30,
        )


if __name__ == "__main__":
    unittest.main()
