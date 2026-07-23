from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai8video.core.models import ArchivedAsset, GenerationOutcome, ParsedRequest, QuickVideoJob, VideoPrompt
from ai8video.generation.adaptive_batch_runner import AdaptiveBatchRunner
from ai8video.generation.generation_progress import (
    clear_generation_progress,
    get_generation_progress,
    mark_job_succeeded,
)
from ai8video.generation.iterative_batch_policy import (
    IterativeBatchPolicyError,
    normalize_iterative_batch_request,
)
from ai8video.generation.pipeline import AI8VideoPipeline


class _RecordingClient:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.created: list[int] = []
        self.durations: list[int] = []
        self.guard = SimpleNamespace(
            forced_duration_seconds=0,
            assert_can_create_count=lambda count: self.events.append(f"guard-{count}"),
        )

    def create_job(self, *, text, video_index, first_frame, duration_seconds, ratio, resolution, preset):
        self.events.append(f"create-{video_index}")
        self.created.append(video_index)
        self.durations.append(duration_seconds)
        return QuickVideoJob(video_index, f"job-{video_index}", status="pending", prompt=text)


class _RecordingPipeline:
    def __init__(self, reviews: dict[int, dict]) -> None:
        self.events: list[str] = []
        self.config = SimpleNamespace(dry_run=False, archive_backend="test")
        self.client = _RecordingClient(self.events)
        self.llm = None
        self.reviews = reviews

    def _reference_task_constraints(self, _request):
        return None

    def _trace_final_video_prompt(self, _request, _video, _session_id):
        return None

    def _trace_video_submit(self, _request, _video, _first_frame, _session_id):
        return None

    def _trace_video_job_created(self, _request, _video, _job, _session_id, *, duration_seconds):
        return None

    def _prepare_video_first_frame(self, _request, _video, *, progress_session_id):
        return None

    def _poll_job(self, job, _session_id):
        self.events.append(f"poll-{job.video_index}")
        job.status = "succeeded"
        job.video_url = f"https://example.test/{job.video_index}.mp4"
        return job

    def _record_completed_job(self, request, video, completed_job, first_frame, progress_session_id):
        self.events.append(f"generated-review-{video.index}")
        guidance = dict(video.keyword_guidance or {})
        guidance["generated_output_review"] = dict(self.reviews[video.index])
        video.keyword_guidance = guidance
        outcome = GenerationOutcome(video.index, completed_job.job_id, "succeeded", "generated")
        archive = ArchivedAsset(video.index, completed_job.job_id, "test", "archived", local_path=f"/tmp/{video.index}.mp4")
        asset_record = {"videoIndex": video.index, "jobId": completed_job.job_id}
        mark_job_succeeded(progress_session_id, completed_job, asset_record)
        return outcome, archive, asset_record


def _review(*, status: str = "completed", source_index: int = 1) -> dict:
    return {
        "status": status,
        "passes": True if status == "completed" else None,
        "issues": [f"第 {source_index} 条主体边缘有轻微闪动"] if status == "completed" else [],
        "improvements": [f"第 {source_index} 条后续镜头减少快速转身"] if status == "completed" else [],
        "nextPromptConstraints": [f"吸收第 {source_index} 条经验，保持主体朝向稳定"] if status == "completed" else [],
        "reviewSource": "test" if status == "completed" else "unavailable",
    }


class AI8VideoIterativeBatchPolicyTest(unittest.TestCase):
    def test_batch_is_normalized_to_at_most_five_serial_ten_second_videos(self) -> None:
        request = ParsedRequest(
            raw_text="生成五条",
            mode="batch_videos",
            video_count=5,
            duration_seconds=18,
            concurrent_generation=True,
        )

        normalized = normalize_iterative_batch_request(request)

        self.assertEqual(normalized.video_count, 5)
        self.assertEqual(normalized.duration_seconds, 10)
        self.assertFalse(normalized.concurrent_generation)
        self.assertTrue(normalized.iterative_generation)

    def test_batch_rejects_six_before_any_generation(self) -> None:
        request = ParsedRequest(raw_text="生成六条", mode="batch_videos", video_count=6)

        with self.assertRaisesRegex(IterativeBatchPolicyError, "最多生成 5 条"):
            normalize_iterative_batch_request(request)


