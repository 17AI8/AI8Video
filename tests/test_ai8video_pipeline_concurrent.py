from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ai8video.core.config import AI8VideoConfig
from ai8video.generation.generation_progress import clear_generation_progress, get_generation_progress
from ai8video.integrations.direct_video_model_client import DirectVideoModelError
from ai8video.core.models import ArchivedAsset, VideoPrompt, FirstFrameAsset, ParsedRequest, QuickVideoJob
from ai8video.generation.pipeline import AI8VideoPipeline


class _FakeClient:
    def __init__(self) -> None:
        self.guard = SimpleNamespace(forced_duration_seconds=0, assert_can_create_count=lambda _count: None)
        self.created: list[int] = []
        self.first_frame_sources: list[str] = []
        self.poll_started_after_create_counts: list[int] = []

    def create_job(self, *, text, video_index, first_frame, duration_seconds, ratio, resolution, preset):
        self.created.append(video_index)
        self.first_frame_sources.append("" if first_frame is None else str(first_frame.source))
        return QuickVideoJob(
            video_index=video_index,
            job_id=f"job-{video_index}",
            status="pending",
            prompt=text,
        )

    def poll_job(self, job: QuickVideoJob) -> QuickVideoJob:
        self.poll_started_after_create_counts.append(len(self.created))
        time.sleep(0.01 * (4 - job.video_index))
        job.status = "succeeded"
        job.video_url = f"https://example.test/{job.video_index}.mp4"
        return job


class _ProgressFakeClient(_FakeClient):
    def poll_job(self, job: QuickVideoJob, progress_callback=None) -> QuickVideoJob:
        for value in (12, 37, 83):
            latest = QuickVideoJob(
                video_index=job.video_index,
                job_id=job.job_id,
                status="pending",
                prompt=job.prompt,
                provider_status="processing",
                provider_progress=value,
            )
            if progress_callback:
                progress_callback(latest)
        job.status = "succeeded"
        job.video_url = f"https://example.test/{job.video_index}.mp4"
        job.provider_status = "completed"
        job.provider_progress = 100
        return job


class _NoDelayFakeClient(_FakeClient):
    def poll_job(self, job: QuickVideoJob) -> QuickVideoJob:
        self.poll_started_after_create_counts.append(len(self.created))
        job.status = "succeeded"
        job.video_url = f"https://example.test/{job.video_index}.mp4"
        return job


class _FailedFakeClient(_FakeClient):
    def poll_job(self, job: QuickVideoJob) -> QuickVideoJob:
        job.status = "failed"
        job.error = "AI短视频额度不足"
        job.video_url = None
        job.storage_key = None
        return job


class _ConcurrentPartialTimeoutFakeClient(_FakeClient):
    def poll_job(self, job: QuickVideoJob) -> QuickVideoJob:
        if job.video_index == 1:
            job.status = "succeeded"
            job.video_url = "https://example.test/1.mp4"
            return job
        raise TimeoutError("Polling timed out for direct video model job job-2")

    def get_job(self, job_id: str, video_index: int = 1, prompt: str = "") -> QuickVideoJob:
        return QuickVideoJob(
            video_index=video_index,
            job_id=job_id,
            status="failed",
            prompt=prompt,
            error="上游任务失败",
            provider_status="failed",
            provider_progress=100,
        )


class _ConcurrentThreeItemPartialTimeoutFakeClient(_FakeClient):
    def poll_job(self, job: QuickVideoJob) -> QuickVideoJob:
        if job.video_index == 2:
            raise TimeoutError("Polling timed out for direct video model job job-2")
        job.status = "succeeded"
        job.video_url = f"https://example.test/{job.video_index}.mp4"
        return job

    def get_job(self, job_id: str, video_index: int = 1, prompt: str = "") -> QuickVideoJob:
        return QuickVideoJob(
            video_index=video_index,
            job_id=job_id,
            status="failed",
            prompt=prompt,
            error="上游任务失败",
            provider_status="failed",
            provider_progress=100,
        )


