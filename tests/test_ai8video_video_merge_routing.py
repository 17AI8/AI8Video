from __future__ import annotations

import unittest
from unittest.mock import patch

from ai8video.application.conversation_controller import AI8VideoConversationController
from ai8video.core.models import VideoPrompt, ParsedRequest, PipelineResult, QuickVideoJob


class _NormalPipeline:
    def __init__(self) -> None:
        self.called = 0

    def run_request(self, request: ParsedRequest, *, progress_session_id: str | None = None) -> PipelineResult:
        self.called += 1
        return PipelineResult(
            request=request,
            videos=[VideoPrompt(index=1, title="普通", prompt=request.raw_text)],
            first_frame=None,
            jobs=[QuickVideoJob(video_index=1, job_id="normal-1", status="succeeded", video_url="https://example.test/1.mp4")],
            dry_run=True,
        )


class _MergedPipeline:
    def __init__(self, segment_count: int = 2) -> None:
        self.called = 0
        self.segment_count = segment_count
        self.requests: list[ParsedRequest] = []

    def run_request(self, request: ParsedRequest, *, progress_session_id: str | None = None) -> PipelineResult:
        self.called += 1
        self.requests.append(request)
        return PipelineResult(
            request=request,
            videos=[VideoPrompt(index=1, title="合并", prompt=request.raw_text)],
            first_frame=None,
            jobs=[QuickVideoJob(video_index=1, job_id="merge-1", status="succeeded", video_url="https://example.test/merge.mp4")],
            dry_run=True,
        )


