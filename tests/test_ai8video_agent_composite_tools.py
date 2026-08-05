from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ai8video.application.agent_context import get_run_context, update_run_context
from ai8video.application.agent_journal import AgentJournal
from ai8video.application.conversation_store import ConversationStore
from ai8video.agent_runtime.composite_tools import AgentCompositeTools
from ai8video.core.models import VideoPrompt


class _PlanningPipeline:
    def __init__(self, planned_count: int) -> None:
        self.planned_count = planned_count
        self.request = None
        self.plan_kwargs = None
        self.script_query_llm = None
        self.script_rerank_llm = None

    def plan_request(self, request, **kwargs):
        self.request = request
        self.plan_kwargs = kwargs
        return [
            VideoPrompt(index=index, title=f"视频 {index}", prompt=f"方案 {index}")
            for index in range(1, self.planned_count + 1)
        ]


class AgentCompositeToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "agent.sqlite3"
        self.store = ConversationStore(self.path)
        self.journal = AgentJournal(self.path)
        conversation = self.store.create_conversation(execution_mode="agent")
        locked = self.store.lock_for_message(
            conversation["id"],
            "开始生成",
            execution_mode="agent",
            expected_revision=0,
            client_message_id="agent-composite-message",
            model_binding_factory=lambda: {"configurationRevision": "test"},
        )
        self.conversation = locked["conversation"]
        self.run_id = locked["agentRun"]["id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_manual_mode_uses_shared_toolbar_settings_without_standard_intent_parser(self) -> None:
        pipeline = _PlanningPipeline(planned_count=4)
        update_run_context(
            self.path,
            self.run_id,
            {"planningInput": "完整产品剧本"},
        )
        tools = AgentCompositeTools(
            self.journal,
            pipeline_factory=lambda _binding: pipeline,
        )

        def apply_selected_knowledge(text, context, **_kwargs):
            return f"{text}\n\n[已选知识库参考]", context

        with patch(
            "ai8video.agent_runtime.composite_tools.default_smart_split_enabled",
            return_value=False,
        ), patch(
            "ai8video.agent_runtime.composite_tools.default_manual_video_count",
            return_value=4,
        ), patch(
            "ai8video.agent_runtime.composite_tools.default_concurrent_generation_enabled",
            return_value=True,
        ), patch(
            "ai8video.agent_runtime.composite_tools.default_tail_frame_chaining_enabled",
            return_value=True,
        ), patch(
            "ai8video.agent_runtime.composite_tools.default_tail_frame_chaining_mode",
            return_value="manual",
        ), patch(
            "ai8video.agent_runtime.composite_tools.default_html_motion_overlay_enabled",
            return_value=True,
        ), patch(
            "ai8video.agent_runtime.composite_tools.default_reference_image_path",
            return_value="/tmp/shared-reference.png",
        ), patch(
            "ai8video.agent_runtime.composite_tools.default_reference_image_custom_prompt",
            return_value="保持人物身份",
        ), patch(
            "ai8video.agent_runtime.composite_tools.enabled_default_reference_image_options",
            return_value={"autoChangeBackground": True},
        ), patch(
            "ai8video.agent_runtime.composite_tools.apply_default_script_reference",
            side_effect=apply_selected_knowledge,
        ), patch(
            "ai8video.agent_runtime.composite_tools.load_default_script_reference",
            return_value={"name": "共享知识"},
        ), patch(
            "ai8video.agent_runtime.composite_tools.read_business_prompt",
            return_value="共享系统提示词",
        ), patch(
            "ai8video.agent_runtime.composite_tools.background_music_track_status",
            return_value={"enabled": True},
        ), patch(
            "ai8video.agent_runtime.composite_tools.video_text_overlay_status",
            return_value={"enabled": True},
        ):
            result = tools.execute(
                "prepare_video_plan",
                {
                    "goal": "生成产品视频",
                    "videoCount": 9,
                    "useReferenceImage": False,
                    "referenceImage": "/tmp/agent-override.png",
                },
                run_id=self.run_id,
                action_id="prepare-action",
                conversation=self.conversation,
            )

        request = pipeline.request
        self.assertIsNotNone(request)
        self.assertEqual(request.video_count, 4)
        self.assertEqual(request.mode, "batch_videos")
        self.assertIn("完整产品剧本", request.raw_text)
        self.assertIn("Agent 对本轮任务的理解", request.raw_text)
        self.assertIn("[已选知识库参考]", request.raw_text)
        self.assertEqual(request.reference_image, "/tmp/shared-reference.png")
        self.assertEqual(request.reference_image_custom_prompt, "保持人物身份")
        self.assertTrue(request.concurrent_generation)
        self.assertFalse(request.tail_frame_chaining)
        self.assertTrue(request.html_motion_overlay_enabled)
        self.assertFalse(pipeline.plan_kwargs["smart_split"])
        self.assertFalse(pipeline.plan_kwargs["smart_split_count_locked"])
        self.assertEqual(result["videoCount"], 4)
        self.assertEqual(result["sharedSettings"]["splitMode"], "manual")
        self.assertTrue(result["sharedSettings"]["systemPromptEnabled"])
        self.assertTrue(result["sharedSettings"]["backgroundMusicEnabled"])
        self.assertTrue(result["sharedSettings"]["flowerTextEnabled"])
        self.assertEqual(get_run_context(self.path, self.run_id)["plannedVideoCount"], 4)

    def test_smart_split_without_agent_count_uses_planner_result_instead_of_defaulting_to_one(self) -> None:
        pipeline = _PlanningPipeline(planned_count=3)
        update_run_context(
            self.path,
            self.run_id,
            {"planningInput": "按完整内容自主分集"},
        )
        tools = AgentCompositeTools(
            self.journal,
            pipeline_factory=lambda _binding: pipeline,
        )

        with patch(
            "ai8video.agent_runtime.composite_tools.default_smart_split_enabled",
            return_value=True,
        ), patch(
            "ai8video.agent_runtime.composite_tools.default_manual_video_count",
            return_value=7,
        ), patch(
            "ai8video.agent_runtime.composite_tools.default_concurrent_generation_enabled",
            return_value=False,
        ), patch(
            "ai8video.agent_runtime.composite_tools.default_tail_frame_chaining_enabled",
            return_value=False,
        ), patch(
            "ai8video.agent_runtime.composite_tools.default_tail_frame_chaining_mode",
            return_value="auto",
        ), patch(
            "ai8video.agent_runtime.composite_tools.default_smart_split_confirmation_enabled",
            return_value=False,
        ), patch(
            "ai8video.agent_runtime.composite_tools.default_html_motion_overlay_enabled",
            return_value=False,
        ), patch(
            "ai8video.agent_runtime.composite_tools.default_reference_image_path",
            return_value=None,
        ), patch(
            "ai8video.agent_runtime.composite_tools.apply_default_script_reference",
            side_effect=lambda text, context, **_kwargs: (text, context),
        ), patch(
            "ai8video.agent_runtime.composite_tools.load_default_script_reference",
            return_value=None,
        ), patch(
            "ai8video.agent_runtime.composite_tools.read_business_prompt",
            return_value="",
        ), patch(
            "ai8video.agent_runtime.composite_tools.background_music_track_status",
            return_value={"enabled": False},
        ), patch(
            "ai8video.agent_runtime.composite_tools.video_text_overlay_status",
            return_value={"enabled": False},
        ):
            result = tools.execute(
                "prepare_video_plan",
                {"goal": "理解素材并完成视频规划"},
                run_id=self.run_id,
                action_id="prepare-action",
                conversation=self.conversation,
            )

        self.assertIsNone(pipeline.request.video_count)
        self.assertEqual(pipeline.request.mode, "batch_videos")
        self.assertTrue(pipeline.plan_kwargs["smart_split"])
        self.assertFalse(pipeline.plan_kwargs["smart_split_count_locked"])
        self.assertEqual(result["videoCount"], 3)
        self.assertEqual(result["sharedSettings"]["splitMode"], "smart")
        self.assertEqual(get_run_context(self.path, self.run_id)["plannedVideoCount"], 3)


if __name__ == "__main__":
    unittest.main()