class AI8VideoAdaptiveBatchRunnerTest(unittest.TestCase):
    def test_each_output_is_reviewed_before_feedback_rewrites_the_next_video(self) -> None:
        pipeline = _RecordingPipeline({index: _review(source_index=index) for index in range(1, 4)})
        request = ParsedRequest(
            raw_text="生成三条",
            mode="batch_videos",
            video_count=3,
            duration_seconds=10,
            iterative_generation=True,
        )
        videos = [VideoPrompt(index, f"第 {index} 条", f"原始提示词 {index}") for index in range(1, 4)]

        def finalize(items, **_kwargs):
            pipeline.events.append(f"finalize-{items[0].index}")
            return items

        def pre_review(items, **_kwargs):
            pipeline.events.append(f"pre-review-{items[0].index}")
            return items

        def rewrite(video, instruction, **_kwargs):
            pipeline.events.append(f"rewrite-{video.index}")
            return replace(video, prompt=f"{video.prompt}\n{instruction}")

        session_id = "iterative-order"
        try:
            with patch("ai8video.generation.adaptive_batch_runner.finalize_video_prompts", side_effect=finalize), patch(
                "ai8video.generation.adaptive_batch_runner.review_final_outputs", side_effect=pre_review
            ), patch("ai8video.generation.adaptive_batch_runner.rewrite_video_with_ai", side_effect=rewrite):
                result = AdaptiveBatchRunner(pipeline).run(request, videos, progress_session_id=session_id)
        finally:
            progress = get_generation_progress(session_id)
            clear_generation_progress(session_id)

        self.assertEqual(pipeline.client.created, [1, 2, 3])
        self.assertEqual(pipeline.client.durations, [10, 10, 10])
        self.assertEqual(
            pipeline.events,
            [
                "guard-3",
                "finalize-1", "pre-review-1", "create-1", "poll-1", "generated-review-1",
                "rewrite-2", "finalize-2", "pre-review-2", "create-2", "poll-2", "generated-review-2",
                "rewrite-3", "finalize-3", "pre-review-3", "create-3", "poll-3", "generated-review-3",
            ],
        )
        self.assertNotIn("iteration", result.videos[0].keyword_guidance)
        self.assertEqual(result.videos[1].keyword_guidance["iteration"]["sourceVideoIndex"], 1)
        self.assertEqual(result.videos[2].keyword_guidance["iteration"]["sourceVideoIndex"], 2)
        self.assertEqual(progress["status"], "completed")

    def test_unavailable_review_stops_remaining_submissions_without_retry(self) -> None:
        pipeline = _RecordingPipeline({1: _review(status="unavailable")})
        request = ParsedRequest(
            raw_text="生成三条",
            mode="batch_videos",
            video_count=3,
            duration_seconds=10,
            iterative_generation=True,
        )
        videos = [VideoPrompt(index, f"第 {index} 条", f"提示词 {index}") for index in range(1, 4)]
        session_id = "iterative-stop"
        try:
            with patch("ai8video.generation.adaptive_batch_runner.finalize_video_prompts", side_effect=lambda items, **_: items), patch(
                "ai8video.generation.adaptive_batch_runner.review_final_outputs", side_effect=lambda items, **_: items
            ):
                result = AdaptiveBatchRunner(pipeline).run(request, videos, progress_session_id=session_id)
            progress = get_generation_progress(session_id)
        finally:
            clear_generation_progress(session_id)

        self.assertEqual(pipeline.client.created, [1])
        self.assertEqual([job.status for job in result.jobs], ["succeeded", "skipped", "skipped"])
        self.assertEqual([outcome.decision for outcome in result.outcomes], ["generated", "skipped", "skipped"])
        self.assertEqual(progress["status"], "completed_with_error")
        self.assertEqual([item["status"] for item in progress["items"]], ["succeeded", "skipped", "skipped"])
        self.assertTrue(all("审查不可用" in item.get("error", "") for item in progress["items"][1:]))

    def test_pipeline_records_generated_review_before_persisting_asset(self) -> None:
        captured: dict = {}
        pipeline = AI8VideoPipeline.__new__(AI8VideoPipeline)
        pipeline.config = SimpleNamespace(archive_backend="test")
        pipeline.archiver = SimpleNamespace(
            archive=lambda request, video, job, outcome: ArchivedAsset(
                video.index,
                job.job_id,
                "test",
                "archived",
                local_path=f"/tmp/{video.index}.mp4",
            )
        )
        pipeline.generated_output_reviewer = SimpleNamespace(
            review=lambda path, video, **kwargs: {
                "status": "completed",
                "passes": False,
                "issues": ["主体闪动"],
                "improvements": ["保持主体稳定"],
                "nextPromptConstraints": ["减少快速转身"],
                "reviewSource": "multimodal_contact_sheet",
            }
        )

        def append_asset(request, video, job, outcome, first_frame, archive):
            captured["guidance"] = dict(video.keyword_guidance)
            captured["outcomeMeta"] = dict(outcome.meta)
            return {"videoIndex": video.index}

        pipeline.asset_store = SimpleNamespace(append=append_asset)
        request = ParsedRequest(
            raw_text="生成一条批量视频",
            mode="batch_videos",
            video_count=1,
            duration_seconds=10,
            iterative_generation=True,
        )
        video = VideoPrompt(1, "第一条", "提示词")
        job = QuickVideoJob(1, "job-1", status="succeeded", video_url="https://example.test/1.mp4")

        with patch("ai8video.generation.pipeline.observe_reviewer_shadow") as observer:
            outcome, archive, asset_record = pipeline._record_completed_job(request, video, job, None, None)

        self.assertEqual(outcome.decision, "generated")
        self.assertEqual(archive.status, "archived")
        self.assertEqual(asset_record, {"videoIndex": 1})
        self.assertEqual(captured["guidance"]["generated_output_review"]["issues"], ["主体闪动"])
        self.assertEqual(captured["outcomeMeta"]["generatedOutputReview"]["reviewSource"], "multimodal_contact_sheet")
        observer.assert_called_once()
        self.assertEqual(observer.call_args.kwargs["task_scope"], "generated-1")


if __name__ == "__main__":
    unittest.main()