class AI8VideoVideoMergeRoutingTest(unittest.TestCase):
    def test_none_mode_uses_only_normal_pipeline(self) -> None:
        normal = _NormalPipeline()
        factory_calls = {"count": 0}

        def factory():
            factory_calls["count"] += 1
            return _MergedPipeline()

        agent = AI8VideoConversationController(
            normal,  # type: ignore[arg-type]
            merged_pipeline_factory=factory,
            merge_mode_loader=lambda: "none",
        )
        request = ParsedRequest(raw_text="生成一条", mode="single_video")

        result = agent._run_generation_request(request, progress_session_id="route-none")

        self.assertEqual(normal.called, 1)
        self.assertEqual(factory_calls["count"], 0)
        self.assertEqual(result.jobs[0].job_id, "normal-1")

    def test_merge2_mode_uses_only_merged_pipeline(self) -> None:
        normal = _NormalPipeline()
        merged = _MergedPipeline()
        agent = AI8VideoConversationController(
            normal,  # type: ignore[arg-type]
            merged_pipeline_factory=lambda: merged,
            merge_mode_loader=lambda: "merge2",
        )
        request = ParsedRequest(raw_text="生成一条", mode="single_video")

        result = agent._run_generation_request(request, progress_session_id="route-merge2")

        self.assertEqual(normal.called, 0)
        self.assertEqual(merged.called, 1)
        self.assertEqual(result.jobs[0].job_id, "merge-1")

    def test_merge4_mode_uses_merged_pipeline_with_four_segments(self) -> None:
        normal = _NormalPipeline()
        created: list[_MergedPipeline] = []

        def factory(*, segment_count: int):
            pipeline = _MergedPipeline(segment_count=segment_count)
            created.append(pipeline)
            return pipeline

        agent = AI8VideoConversationController(
            normal,  # type: ignore[arg-type]
            merged_pipeline_factory=factory,
            merge_mode_loader=lambda: "merge4",
        )
        request = ParsedRequest(raw_text="生成一条", mode="single_video")

        result = agent._run_generation_request(request, progress_session_id="route-merge4")

        self.assertEqual(normal.called, 0)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].segment_count, 4)
        self.assertEqual(created[0].called, 1)
        self.assertEqual(result.jobs[0].job_id, "merge-1")

    def test_merge2_failure_does_not_fallback_to_normal_pipeline(self) -> None:
        normal = _NormalPipeline()

        class FailingMergedPipeline:
            def run_request(self, request: ParsedRequest, *, progress_session_id: str | None = None) -> PipelineResult:
                raise RuntimeError("合并链路失败")

        agent = AI8VideoConversationController(
            normal,  # type: ignore[arg-type]
            merged_pipeline_factory=lambda: FailingMergedPipeline(),
            merge_mode_loader=lambda: "merge2",
        )

        with self.assertRaisesRegex(RuntimeError, "合并链路失败"):
            agent._run_generation_request(ParsedRequest(raw_text="生成一条", mode="single_video"))

        self.assertEqual(normal.called, 0)

    def test_merge2_chat_flow_keeps_pre_generation_tabs_in_normal_mode(self) -> None:
        normal = _NormalPipeline()
        merged = _MergedPipeline()
        agent = AI8VideoConversationController(
            normal,  # type: ignore[arg-type]
            merged_pipeline_factory=lambda: merged,
            merge_mode_loader=lambda: "merge2",
        )
        script_item = {
            "name": "2.docx",
            "relativePath": "2.docx",
            "path": "/tmp/2.docx",
            "kind": "script",
            "preview": "AI8video 发布倒计时。",
        }

        with patch("ai8video.knowledge.default_script_reference.load_default_script_reference", return_value=script_item), \
                patch("ai8video.knowledge.default_script_reference.read_script_material_text", return_value="参考剧本正文：AI8video 全球发布倒计时。"), \
                patch("ai8video.application.conversation_controller.default_reference_image_path", return_value="/tmp/default.png"), \
                patch(
                    "ai8video.application.conversation_controller.enabled_default_reference_image_options",
                    return_value={"autoChangeClothes": False, "autoChangeBackground": True, "autoChangePose": True},
                ), patch("ai8video.application.conversation_controller.default_concurrent_generation_enabled", return_value=False):
            reply = agent.handle_message("merge2-tabs-normal", "2个")

        self.assertEqual(reply.stage, "completed")
        self.assertEqual(normal.called, 0)
        self.assertEqual(merged.called, 1)
        request = merged.requests[0]
        self.assertIn("剧本参考《2.docx》内容", request.raw_text)
        self.assertEqual(request.reference_image, "/tmp/default.png")
        self.assertEqual(request.reference_image_transform_options, {
            "autoChangeClothes": False,
            "autoChangeBackground": True,
            "autoChangePose": True,
        })
        self.assertFalse(request.concurrent_generation)

    def test_merge2_chat_flow_keeps_pre_generation_tabs_in_concurrent_mode(self) -> None:
        normal = _NormalPipeline()
        merged = _MergedPipeline()
        agent = AI8VideoConversationController(
            normal,  # type: ignore[arg-type]
            merged_pipeline_factory=lambda: merged,
            merge_mode_loader=lambda: "merge2",
        )
        script_item = {
            "name": "2.docx",
            "relativePath": "2.docx",
            "path": "/tmp/2.docx",
            "kind": "script",
            "preview": "AI8video 发布倒计时。",
        }

        with patch("ai8video.knowledge.default_script_reference.load_default_script_reference", return_value=script_item), \
                patch("ai8video.knowledge.default_script_reference.read_script_material_text", return_value="参考剧本正文：AI8video 全球发布倒计时。"), \
                patch("ai8video.application.conversation_controller.default_reference_image_path", return_value="/tmp/default.png"), \
                patch(
                    "ai8video.application.conversation_controller.enabled_default_reference_image_options",
                    return_value={"autoChangeClothes": True, "autoChangeBackground": False, "autoChangePose": True},
                ), patch("ai8video.application.conversation_controller.default_concurrent_generation_enabled", return_value=True):
            reply = agent.handle_message("merge2-tabs-concurrent", "2个")

        self.assertEqual(reply.stage, "completed")
        self.assertEqual(normal.called, 0)
        self.assertEqual(merged.called, 1)
        request = merged.requests[0]
        self.assertIn("剧本参考《2.docx》内容", request.raw_text)
        self.assertEqual(request.reference_image, "/tmp/default.png")
        self.assertEqual(request.reference_image_transform_options, {
            "autoChangeClothes": True,
            "autoChangeBackground": False,
            "autoChangePose": True,
        })
        self.assertTrue(request.concurrent_generation)


if __name__ == "__main__":
    unittest.main()
