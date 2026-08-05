from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ai8video.interfaces.web import app as ai8video_web


class AI8VideoModeRoutingTest(unittest.TestCase):
    @staticmethod
    def _config():
        return SimpleNamespace(
            dry_run=True,
            llm_base_url="https://example.invalid/v1",
            llm_api_key="secret",
            has_llm=lambda: True,
        )

    def test_agent_route_receives_enriched_shared_input_without_calling_standard_controller(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={
                "sessionId": "agent-session",
                "message": "完整剧本",
                "temporaryKnowledge": {"title": "临时知识", "leaves": [{"content": "知识内容"}]},
                "useDefaultKnowledgeReference": True,
                "expectedExecutionMode": "agent",
            },
        )
        ai8video_web.response = SimpleNamespace(status=200)
        main_agent = Mock()
        main_agent.handle_message.return_value = {"reply": {"text": "agent-result"}}
        locked = {
            "conversation": {"id": "agent-session", "executionMode": "agent", "modelBinding": {}},
            "agentRun": {"id": "run-agent"},
        }

        try:
            with patch.object(
                ai8video_web.AI8VideoConfig,
                "from_env",
                return_value=self._config(),
            ), patch.object(
                ai8video_web,
                "load_video_model_settings",
                return_value=SimpleNamespace(configured=lambda: False),
            ), patch.object(
                ai8video_web,
                "apply_temporary_script_knowledge",
                return_value="完整剧本\n\n共享临时知识",
            ), patch.object(
                ai8video_web,
                "lock_conversation_for_chat",
                return_value=locked,
            ) as lock_chat, patch.object(
                ai8video_web,
                "clear_generation_progress",
            ), patch.object(
                ai8video_web,
                "agent_mode_enabled",
                return_value=True,
            ), patch.object(
                ai8video_web,
                "get_main_agent",
                return_value=main_agent,
            ), patch.object(
                ai8video_web,
                "get_conversation_store",
            ), patch.object(
                ai8video_web,
                "get_agent_journal",
            ), patch.object(
                ai8video_web,
                "append_assistant_message",
                side_effect=lambda _session_id, body: body,
            ), patch.object(
                ai8video_web,
                "handle_chat_via_ai8video",
            ) as standard_controller:
                body = ai8video_web.api_chat()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertEqual(body["reply"]["text"], "agent-result")
        lock_chat.assert_called_once()
        self.assertEqual(lock_chat.call_args.args[1], "完整剧本")
        main_agent.handle_message.assert_called_once_with(
            conversation=locked["conversation"],
            run_id="run-agent",
            message="完整剧本",
            planning_input="完整剧本\n\n共享临时知识",
        )
        standard_controller.assert_not_called()

    def test_standard_route_does_not_call_main_agent(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={
                "sessionId": "workflow-session",
                "message": "生成一条视频",
                "expectedExecutionMode": "workflow",
            },
        )
        ai8video_web.response = SimpleNamespace(status=200)
        locked = {
            "conversation": {"id": "workflow-session", "executionMode": "workflow"},
            "agentRun": None,
        }

        try:
            with patch.object(
                ai8video_web.AI8VideoConfig,
                "from_env",
                return_value=self._config(),
            ), patch.object(
                ai8video_web,
                "load_video_model_settings",
                return_value=SimpleNamespace(configured=lambda: False),
            ), patch.object(
                ai8video_web,
                "lock_conversation_for_chat",
                return_value=locked,
            ), patch.object(
                ai8video_web,
                "clear_generation_progress",
            ), patch.object(
                ai8video_web,
                "handle_chat_via_ai8video",
                return_value={"reply": {"text": "workflow-result"}},
            ) as standard_controller, patch.object(
                ai8video_web,
                "get_main_agent",
            ) as main_agent, patch.object(
                ai8video_web,
                "append_assistant_message",
                side_effect=lambda _session_id, body: body,
            ):
                body = ai8video_web.api_chat()
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertEqual(body["reply"]["text"], "workflow-result")
        standard_controller.assert_called_once()
        self.assertEqual(standard_controller.call_args.kwargs["message"], "生成一条视频")
        main_agent.assert_not_called()

    def test_disabled_agent_route_fails_instead_of_falling_back_to_standard(self) -> None:
        request_backup = ai8video_web.request
        response_backup = ai8video_web.response
        ai8video_web.request = SimpleNamespace(
            method="POST",
            json={
                "sessionId": "disabled-agent-session",
                "message": "继续处理",
                "expectedExecutionMode": "agent",
            },
        )
        ai8video_web.response = SimpleNamespace(status=200)
        locked = {
            "conversation": {"id": "disabled-agent-session", "executionMode": "agent"},
            "agentRun": None,
        }

        try:
            with patch.object(
                ai8video_web.AI8VideoConfig,
                "from_env",
                return_value=self._config(),
            ), patch.object(
                ai8video_web,
                "load_video_model_settings",
                return_value=SimpleNamespace(configured=lambda: False),
            ), patch.object(
                ai8video_web,
                "lock_conversation_for_chat",
                return_value=locked,
            ), patch.object(
                ai8video_web,
                "clear_generation_progress",
            ), patch.object(
                ai8video_web,
                "agent_mode_enabled",
                return_value=False,
            ), patch.object(
                ai8video_web,
                "handle_chat_via_ai8video",
            ) as standard_controller, patch.object(
                ai8video_web,
                "get_main_agent",
            ) as main_agent:
                body = ai8video_web.api_chat()
                response_status = ai8video_web.response.status
        finally:
            ai8video_web.request = request_backup
            ai8video_web.response = response_backup

        self.assertEqual(response_status, 503)
        self.assertEqual(body["code"], "AGENT_MODE_DISABLED")
        self.assertIn("不能回退到标准模式", body["error"])
        standard_controller.assert_not_called()
        main_agent.assert_not_called()


if __name__ == "__main__":
    unittest.main()
