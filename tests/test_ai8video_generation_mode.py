from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai8video.generation import generation_mode
from ai8video.application.conversation_controller import AI8VideoConversationController
from ai8video.core.models import VideoPrompt, ParsedRequest, PipelineResult, QuickVideoJob
from ai8video.generation.tail_frame_chaining import (
    TAIL_FRAME_CHAIN_PROMPT_SUFFIX,
    append_tail_frame_chain_prompt,
)


class AI8VideoGenerationModeTest(unittest.TestCase):
    def test_generation_mode_defaults_to_normal_and_saves_concurrent(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            settings_path = Path(tempdir) / "生成模式" / "settings.json"
            with patch.object(generation_mode, "GENERATION_MODE_DIR", settings_path.parent), \
                    patch.object(generation_mode, "GENERATION_MODE_SETTINGS_PATH", settings_path):
                self.assertFalse(generation_mode.default_concurrent_generation_enabled())

                status = generation_mode.update_generation_mode(concurrent_generation=True)

                self.assertTrue(status["ok"])
                self.assertTrue(status["concurrentGeneration"])
                self.assertTrue(generation_mode.default_concurrent_generation_enabled())

                status = generation_mode.update_generation_mode(
                    concurrent_generation=True,
                    smart_split=True,
                    confirm_smart_split=True,
                )
                self.assertTrue(status["smartSplit"])
                self.assertTrue(status["confirmSmartSplit"])

                status = generation_mode.update_generation_mode(
                    concurrent_generation=True,
                    smart_split=True,
                    tail_frame_chaining=True,
                )
                self.assertTrue(status["tailFrameChaining"])
                self.assertFalse(status["concurrentGeneration"])
                self.assertTrue(generation_mode.default_tail_frame_chaining_enabled())

    def test_tail_frame_chain_prompt_requires_subject_facing_camera(self) -> None:
        video = VideoPrompt(index=1, title="第一条", prompt="主体走进仓库。")

        updated = append_tail_frame_chain_prompt(video)

        self.assertIn(TAIL_FRAME_CHAIN_PROMPT_SUFFIX, updated.prompt)
        self.assertEqual(updated.prompt.count(TAIL_FRAME_CHAIN_PROMPT_SUFFIX), 1)

    def test_conversation_controller_uses_default_concurrent_mode_when_user_does_not_choose(self) -> None:
        captured: dict[str, ParsedRequest] = {}

        class FakePipeline:
            def run_request(self, request: ParsedRequest, *, progress_session_id: str | None = None) -> PipelineResult:
                captured["request"] = request
                return PipelineResult(
                    request=request,
                    videos=[VideoPrompt(index=1, title="第 1 条", prompt=request.raw_text)],
                    first_frame=None,
                    jobs=[QuickVideoJob(video_index=1, job_id="dry-1", status="succeeded")],
                    dry_run=True,
                )

        agent = AI8VideoConversationController(FakePipeline(), merge_mode_loader=lambda: "normal")  # type: ignore[arg-type]
        message = (
            "根据这个剧本生成 2 个 10s 短视频，老板商务风。"
            "核心主题：私域资产。参考图：/tmp/612.png"
        )
        with patch("ai8video.application.conversation_controller.default_concurrent_generation_enabled", return_value=True), \
                patch("ai8video.application.conversation_controller.default_smart_split_enabled", return_value=False):
            reply = agent.handle_message("generation-default-concurrent", message)

        self.assertEqual(reply.stage, "completed")
        self.assertTrue(captured["request"].concurrent_generation)

    def test_conversation_controller_explicit_normal_mode_overrides_default_concurrent_mode(self) -> None:
        captured: dict[str, ParsedRequest] = {}

        class FakePipeline:
            def run_request(self, request: ParsedRequest, *, progress_session_id: str | None = None) -> PipelineResult:
                captured["request"] = request
                return PipelineResult(
                    request=request,
                    videos=[VideoPrompt(index=1, title="第 1 条", prompt=request.raw_text)],
                    first_frame=None,
                    jobs=[QuickVideoJob(video_index=1, job_id="dry-1", status="succeeded")],
                    dry_run=True,
                )

        agent = AI8VideoConversationController(FakePipeline(), merge_mode_loader=lambda: "normal")  # type: ignore[arg-type]
        message = (
            "根据这个剧本生成 2 个 10s 短视频，老板商务风。"
            "核心主题：私域资产。参考图：/tmp/612.png，普通模式"
        )
        with patch("ai8video.application.conversation_controller.default_concurrent_generation_enabled", return_value=True), \
                patch("ai8video.application.conversation_controller.default_smart_split_enabled", return_value=False):
            reply = agent.handle_message("generation-explicit-normal", message)

        self.assertEqual(reply.stage, "completed")
        self.assertFalse(captured["request"].concurrent_generation)

    def test_smart_split_waits_for_confirmation_then_runs_planned_videos(self) -> None:
        captured: dict[str, object] = {}

        class FakePipeline:
            def plan_request(self, request, **kwargs):
                captured["planned_request"] = request
                captured["smart_split"] = kwargs.get("smart_split")
                return [
                    VideoPrompt(index=1, title="风险篇", prompt="提示词一", source_summary="素材前半段"),
                    VideoPrompt(index=2, title="运营篇", prompt="提示词二", source_summary="素材后半段"),
                ]

            def run_planned_request(self, request, videos, **kwargs):
                captured["videos"] = videos
                return PipelineResult(
                    request=request,
                    videos=videos,
                    first_frame=None,
                    jobs=[QuickVideoJob(video_index=1, job_id="dry-1", status="succeeded")],
                    dry_run=True,
                )

        agent = AI8VideoConversationController(FakePipeline(), merge_mode_loader=lambda: "normal")  # type: ignore[arg-type]
        message = "根据这篇完整素材智能规划短视频。核心主题：跨境运营。参考图：/tmp/ref.png"
        with patch("ai8video.application.conversation_controller.default_smart_split_enabled", return_value=True), \
                patch("ai8video.application.conversation_controller.default_smart_split_confirmation_enabled", return_value=True):
            planned = agent.handle_message("smart-split", message)
            self.assertEqual(planned.awaiting, "smart_split_confirmation")
            self.assertIn("风险篇", planned.text)
            self.assertNotIn("videos", captured)

            completed = agent.handle_message("smart-split", "确认分集")

        self.assertEqual(completed.stage, "completed")
        self.assertTrue(captured["smart_split"])
        self.assertEqual(len(captured["videos"]), 2)


if __name__ == "__main__":
    unittest.main()
