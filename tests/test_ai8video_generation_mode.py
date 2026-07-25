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
                patch("ai8video.application.conversation_controller.default_smart_split_enabled", return_value=False), \
                patch("ai8video.application.conversation_controller.default_tail_frame_chaining_enabled", return_value=False):
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

    def test_manual_video_count_ignores_text_and_generates_directly(self) -> None:
        planned_counts: list[int | None] = []
        smart_split_flags: list[bool] = []
        generated_videos: list[list[VideoPrompt]] = []

        class FakePipeline:
            def plan_request(self, request, **kwargs):
                planned_counts.append(request.video_count)
                smart_split_flags.append(bool(kwargs.get("smart_split")))
                count = int(request.video_count or 0)
                return [
                    VideoPrompt(
                        index=index,
                        title=f"主题 {index}",
                        prompt=f"提示词 {index}",
                        source_summary=f"摘要 {index}",
                    )
                    for index in range(1, count + 1)
                ]

            def run_planned_request(self, request, videos, **kwargs):
                generated_videos.append(list(videos))
                return PipelineResult(
                    request=request,
                    videos=videos,
                    first_frame=None,
                    jobs=[QuickVideoJob(video_index=1, job_id="dry-1", status="succeeded")],
                    dry_run=True,
                )

        agent = AI8VideoConversationController(FakePipeline(), merge_mode_loader=lambda: "normal")  # type: ignore[arg-type]
        message = (
            "根据这个剧本生成 2 个 10s 短视频，老板商务风。"
            "核心主题：私域资产。参考图：/tmp/612.png"
        )
        with patch("ai8video.application.conversation_controller.default_smart_split_enabled", return_value=False), \
                patch("ai8video.application.conversation_controller.default_manual_video_count", return_value=4), \
                patch("ai8video.application.conversation_controller.default_smart_split_confirmation_enabled", return_value=False):
            completed = agent.handle_message("explicit-count-confirmation", message)

        self.assertEqual(planned_counts, [4])
        self.assertEqual(smart_split_flags, [False])
        self.assertEqual(len(generated_videos[-1]), 4)
        self.assertEqual(completed.stage, "completed")
        self.assertFalse(completed.draft.tail_frame_chaining)

    def test_text_cannot_change_manual_video_count(self) -> None:
        planned_counts: list[int | None] = []
        smart_split_flags: list[bool] = []

        class FakePipeline:
            def plan_request(self, request, **kwargs):
                planned_counts.append(request.video_count)
                smart_split_flags.append(bool(kwargs.get("smart_split")))
                count = int(request.video_count or 0)
                return [
                    VideoPrompt(index=index, title=f"主题 {index}", prompt=f"提示词 {index}")
                    for index in range(1, count + 1)
                ]

            def run_planned_request(self, request, videos, **kwargs):
                return PipelineResult(
                    request=request,
                    videos=videos,
                    first_frame=None,
                    jobs=[],
                    dry_run=True,
                )

        agent = AI8VideoConversationController(FakePipeline(), merge_mode_loader=lambda: "normal")  # type: ignore[arg-type]
        message = "根据这段完整文案生成 2 条短视频。核心主题：跨境运营。参考图：/tmp/ref.png"
        with patch("ai8video.application.conversation_controller.default_smart_split_enabled", return_value=False), \
                patch("ai8video.application.conversation_controller.default_manual_video_count", return_value=2):
            completed = agent.handle_message("explicit-count-replan", message)

        self.assertEqual(completed.stage, "completed")
        self.assertEqual(planned_counts, [2])
        self.assertEqual(smart_split_flags, [False])
        self.assertEqual(len(agent.sessions["explicit-count-replan"].planned_videos), 2)

    def test_cancel_smart_split_confirmation_resets_pending_plan(self) -> None:
        class FakePipeline:
            def plan_request(self, request, **kwargs):
                return [
                    VideoPrompt(index=index, title=f"主题 {index}", prompt=f"提示词 {index}")
                    for index in range(1, 3)
                ]

            def run_planned_request(self, request, videos, **kwargs):
                raise AssertionError("取消分集后不应进入视频生成")

        session_id = "smart-split-cancel"
        agent = AI8VideoConversationController(FakePipeline(), merge_mode_loader=lambda: "normal")  # type: ignore[arg-type]
        message = "根据这段完整文案生成 2 条短视频。核心主题：跨境运营。参考图：/tmp/ref.png"
        with patch("ai8video.application.conversation_controller.default_smart_split_enabled", return_value=True), \
                patch("ai8video.application.conversation_controller.default_smart_split_confirmation_enabled", return_value=True):
            planned = agent.handle_message(session_id, message)

        actions = planned.meta["guide"]["actions"]
        self.assertIn(
            {"kind": "dismiss-plan", "label": "取消", "value": "取消规划"},
            actions,
        )
        original_state = agent.sessions[session_id]
        self.assertTrue(agent.cancel_smart_split_confirmation(session_id))
        self.assertIsNot(agent.sessions[session_id], original_state)
        self.assertIsNone(agent.sessions[session_id].awaiting)
        self.assertEqual(agent.sessions[session_id].planned_videos, [])
        self.assertFalse(agent.sessions[session_id].planned_video_count_locked)
        self.assertFalse(agent.cancel_smart_split_confirmation(session_id))

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
            self.assertNotIn("风险篇", planned.text)
            self.assertEqual(
                planned.meta["guide"]["plannedVideos"][0],
                {
                    "index": 1,
                    "title": "风险篇",
                    "sourceSummary": "素材前半段",
                    "prompt": "提示词一",
                },
            )
            self.assertNotIn("videos", captured)

            completed = agent.handle_message("smart-split", "确认分集")

        self.assertEqual(completed.stage, "completed")
        self.assertIsNone(agent.sessions["smart-split"].awaiting)
        self.assertTrue(captured["smart_split"])
        self.assertEqual(len(captured["videos"]), 2)

    def test_smart_split_replan_command_keeps_context_and_locks_requested_count(self) -> None:
        planned_raw_texts: list[str] = []
        smart_split_flags: list[bool] = []
        generated_videos: list[list[VideoPrompt]] = []

        class FakePipeline:
            def plan_request(self, request, **kwargs):
                planned_raw_texts.append(request.raw_text)
                smart_split_flags.append(bool(kwargs.get("smart_split")))
                count = int(request.video_count or 1)
                return [
                    VideoPrompt(
                        index=index,
                        title=f"主题 {index}",
                        prompt=f"提示词 {index}",
                        source_summary=f"摘要 {index}",
                    )
                    for index in range(1, count + 1)
                ]

            def run_planned_request(self, request, videos, **kwargs):
                generated_videos.append(list(videos))
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
            first_plan = agent.handle_message("smart-split-feedback", message)
            revised_plan = agent.handle_message(
                "smart-split-feedback",
                "重新分集：5集，要偷偷地为向飞讯打广告",
            )
            self.assertEqual(agent.sessions["smart-split-feedback"].awaiting, "smart_split_confirmation")
            completed = agent.handle_message("smart-split-feedback", "确认并继续")

        self.assertEqual(first_plan.awaiting, "smart_split_confirmation")
        self.assertEqual(revised_plan.awaiting, "smart_split_confirmation")
        self.assertIn(message, planned_raw_texts[-1])
        self.assertIn("分集调整要求：重新分集：5集，要偷偷地为向飞讯打广告", planned_raw_texts[-1])
        self.assertEqual(len(planned_raw_texts), 2)
        self.assertEqual(smart_split_flags, [True, False])
        self.assertIn("固定规划为 5 条视频", revised_plan.text)
        self.assertEqual(len(generated_videos[-1]), 5)
        self.assertEqual(completed.stage, "completed")
        self.assertIsNone(agent.sessions["smart-split-feedback"].awaiting)

    def test_failed_smart_split_replan_consumes_previous_confirmation(self) -> None:
        plan_count = 0

        class FakePipeline:
            def plan_request(self, request, **kwargs):
                nonlocal plan_count
                plan_count += 1
                if plan_count > 1:
                    raise RuntimeError("重新规划失败")
                return [VideoPrompt(index=1, title="原方案", prompt="提示词", source_summary="原摘要")]

            def run_planned_request(self, request, videos, **kwargs):
                raise AssertionError("重新规划失败后不应进入生成")

        session_id = "smart-split-replan-failure"
        agent = AI8VideoConversationController(FakePipeline(), merge_mode_loader=lambda: "normal")  # type: ignore[arg-type]
        message = "根据这篇完整素材智能规划短视频。核心主题：跨境运营。参考图：/tmp/ref.png"
        with patch("ai8video.application.conversation_controller.default_smart_split_enabled", return_value=True), \
                patch("ai8video.application.conversation_controller.default_smart_split_confirmation_enabled", return_value=True):
            planned = agent.handle_message(session_id, message)
            self.assertEqual(planned.awaiting, "smart_split_confirmation")
            with self.assertRaisesRegex(RuntimeError, "重新规划失败"):
                agent.handle_message(session_id, "重新分集：3集")

        state = agent.sessions[session_id]
        self.assertIsNone(state.awaiting)
        self.assertEqual(state.planned_videos, [])


if __name__ == "__main__":
    unittest.main()