class _LimitedFakeClient(_FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.guard = SimpleNamespace(
            forced_duration_seconds=0,
            assert_can_create_count=self._assert_can_create_count,
        )

    def _assert_can_create_count(self, count: int) -> None:
        raise RuntimeError(f"真实生成额度已用完：本轮需要提交 {count} 条，当前剩余 0 条")


class _RejectedOnCreateFakeClient(_FakeClient):
    def create_job(self, *, text, video_index, first_frame, duration_seconds, ratio, resolution, preset):
        raise DirectVideoModelError("视频 1 未提交到真实生成后端：AI短视频额度不足")


class _FakeArchiver:
    def archive(self, request, video, job, outcome) -> ArchivedAsset:
        return ArchivedAsset(
            video_index=video.index,
            job_id=job.job_id,
            backend="test",
            status="stored",
            archive_key=f"video/{video.index}.mp4",
        )


class _FailingArchiver:
    def archive(self, request, video, job, outcome) -> ArchivedAsset:
        raise RuntimeError("花字烧录失败：No module named 'PIL'")


class _FakeAssetStore:
    def __init__(self) -> None:
        self.appended: list[int] = []

    def append(self, request, video, job, outcome, first_frame, archive):
        self.appended.append(video.index)
        return {"videoIndex": video.index, "jobId": job.job_id}


class _PerVideoReferencePreprocessor:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []

    def prepare_first_frame(self, request: ParsedRequest, video: VideoPrompt | None = None):
        if video is None:
            return None
        self.calls.append((video.index, video.prompt))
        return FirstFrameAsset(source=f"/tmp/reference-{video.index}.png")


class _TempTransformedReferencePreprocessor:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.paths: list[Path] = []

    def prepare_first_frame(self, request: ParsedRequest, video: VideoPrompt | None = None):
        if video is None:
            return None
        path = self.output_dir / f"reference-i2i-test-{video.index}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
        self.paths.append(path)
        return FirstFrameAsset(source=str(path))


class _DisconnectingReferencePreprocessor:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def prepare_first_frame(self, request: ParsedRequest, video: VideoPrompt | None = None):
        if video is not None:
            self.calls.append(video.index)
        raise ConnectionError("('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))")


class AI8VideoPipelineConcurrentTest(unittest.TestCase):
    def test_concurrent_mode_creates_all_jobs_before_polling_and_keeps_order(self) -> None:
        pipeline = AI8VideoPipeline.__new__(AI8VideoPipeline)
        pipeline.config = AI8VideoConfig(dry_run=True)
        pipeline.client = _NoDelayFakeClient()
        pipeline.reference_image_preprocessor = SimpleNamespace(prepare_first_frame=lambda _request: None)
        pipeline.archiver = _FakeArchiver()
        pipeline.asset_store = _FakeAssetStore()

        request = ParsedRequest(
            raw_text="三条视频素材",
            mode="batch_videos",
            video_count=3,
            concurrent_generation=True,
        )
        videos = [
            VideoPrompt(index=1, title="第一条视频", prompt="video1"),
            VideoPrompt(index=2, title="第二条视频", prompt="video2"),
            VideoPrompt(index=3, title="第三条视频", prompt="video3"),
        ]

        result = pipeline._run_videos(request, videos, progress_session_id="progress-test")

        self.assertEqual(pipeline.client.created, [1, 2, 3])
        self.assertTrue(all(count == 3 for count in pipeline.client.poll_started_after_create_counts))
        self.assertEqual([job.video_index for job in result.jobs], [1, 2, 3])
        self.assertEqual(sorted(pipeline.asset_store.appended), [1, 2, 3])
        progress = get_generation_progress("progress-test")
        self.assertIsNotNone(progress)
        self.assertEqual(progress["totalRequested"], 3)
        self.assertEqual(progress["submittedCount"], 3)
        self.assertEqual(progress["succeededCount"], 3)
        self.assertEqual(progress["runningCount"], 0)
        self.assertEqual([item["jobId"] for item in progress["items"]], ["job-1", "job-2", "job-3"])
        clear_generation_progress("progress-test")

    def test_concurrent_mode_staggers_five_submissions_without_waiting_for_poll_slots(self) -> None:
        pipeline = AI8VideoPipeline.__new__(AI8VideoPipeline)
        pipeline.config = AI8VideoConfig(dry_run=True)
        pipeline.client = _NoDelayFakeClient()
        pipeline.reference_image_preprocessor = SimpleNamespace(prepare_first_frame=lambda _request: None)
        pipeline.archiver = _FakeArchiver()
        pipeline.asset_store = _FakeAssetStore()

        request = ParsedRequest(
            raw_text="五条视频素材",
            mode="batch_videos",
            video_count=5,
            concurrent_generation=True,
        )
        videos = [
            VideoPrompt(index=index, title=f"第{index}集", prompt=f"ep{index}")
            for index in range(1, 6)
        ]

        with patch("ai8video.generation.pipeline.time.sleep") as submit_sleep:
            result = pipeline._run_videos(request, videos, progress_session_id="five-submit-progress-test")

        self.assertEqual(sorted(pipeline.client.created), [1, 2, 3, 4, 5])
        self.assertEqual(sorted(call.args[0] for call in submit_sleep.call_args_list), [1.0, 2.0, 3.0, 4.0])
        self.assertTrue(all(count == 5 for count in pipeline.client.poll_started_after_create_counts))
        self.assertEqual([job.video_index for job in result.jobs], [1, 2, 3, 4, 5])
        progress = get_generation_progress("five-submit-progress-test")
        self.assertIsNotNone(progress)
        self.assertEqual(progress["totalRequested"], 5)
        self.assertEqual(progress["submittedCount"], 5)
        self.assertEqual(progress["waitingCount"], 0)
        self.assertEqual(progress["succeededCount"], 5)
        clear_generation_progress("five-submit-progress-test")

    def test_concurrent_mode_prepares_reference_image_per_video(self) -> None:
        pipeline = AI8VideoPipeline.__new__(AI8VideoPipeline)
        pipeline.config = AI8VideoConfig(dry_run=True)
        pipeline.client = _FakeClient()
        preprocessor = _PerVideoReferencePreprocessor()
        pipeline.reference_image_preprocessor = preprocessor
        pipeline.archiver = _FakeArchiver()
        pipeline.asset_store = _FakeAssetStore()

        request = ParsedRequest(
            raw_text="三条视频素材",
            mode="batch_videos",
            video_count=3,
            concurrent_generation=True,
            reference_image="/tmp/default.png",
            reference_image_transform_options={"autoChangeBackground": True},
        )
        videos = [
            VideoPrompt(index=1, title="第一条视频", prompt="video1"),
            VideoPrompt(index=2, title="第二条视频", prompt="video2"),
            VideoPrompt(index=3, title="第三条视频", prompt="video3"),
        ]

        result = pipeline._run_videos(request, videos, progress_session_id="per-video-ref-test")

        self.assertEqual([item[0] for item in preprocessor.calls], [1, 2, 3])
        self.assertTrue(preprocessor.calls[0][1].startswith("video1"))
        self.assertTrue(preprocessor.calls[1][1].startswith("video2"))
        self.assertTrue(preprocessor.calls[2][1].startswith("video3"))
        self.assertEqual(pipeline.client.first_frame_sources, [
            "/tmp/reference-1.png",
            "/tmp/reference-2.png",
            "/tmp/reference-3.png",
        ])
        self.assertEqual(result.first_frame.source, "/tmp/reference-1.png")
        clear_generation_progress("per-video-ref-test")

    def test_concurrent_mode_does_not_submit_video_when_i2i_fails(self) -> None:
        pipeline = AI8VideoPipeline.__new__(AI8VideoPipeline)
        pipeline.config = AI8VideoConfig(dry_run=True)
        pipeline.client = _NoDelayFakeClient()
        preprocessor = _DisconnectingReferencePreprocessor()
        pipeline.reference_image_preprocessor = preprocessor
        pipeline.archiver = _FakeArchiver()
        pipeline.asset_store = _FakeAssetStore()

        request = ParsedRequest(
            raw_text="三条视频素材",
            mode="batch_videos",
            video_count=3,
            concurrent_generation=True,
            reference_image="/tmp/default.png",
            reference_image_transform_options={"autoChangeBackground": True},
        )
        videos = [
            VideoPrompt(index=1, title="第一条视频", prompt="video1"),
            VideoPrompt(index=2, title="第二条视频", prompt="video2"),
            VideoPrompt(index=3, title="第三条视频", prompt="video3"),
        ]

        result = pipeline._run_videos(request, videos, progress_session_id="i2i-fallback-progress-test")

        self.assertEqual(sorted(preprocessor.calls), [1, 2, 3])
        self.assertEqual(pipeline.client.created, [])
        self.assertEqual(pipeline.client.first_frame_sources, [])
        self.assertIsNone(result.first_frame)
        self.assertEqual([job.status for job in result.jobs], ["failed", "failed", "failed"])
        self.assertTrue(all(str(job.job_id).startswith("create-failed-") for job in result.jobs))
        progress = get_generation_progress("i2i-fallback-progress-test")
        self.assertIsNotNone(progress)
        self.assertEqual(progress["failedCount"], 3)
        self.assertEqual(progress["succeededCount"], 0)
        self.assertEqual([item["statusLabel"] for item in progress["items"]], ["首帧图未回填", "首帧图未回填", "首帧图未回填"])
        self.assertEqual([item["providerStatus"] for item in progress["items"]], [
            "first_frame_response_lost",
            "first_frame_response_lost",
            "first_frame_response_lost",
        ])
        clear_generation_progress("i2i-fallback-progress-test")

    def test_concurrent_mode_archives_transformed_reference_images_after_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output_dir = Path(tempdir) / "i2i"
            archive_dir = Path(tempdir) / "archive"
            pipeline = AI8VideoPipeline.__new__(AI8VideoPipeline)
            pipeline.config = AI8VideoConfig(dry_run=True, archive_local_dir=str(archive_dir))
            pipeline.client = _NoDelayFakeClient()
            preprocessor = _TempTransformedReferencePreprocessor(output_dir)
            pipeline.reference_image_preprocessor = preprocessor
            pipeline.archiver = _FakeArchiver()
            pipeline.asset_store = _FakeAssetStore()

            request = ParsedRequest(
                raw_text="两条视频素材",
                mode="batch_videos",
                video_count=2,
                concurrent_generation=True,
                reference_image="/tmp/default.png",
                reference_image_transform_options={"autoChangeBackground": True},
            )
            videos = [
                VideoPrompt(index=1, title="第一条视频", prompt="video1"),
                VideoPrompt(index=2, title="第二条视频", prompt="video2"),
            ]

            with patch("ai8video.generation.pipeline.TRANSFORMED_REFERENCE_DIR", output_dir), patch(
                "ai8video.generation.reference_image_preprocessor.TRANSFORMED_REFERENCE_DIR", output_dir
            ):
                result = pipeline._run_videos(request, videos, progress_session_id="cleanup-progress-test")

            self.assertEqual([job.video_index for job in result.jobs], [1, 2])
            self.assertEqual(len(preprocessor.paths), 2)
            self.assertTrue(all(not path.exists() for path in preprocessor.paths))
            self.assertEqual(len(list((archive_dir / "first-frames").rglob("*.png"))), 2)
            clear_generation_progress("cleanup-progress-test")

    def test_failed_first_frame_is_moved_to_archive_for_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output_dir = Path(tempdir) / "i2i"
            archive_dir = Path(tempdir) / "archive"
            first_frame_path = output_dir / "reference-i2i-test-1.png"
            first_frame_path.parent.mkdir(parents=True, exist_ok=True)
            first_frame_path.write_bytes(b"png")
            pipeline = AI8VideoPipeline.__new__(AI8VideoPipeline)
            pipeline.config = AI8VideoConfig(dry_run=True, archive_local_dir=str(archive_dir))
            first_frame = FirstFrameAsset(source=str(first_frame_path))
            video = VideoPrompt(index=1, title="第一条视频", prompt="video1")

            with patch("ai8video.generation.pipeline.TRANSFORMED_REFERENCE_DIR", output_dir):
                pipeline._archive_transformed_first_frame(first_frame, video, "retry-preserve-test")

            self.assertFalse(first_frame_path.exists())
            self.assertTrue(Path(str(first_frame.source)).is_file())

    def test_create_stage_rejection_keeps_transformed_reference_image(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output_dir = Path(tempdir) / "i2i"
            pipeline = AI8VideoPipeline.__new__(AI8VideoPipeline)
            pipeline.config = AI8VideoConfig(dry_run=False)
            pipeline.client = _RejectedOnCreateFakeClient()
            preprocessor = _TempTransformedReferencePreprocessor(output_dir)
            pipeline.reference_image_preprocessor = preprocessor
            pipeline.archiver = _FakeArchiver()
            pipeline.asset_store = _FakeAssetStore()

            request = ParsedRequest(
                raw_text="一条视频素材",
                mode="single_video",
                video_count=1,
                reference_image="/tmp/default.png",
                reference_image_transform_options={"autoChangeBackground": True},
            )
            videos = [VideoPrompt(index=1, title="第一条视频", prompt="video1")]

            with patch("ai8video.generation.reference_image_preprocessor.TRANSFORMED_REFERENCE_DIR", output_dir):
                with self.assertRaises(DirectVideoModelError):
                    pipeline._run_videos(request, videos, progress_session_id="cleanup-create-failed-test")

            self.assertEqual(len(preprocessor.paths), 1)
            self.assertTrue(preprocessor.paths[0].exists())
            clear_generation_progress("cleanup-create-failed-test")

    def test_pipeline_records_provider_progress_during_polling_and_clears_terminal_item(self) -> None:
        pipeline = AI8VideoPipeline.__new__(AI8VideoPipeline)
        pipeline.config = AI8VideoConfig(dry_run=True)
        pipeline.client = _ProgressFakeClient()
        pipeline.reference_image_preprocessor = SimpleNamespace(prepare_first_frame=lambda _request: None)
        pipeline.archiver = _FakeArchiver()
        pipeline.asset_store = _FakeAssetStore()

        request = ParsedRequest(
            raw_text="单条视频",
            mode="single_video",
            video_count=1,
        )
        videos = [VideoPrompt(index=1, title="第一条视频", prompt="video1")]

        pipeline._run_videos(request, videos, progress_session_id="provider-progress-test")

        progress = get_generation_progress("provider-progress-test")
        self.assertIsNotNone(progress)
        item = progress["items"][0]
        self.assertEqual(item["jobId"], "job-1")
        self.assertEqual(item["status"], "succeeded")
        self.assertNotIn("providerProgress", item)
        self.assertNotIn("providerStatus", item)
        provider_events = [event for event in progress["events"] if event.get("kind") == "provider_progress"]
        self.assertEqual([83], [event.get("providerProgress") for event in provider_events])
        clear_generation_progress("provider-progress-test")

    def test_failed_polled_job_stays_failed_and_does_not_get_fake_archive(self) -> None:
        pipeline = AI8VideoPipeline.__new__(AI8VideoPipeline)
        pipeline.config = AI8VideoConfig(dry_run=False)
        pipeline.client = _FailedFakeClient()
        pipeline.reference_image_preprocessor = SimpleNamespace(prepare_first_frame=lambda _request: None)
        pipeline.archiver = _FakeArchiver()
        pipeline.asset_store = _FakeAssetStore()

        request = ParsedRequest(
            raw_text="单条视频",
            mode="single_video",
            video_count=1,
        )
        videos = [VideoPrompt(index=1, title="失败视频", prompt="video1")]

        result = pipeline._run_videos(request, videos, progress_session_id="failed-progress-test")

        self.assertEqual(result.jobs[0].status, "failed")
        self.assertEqual(result.archives[0].status, "failed")
        self.assertIsNone(result.archives[0].archive_key)
        self.assertEqual(pipeline.asset_store.appended, [1])
        progress = get_generation_progress("failed-progress-test")
        self.assertIsNotNone(progress)
        self.assertEqual(progress["succeededCount"], 0)
        self.assertEqual(progress["failedCount"], 1)
        self.assertEqual(progress["items"][0]["status"], "failed")
        clear_generation_progress("failed-progress-test")

    def test_concurrent_partial_timeout_returns_mixed_result_not_global_error(self) -> None:
        pipeline = AI8VideoPipeline.__new__(AI8VideoPipeline)
        pipeline.config = AI8VideoConfig(dry_run=False)
        pipeline.client = _ConcurrentPartialTimeoutFakeClient()
        pipeline.reference_image_preprocessor = SimpleNamespace(prepare_first_frame=lambda _request: None)
        pipeline.archiver = _FakeArchiver()
        pipeline.asset_store = _FakeAssetStore()

        request = ParsedRequest(
            raw_text="两条视频素材",
            mode="batch_videos",
            video_count=2,
            concurrent_generation=True,
        )
        videos = [
            VideoPrompt(index=1, title="第一条视频", prompt="video1"),
            VideoPrompt(index=2, title="第二条视频", prompt="video2"),
        ]

        result = pipeline._run_videos(request, videos, progress_session_id="partial-timeout-progress-test")

        self.assertEqual([job.status for job in result.jobs], ["succeeded", "failed"])
        self.assertEqual([archive.status for archive in result.archives], ["stored", "failed"])
        self.assertEqual(sorted(pipeline.asset_store.appended), [1, 2])
        progress = get_generation_progress("partial-timeout-progress-test")
        self.assertIsNotNone(progress)
        self.assertEqual(progress["status"], "completed_with_error")
        self.assertEqual(progress["succeededCount"], 1)
        self.assertEqual(progress["failedCount"], 1)
        self.assertEqual(progress["runningCount"], 0)
        self.assertEqual([item["status"] for item in progress["items"]], ["succeeded", "failed"])
        clear_generation_progress("partial-timeout-progress-test")

    def test_archive_failure_marks_progress_failed_not_succeeded(self) -> None:
        pipeline = AI8VideoPipeline.__new__(AI8VideoPipeline)
        pipeline.config = AI8VideoConfig(dry_run=False)
        pipeline.client = _NoDelayFakeClient()
        pipeline.reference_image_preprocessor = SimpleNamespace(prepare_first_frame=lambda _request: None)
        pipeline.archiver = _FailingArchiver()
        pipeline.asset_store = _FakeAssetStore()

        request = ParsedRequest(raw_text="单条视频", mode="single_video")
        videos = [VideoPrompt(index=1, title="第一条视频", prompt="video1")]

        result = pipeline._run_videos(request, videos, progress_session_id="archive-failed-progress-test")

        self.assertEqual(result.jobs[0].status, "succeeded")
        self.assertEqual(result.archives[0].status, "error")
        self.assertIn("花字烧录失败", result.archives[0].error)
        progress = get_generation_progress("archive-failed-progress-test")
        self.assertIsNotNone(progress)
        self.assertEqual(progress["status"], "failed")
        self.assertEqual(progress["succeededCount"], 0)
        self.assertEqual(progress["failedCount"], 1)
        self.assertEqual(progress["items"][0]["status"], "failed")
        clear_generation_progress("archive-failed-progress-test")

    def test_concurrent_partial_timeout_with_three_items_keeps_whole_batch_shape(self) -> None:
        pipeline = AI8VideoPipeline.__new__(AI8VideoPipeline)
        pipeline.config = AI8VideoConfig(dry_run=False)
        pipeline.client = _ConcurrentThreeItemPartialTimeoutFakeClient()
        pipeline.reference_image_preprocessor = SimpleNamespace(prepare_first_frame=lambda _request: None)
        pipeline.archiver = _FakeArchiver()
        pipeline.asset_store = _FakeAssetStore()

        request = ParsedRequest(
            raw_text="三条视频素材",
            mode="batch_videos",
            video_count=3,
            concurrent_generation=True,
        )
        videos = [
            VideoPrompt(index=1, title="第一条视频", prompt="video1"),
            VideoPrompt(index=2, title="第二条视频", prompt="video2"),
            VideoPrompt(index=3, title="第三条视频", prompt="video3"),
        ]

        result = pipeline._run_videos(request, videos, progress_session_id="partial-timeout-three-progress-test")

        self.assertEqual([job.video_index for job in result.jobs], [1, 2, 3])
        self.assertEqual([job.status for job in result.jobs], ["succeeded", "failed", "succeeded"])
        self.assertEqual([archive.status for archive in result.archives], ["stored", "failed", "stored"])
        self.assertEqual(sorted(pipeline.asset_store.appended), [1, 2, 3])
        progress = get_generation_progress("partial-timeout-three-progress-test")
        self.assertIsNotNone(progress)
        self.assertEqual(progress["status"], "completed_with_error")
        self.assertEqual(progress["succeededCount"], 2)
        self.assertEqual(progress["failedCount"], 1)
        self.assertEqual(progress["runningCount"], 0)
        self.assertEqual([item["status"] for item in progress["items"]], ["succeeded", "failed", "succeeded"])
        clear_generation_progress("partial-timeout-three-progress-test")

    def test_real_generation_limit_blocks_batch_before_any_create_job(self) -> None:
        pipeline = AI8VideoPipeline.__new__(AI8VideoPipeline)
        pipeline.config = AI8VideoConfig(dry_run=False)
        pipeline.client = _LimitedFakeClient()
        pipeline.reference_image_preprocessor = SimpleNamespace(prepare_first_frame=lambda _request: None)
        pipeline.archiver = _FakeArchiver()
        pipeline.asset_store = _FakeAssetStore()

        request = ParsedRequest(
            raw_text="三条视频素材",
            mode="batch_videos",
            video_count=3,
            concurrent_generation=True,
        )
        videos = [
            VideoPrompt(index=1, title="第一条视频", prompt="video1"),
            VideoPrompt(index=2, title="第二条视频", prompt="video2"),
            VideoPrompt(index=3, title="第三条视频", prompt="video3"),
        ]

        with self.assertRaisesRegex(RuntimeError, "本轮需要提交 3 条"):
            pipeline._run_videos(request, videos, progress_session_id="limited-progress-test")

        self.assertEqual(pipeline.client.created, [])
        self.assertIsNone(get_generation_progress("limited-progress-test"))

    def test_create_stage_rejection_records_each_failed_item_without_waiting(self) -> None:
        pipeline = AI8VideoPipeline.__new__(AI8VideoPipeline)
        pipeline.config = AI8VideoConfig(dry_run=False)
        pipeline.client = _RejectedOnCreateFakeClient()
        pipeline.reference_image_preprocessor = SimpleNamespace(prepare_first_frame=lambda _request: None)
        pipeline.archiver = _FakeArchiver()
        pipeline.asset_store = _FakeAssetStore()

        request = ParsedRequest(
            raw_text="两条视频素材",
            mode="batch_videos",
            video_count=2,
            concurrent_generation=True,
        )
        videos = [
            VideoPrompt(index=1, title="第一条视频", prompt="video1"),
            VideoPrompt(index=2, title="第二条视频", prompt="video2"),
        ]

        with patch("ai8video.generation.pipeline.time.sleep"):
            result = pipeline._run_videos(request, videos, progress_session_id="create-rejected-progress-test")

        self.assertEqual([job.status for job in result.jobs], ["failed", "failed"])
        self.assertEqual(sorted(pipeline.asset_store.appended), [1, 2])
        progress = get_generation_progress("create-rejected-progress-test")
        self.assertIsNotNone(progress)
        self.assertEqual(progress["status"], "failed")
        self.assertEqual(progress["failedCount"], 2)
        self.assertEqual(progress["skippedCount"], 0)
        self.assertEqual(progress["waitingCount"], 0)
        self.assertEqual(progress["items"][0]["status"], "failed")
        self.assertEqual(progress["items"][1]["status"], "failed")
        clear_generation_progress("create-rejected-progress-test")


if __name__ == "__main__":
    unittest.main()
