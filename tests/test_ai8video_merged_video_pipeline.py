from __future__ import annotations

from contextlib import contextmanager
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ai8video.generation import business_prompt
from ai8video.core.config import AI8VideoConfig
from ai8video.generation.generation_progress import (
    cancel_generation_progress,
    clear_generation_progress,
    get_generation_progress,
    mark_job_polling,
    start_generation_progress,
)
from ai8video.generation.pipeline import AI8VideoPipeline
from ai8video.generation.merged_video_pipeline import (
    AI8VideoMergedPipeline,
    TAIL_FRAME_PROMPT_SUFFIX,
    _merged_local_tts_narration_text,
)
from ai8video.core.models import ArchivedAsset, EpisodePrompt, FirstFrameAsset, ParsedRequest, QuickVideoJob


class _FakeClient:
    def __init__(self) -> None:
        self.guard = SimpleNamespace(forced_duration_seconds=0, assert_can_create_count=lambda _count: None)
        self.created: list[dict] = []

    def create_job(self, *, text, episode_index, first_frame, duration_seconds, ratio, resolution, preset):
        self.created.append({
            "episodeIndex": episode_index,
            "prompt": text,
            "firstFrameSource": None if first_frame is None else first_frame.source,
            "durationSeconds": duration_seconds,
        })
        job_id = f"job-{len(self.created)}"
        return QuickVideoJob(
            episode_index=episode_index,
            job_id=job_id,
            status="succeeded",
            prompt=text,
            video_url=f"https://example.invalid/{job_id}.mp4",
        )

    def poll_job(self, job: QuickVideoJob) -> QuickVideoJob:
        return job


class _BarrierClient(_FakeClient):
    def __init__(self, barrier: threading.Barrier) -> None:
        super().__init__()
        self.barrier = barrier
        self.lock = threading.Lock()

    def create_job(self, *, text, episode_index, first_frame, duration_seconds, ratio, resolution, preset):
        if first_frame is None:
            self.barrier.wait(timeout=2)
            time.sleep(0.01)
        with self.lock:
            return super().create_job(
                text=text,
                episode_index=episode_index,
                first_frame=first_frame,
                duration_seconds=duration_seconds,
                ratio=ratio,
                resolution=resolution,
                preset=preset,
            )


class _CancelAfterFirstPollClient(_FakeClient):
    def __init__(self, session_id: str) -> None:
        super().__init__()
        self.session_id = session_id

    def poll_job(self, job: QuickVideoJob) -> QuickVideoJob:
        cancel_generation_progress(self.session_id, "用户强行终止")
        return job


class _FailSecondPollClient(_FakeClient):
    def poll_job(self, job: QuickVideoJob) -> QuickVideoJob:
        if len(self.created) >= 2:
            return QuickVideoJob(
                episode_index=job.episode_index,
                job_id=job.job_id,
                status="failed",
                prompt=job.prompt,
                error="上游敏感信息失败",
            )
        return job


class _FakeArchiver:
    def __init__(self) -> None:
        self.archived: list[Path] = []

    def archive_local_file(self, source_video, request, episode, job, outcome, *, extra_meta=None):
        source = Path(source_video)
        self.archived.append(source)
        return ArchivedAsset(
            episode_index=episode.index,
            job_id=job.job_id,
            backend="test",
            status="archived",
            archive_key=f"video/{episode.index}.mp4",
            local_path=str(source),
            meta=extra_meta or {},
        )


class _FakeAssetStore:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def append(self, request, episode, job, outcome, first_frame, archive):
        record = {"episodeIndex": episode.index, "jobId": job.job_id, "archiveKey": archive.archive_key}
        self.records.append(record)
        return record


class _TempTransformedReferencePreprocessor:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.paths: list[Path] = []

    def prepare_first_frame(self, request: ParsedRequest, episode=None, trace_session_id=None):
        del request, trace_session_id
        if episode is None:
            return None
        path = self.output_dir / f"reference-i2i-merge-{episode.index}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
        self.paths.append(path)
        return FirstFrameAsset(source=str(path))


def _build_pipeline(client=None, *, segment_count: int = 2) -> AI8VideoMergedPipeline:
    pipeline = AI8VideoMergedPipeline.__new__(AI8VideoMergedPipeline)
    pipeline.config = AI8VideoConfig(dry_run=True, archive_backend="local")
    pipeline.llm = None
    pipeline.segment_count = segment_count
    pipeline.client = client or _FakeClient()
    pipeline.reference_image_preprocessor = SimpleNamespace(prepare_first_frame=lambda _request, episode=None, trace_session_id=None: None)
    pipeline.archiver = _FakeArchiver()
    pipeline.asset_store = _FakeAssetStore()
    return pipeline


@contextmanager
def _patch_postprocess():
    def materialize(job, work_dir, *, name, dry_run=False, duration_seconds=1, timeout_seconds=180):
        path = Path(work_dir) / f"{name}.mp4"
        path.write_bytes(b"segment")
        return path

    def tail(video_path, output_path=None, **_kwargs):
        path = output_path or Path(video_path).with_suffix(".tail.jpg")
        path.write_bytes(b"tail")
        return path

    def concat(video_paths, output_path, **_kwargs):
        output_path.write_bytes(b"merged")
        return {"status": "merged", "method": "test", "segments": [str(item) for item in video_paths]}

    with tempfile.TemporaryDirectory() as tempdir:
        with patch.multiple(
            "ai8video.generation.merged_video_pipeline",
            materialize_segment_video=materialize,
            extract_tail_frame=tail,
            concat_videos=concat,
        ):
            yield


class AI8VideoMergedPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.narration_review_patcher = patch(
            "ai8video.generation.merged_video_pipeline.narration_review_status",
            return_value={"ok": True, "reviewCount": 0},
        )
        self.narration_review_patcher.start()

    def tearDown(self) -> None:
        self.narration_review_patcher.stop()

    def test_run_request_plans_final_video_with_double_duration(self) -> None:
        pipeline = _build_pipeline()
        request = ParsedRequest(raw_text="生成一条", mode="single_prompt", duration_seconds=8)
        captured = {}

        def fake_run(final_request, episodes, *, segment_duration_seconds, progress_session_id=None):
            captured["request"] = final_request
            captured["episodes"] = episodes
            captured["segmentDurationSeconds"] = segment_duration_seconds
            return SimpleNamespace(request=final_request)

        with patch.object(pipeline, "_run_final_episodes", side_effect=fake_run):
            pipeline.run_request(request, progress_session_id="merge-plan")

        self.assertEqual(captured["request"].duration_seconds, 16)
        self.assertEqual(captured["segmentDurationSeconds"], 8)
        self.assertIn("16 秒", captured["episodes"][0].prompt)

    def test_merge2_starts_progress_before_finalize_episode_prompts(self) -> None:
        pipeline = _build_pipeline()
        request = ParsedRequest(raw_text="生成一条", mode="single_prompt", duration_seconds=10)
        episodes = [EpisodePrompt(index=1, title="第一条", prompt="原始提示词")]
        session_id = "merge-progress-before-finalize"

        def fake_finalize(*_args, **_kwargs):
            progress = get_generation_progress(session_id)
            self.assertIsNotNone(progress)
            self.assertEqual(progress["status"], "active")
            self.assertEqual(progress["totalRequested"], 1)
            self.assertEqual(progress["items"][0]["status"], "pending_submission")
            raise RuntimeError("stop after progress assertion")

        try:
            with patch("ai8video.generation.merged_video_pipeline.finalize_episode_prompts", side_effect=fake_finalize):
                with self.assertRaisesRegex(RuntimeError, "stop after progress assertion"):
                    pipeline._run_final_episodes(
                        request,
                        episodes,
                        segment_duration_seconds=10,
                        progress_session_id=session_id,
                    )
        finally:
            clear_generation_progress(session_id)

    def test_merge2_creates_two_segments_and_uses_tail_frame_as_second_first_frame(self) -> None:
        pipeline = _build_pipeline()
        request = ParsedRequest(raw_text="生成一条", mode="single_prompt", duration_seconds=10)
        episodes = [EpisodePrompt(
            index=1,
            title="第一条",
            prompt=(
                "总时长20秒，由两个连续10秒片段组成。\n"
                "0-5秒：前半段开场。\n"
                "5-10秒：前半段推进。\n"
                "10-15秒：后半段承接。\n"
                "15-20秒：后半段收束。"
            ),
        )]

        with patch("ai8video.generation.merged_video_pipeline.finalize_episode_prompts", return_value=episodes), \
                _patch_postprocess():
            result = pipeline._run_final_episodes(
                request,
                episodes,
                segment_duration_seconds=10,
                progress_session_id="merge-one",
            )

        self.assertEqual(len(pipeline.client.created), 2)
        self.assertIsNone(pipeline.client.created[0]["firstFrameSource"])
        self.assertIn("临时媒体", str(pipeline.client.created[1]["firstFrameSource"]))
        self.assertIn("视频合并", str(pipeline.client.created[1]["firstFrameSource"]))
        self.assertTrue(str(pipeline.client.created[1]["firstFrameSource"]).endswith("01-segment-1-tail.png"))
        self.assertTrue(all(TAIL_FRAME_PROMPT_SUFFIX in item["prompt"] for item in pipeline.client.created))
        self.assertNotIn("本片段是同一集合并视频", pipeline.client.created[0]["prompt"])
        self.assertNotIn("本次提交给视频模型的目标时长", pipeline.client.created[0]["prompt"])
        self.assertIn("镜头一（0-5s）：前半段开场", pipeline.client.created[0]["prompt"])
        self.assertIn("镜头二（5-10s）：前半段推进", pipeline.client.created[0]["prompt"])
        self.assertNotIn("后半段承接", pipeline.client.created[0]["prompt"])
        self.assertNotIn("本片段是同一集合并视频", pipeline.client.created[1]["prompt"])
        self.assertNotIn("本次提交给视频模型的目标时长", pipeline.client.created[1]["prompt"])
        self.assertIn("镜头一（0-5s）：后半段承接", pipeline.client.created[1]["prompt"])
        self.assertIn("镜头二（5-10s）：后半段收束", pipeline.client.created[1]["prompt"])
        self.assertNotIn("前半段开场", pipeline.client.created[1]["prompt"])
        self.assertNotIn("总时长20秒", pipeline.client.created[0]["prompt"])
        self.assertNotIn("总时长20秒", pipeline.client.created[1]["prompt"])
        self.assertEqual(pipeline.client.created[0]["durationSeconds"], 10)
        self.assertEqual(pipeline.client.created[1]["durationSeconds"], 10)
        self.assertEqual(result.request.duration_seconds, 10)
        self.assertEqual(result.jobs[0].status, "succeeded")
        self.assertEqual(len(result.asset_records), 1)
        self.assertIn("segmentRecords", result.outcomes[0].meta)
        segment_records = result.outcomes[0].meta["segmentRecords"]
        self.assertIn("rawTailFramePath", segment_records[0])
        self.assertEqual(segment_records[0]["tailFrameLifecycle"], "user-visible-temp")
        self.assertNotEqual(segment_records[0]["rawTailFramePath"], segment_records[0]["tailFramePath"])
        self.assertIn("临时媒体", segment_records[0]["tailFramePath"])
        self.assertTrue(Path(segment_records[0]["tailFramePath"]).exists())
        progress = get_generation_progress("merge-one")
        self.assertIsNotNone(progress)
        self.assertEqual(progress["items"][0]["status"], "succeeded")
        segment_status = progress["items"][0]["segmentStatus"]
        self.assertEqual([item["segmentLabel"] for item in segment_status], ["片段 1", "片段 2"])
        self.assertEqual([item["status"] for item in segment_status], ["archiving", "archiving"])
        self.assertEqual([item["jobId"] for item in segment_status], ["job-1", "job-2"])
        clear_generation_progress("merge-one")

    def test_merge2_local_tts_narration_uses_ordered_segment_dialogue(self) -> None:
        pipeline = _build_pipeline()
        request = ParsedRequest(raw_text="生成一条", mode="single_prompt", duration_seconds=10)
        episodes = [EpisodePrompt(
            index=1,
            title="第一条",
            prompt=(
                "镜头一（0-5s）：中景。台词/口播：片段一第一句。\n"
                "镜头二（5-10s）：近景。台词/口播：片段一第二句。\n"
                "镜头三（10-15s）：远景。台词/口播：片段二第一句。\n"
                "镜头四（15-20s）：特写。台词/口播：片段二第二句。"
            ),
        )]

        with patch("ai8video.generation.merged_video_pipeline.finalize_episode_prompts", return_value=episodes), \
                _patch_postprocess():
            result = pipeline._run_final_episodes(
                request,
                episodes,
                segment_duration_seconds=10,
                progress_session_id="merge-tts-text",
            )

        text = result.jobs[0].usage["localTtsNarrationText"]
        self.assertIn("片段一第一句", text)
        self.assertIn("片段一第二句", text)
        self.assertIn("片段二第一句", text)
        self.assertIn("片段二第二句", text)
        self.assertLess(text.index("片段一第二句"), text.index("片段二第一句"))
        self.assertEqual(result.outcomes[0].meta["segmentRecords"][0]["narrationText"], "片段一第一句。 片段一第二句")

    def test_merge2_local_tts_narration_preserves_pause_between_segments(self) -> None:
        episode = EpisodePrompt(index=1, title="第一条", prompt="兜底口播")

        text = _merged_local_tts_narration_text(
            [
                {"narrationText": "不用切换应用，一个入口，触达全球"},
                {"narrationText": "AI实时翻译，覆盖文本、语音、视频"},
            ],
            episode,
        )

        self.assertIn("触达全球。 AI实时翻译", text)

    def test_empty_reviewed_narration_does_not_fallback_to_episode_prompt(self) -> None:
        episode = EpisodePrompt(index=1, title="第一条", prompt="禁止朗读的完整视频提示词")

        text = _merged_local_tts_narration_text(
            [{"narrationText": ""}, {"narrationText": ""}],
            episode,
        )

        self.assertEqual(text, "")

    def test_local_tts_narration_duration_fit_uses_ai_model(self) -> None:
        pipeline = _build_pipeline()
        calls: list[str] = []

        def llm(prompt: str) -> str:
            calls.append(prompt)
            return '{"narration_text":"压缩后的口播。","estimated_seconds":8.2,"notes":"已按时长压缩"}'

        pipeline.llm = llm
        episode = EpisodePrompt(index=1, title="第一条", prompt="原始提示词")

        result = pipeline._fit_local_tts_narration_to_duration(
            "这是一段明显偏长的口播，需要让模型按时长处理。",
            episode=episode,
            target_duration_seconds=10,
            progress_session_id="merge-tts-duration-fit",
            allow_model_rewrite=True,
        )

        self.assertEqual(result["status"], "model_adjusted")
        self.assertEqual(result["text"], "压缩后的口播")
        self.assertEqual(result["estimatedSeconds"], 8.2)
        self.assertTrue(any("TTS 时长校准模型" in call for call in calls))

    def test_local_tts_narration_duration_fit_locks_source_by_default(self) -> None:
        pipeline = _build_pipeline()
        pipeline.llm = lambda _prompt: '{"narration_text":"不应该出现"}'
        episode = EpisodePrompt(index=1, title="第一条", prompt="原始提示词")

        result = pipeline._fit_local_tts_narration_to_duration(
            "源头已经按最终时长写好的自然口播。",
            episode=episode,
            target_duration_seconds=20,
            progress_session_id="merge-tts-source-locked",
        )

        self.assertEqual(result["status"], "source_locked")
        self.assertEqual(result["text"], "源头已经按最终时长写好的自然口播")

    def test_merge2_local_tts_narration_extracts_parenthesized_voiceover_dialogue(self) -> None:
        pipeline = _build_pipeline()
        request = ParsedRequest(raw_text="生成一条", mode="single_prompt", duration_seconds=10)
        episodes = [EpisodePrompt(
            index=2,
            title="第二条",
            prompt=(
                "镜头一（0-5s）：中近景，女性角色在明亮办公室，面前空无一物，做出点击手势。"
                "台词（画外音，好奇且惊喜）："
                "“刚刚体验完，我真的被震惊了。它不仅是聊天软件，更是AI8video 超级平台。”"
                "音效：清脆的鼠标点击声。\n"
                "镜头二（5-10s）：特写，女性角色戴着耳机。"
                "台词（画外音，兴奋）："
                "“AI翻译、AI回复、AI视频生成，全部自动完成。聊天、支付、办公、社群，一次拥有。”"
                "音效：快速的信息流声音。\n"
                "镜头三（10-15s）：侧脸中景，女性角色站在大落地窗前。"
                "台词（画外音，自豪）："
                "“AI8video，全球首款AI跨语言私域社交平台。支持180多种语言实时翻译，不会外语也能全球畅聊。”"
                "音效：全球各地人声混合。\n"
                "镜头四（15-20s）：全景，女性角色坐在现代沙发中。"
                "台词（画外音，笃定）："
                "“未来最赚钱的平台，一定是生态平台。今天开始，你也能拥有。”"
                "音效：舒缓的钢琴音乐渐赴高潮。\n"
                "所有主体最后一秒尽可能全身正对着镜头。"
            ),
        )]

        with patch("ai8video.generation.merged_video_pipeline.finalize_episode_prompts", return_value=episodes), \
                _patch_postprocess():
            result = pipeline._run_final_episodes(
                request,
                episodes,
                segment_duration_seconds=10,
                progress_session_id="merge-tts-parenthesized",
            )

        segment_records = result.outcomes[0].meta["segmentRecords"]
        self.assertEqual(
            segment_records[0]["narrationText"],
            "刚刚体验完，我真的被震惊了。它不仅是聊天软件，更是AI8video 超级平台。 "
            "AI翻译、AI回复、AI视频生成，全部自动完成。聊天、支付、办公、社群，一次拥有",
        )
        self.assertEqual(
            segment_records[1]["narrationText"],
            "AI8video，全球首款AI跨语言私域社交平台。支持180多种语言实时翻译，不会外语也能全球畅聊。 "
            "未来最赚钱的平台，一定是生态平台。今天开始，你也能拥有",
        )
        local_tts_text = result.jobs[0].usage["localTtsNarrationText"]
        self.assertIn("刚刚体验完，我真的被震惊了", local_tts_text)
        self.assertIn("今天开始，你也能拥有", local_tts_text)
        self.assertNotIn("最后一秒尽可能全身正对着镜头", local_tts_text)
        self.assertNotIn("音效", local_tts_text)

    def test_merge2_local_tts_narration_uses_ai_extractor_before_fallback(self) -> None:
        calls: list[str] = []

        def llm(prompt: str) -> str:
            calls.append(prompt)
            if "TTS 台词抽取模型" in prompt:
                if "不会英语的人，机会来了" in prompt:
                    return '{"narration_text":"不会英语的人，机会来了。"}'
                return '{"narration_text":"一款AI跨语言私域社交平台即将全球发布。"}'
            if "合并视频分段提取模型" in prompt:
                return (
                    '{"segment1_prompt":"镜头一（0-5s）：中景，女性在办公室。'
                    '画外音（平静略带激动）：“不会英语的人，机会来了。”背景音轻快。\\n'
                    '镜头二（5-10s）：近景，女性拿起平板。'
                    '画外音：“一款AI跨语言私域社交平台即将全球发布。”音效科技感。",'
                    '"segment2_prompt":"镜头一（0-5s）：近景，女性拿起平板。'
                    '画外音：“一款AI跨语言私域社交平台即将全球发布。”音效科技感。\\n'
                    '镜头二（5-10s）：特写，女性对镜头微笑。'
                    '画外音：“一款AI跨语言私域社交平台即将全球发布。”音效科技感。"}'
                )
            return '{"narration_text":""}'

        pipeline = _build_pipeline()
        pipeline.llm = llm
        request = ParsedRequest(raw_text="生成一条", mode="single_prompt", duration_seconds=10)
        episodes = [EpisodePrompt(
            index=1,
            title="AI抽取台词",
            prompt=(
                "镜头一（0-5s）：中景，女性在办公室。"
                "画外音（平静略带激动）：“不会英语的人，机会来了。”背景音轻快。\n"
                "镜头二（5-10s）：近景，女性拿起平板。"
                "画外音：“一款AI跨语言私域社交平台即将全球发布。”音效科技感。\n"
                "镜头三（10-15s）：同一女性继续操作。"
                "画外音：“不会英语的人，机会来了。”背景音轻快。\n"
                "镜头四（15-20s）：女性对镜头微笑。"
                "画外音：“一款AI跨语言私域社交平台即将全球发布。”音效科技感。"
            ),
        )]

        with patch("ai8video.generation.merged_video_pipeline.finalize_episode_prompts", return_value=episodes), \
                _patch_postprocess():
            result = pipeline._run_final_episodes(
                request,
                episodes,
                segment_duration_seconds=10,
                progress_session_id="merge-tts-ai-extract",
            )

        text = result.jobs[0].usage["localTtsNarrationText"]
        self.assertIn("不会英语的人，机会来了", text)
        self.assertIn("一款AI跨语言私域社交平台即将全球发布", text)
        self.assertNotIn("背景音", text)
        self.assertNotIn("音效", text)
        self.assertTrue(any("TTS 台词抽取模型" in call for call in calls))

    def test_merge2_saves_completed_segment_to_recycle_bin_before_tempdir_cleanup(self) -> None:
        pipeline = _build_pipeline(client=_FailSecondPollClient())
        request = ParsedRequest(raw_text="生成一条", mode="single_prompt", duration_seconds=10)
        episodes = [EpisodePrompt(
            index=2,
            title="第二条",
            prompt=(
                "总时长20秒，由两个连续10秒片段组成。\n"
                "0-5秒：前半段开场。\n"
                "5-10秒：前半段推进。\n"
                "10-15秒：后半段承接。\n"
                "15-20秒：后半段收束。"
            ),
        )]
        captured: dict[str, object] = {}

        def fake_save_failed_video_task(*, episode, job=None, reason, videos, meta=None):
            video_paths = [Path(item) for item in videos]
            captured["episode"] = episode.index
            captured["reason"] = reason
            captured["videos"] = video_paths
            captured["meta"] = meta
            self.assertTrue(video_paths)
            self.assertTrue(all(path.is_file() for path in video_paths))
            return {"ok": True, "videoCount": len(video_paths)}

        with patch("ai8video.generation.merged_video_pipeline.finalize_episode_prompts", return_value=episodes), \
                patch("ai8video.generation.merged_video_pipeline.save_failed_video_task", side_effect=fake_save_failed_video_task), \
                _patch_postprocess():
            result = pipeline._run_final_episodes(
                request,
                episodes,
                segment_duration_seconds=10,
                progress_session_id="merge-save-partial",
            )

        self.assertEqual(result.jobs[0].status, "failed")
        self.assertEqual(captured["episode"], 2)
        self.assertIn("上游敏感信息失败", str(captured["reason"]))
        self.assertEqual(len(captured["videos"]), 1)
        self.assertEqual(captured["meta"]["source"], "merged-partial-failure")

    def test_merge4_creates_four_segments_with_tail_frame_chain(self) -> None:
        pipeline = _build_pipeline(segment_count=4)
        request = ParsedRequest(raw_text="生成一条", mode="single_prompt", duration_seconds=10)
        episodes = [EpisodePrompt(
            index=1,
            title="第一条",
            prompt=(
                "总时长40秒，由四个连续10秒片段组成。\n"
                "0-10秒：第一镜头开场。\n"
                "10-20秒：第二镜头承接。\n"
                "20-30秒：第三镜头转折。\n"
                "30-40秒：第四镜头落点。"
            ),
        )]

        with patch("ai8video.generation.merged_video_pipeline.finalize_episode_prompts", return_value=episodes), \
                _patch_postprocess():
            result = pipeline._run_final_episodes(
                request,
                episodes,
                segment_duration_seconds=10,
                progress_session_id="merge-four",
            )

        self.assertEqual(len(pipeline.client.created), 4)
        self.assertIsNone(pipeline.client.created[0]["firstFrameSource"])
        for index in range(1, 4):
            self.assertIn("临时媒体", str(pipeline.client.created[index]["firstFrameSource"]))
            self.assertIn(f"01-segment-{index}-tail.png", str(pipeline.client.created[index]["firstFrameSource"]))
        self.assertIn("第一镜头开场", pipeline.client.created[0]["prompt"])
        self.assertNotIn("第二镜头承接", pipeline.client.created[0]["prompt"])
        self.assertIn("第二镜头承接", pipeline.client.created[1]["prompt"])
        self.assertNotIn("第一镜头开场", pipeline.client.created[1]["prompt"])
        self.assertIn("第三镜头转折", pipeline.client.created[2]["prompt"])
        self.assertNotIn("第二镜头承接", pipeline.client.created[2]["prompt"])
        self.assertIn("第四镜头落点", pipeline.client.created[3]["prompt"])
        self.assertNotIn("第三镜头转折", pipeline.client.created[3]["prompt"])
        self.assertIn("镜头一（0-10s）", pipeline.client.created[1]["prompt"])
        self.assertNotIn("镜头二（5-10s）", pipeline.client.created[1]["prompt"])
        self.assertTrue(all(item["durationSeconds"] == 10 for item in pipeline.client.created))
        self.assertEqual(result.jobs[0].status, "succeeded")
        self.assertTrue(result.jobs[0].job_id.startswith("merge4-"))
        self.assertEqual(result.outcomes[0].meta["mergeMode"], "merge4")
        self.assertEqual(len(result.outcomes[0].meta["segmentRecords"]), 4)

    def test_merge4_local_tts_narration_uses_all_four_segment_dialogues(self) -> None:
        pipeline = _build_pipeline(segment_count=4)
        request = ParsedRequest(raw_text="生成一条", mode="single_prompt", duration_seconds=10)
        episodes = [EpisodePrompt(
            index=1,
            title="第一条",
            prompt=(
                "镜头一（0-10s）：中景。台词/口播：第一段台词。\n"
                "镜头二（10-20s）：近景。台词/口播：第二段台词。\n"
                "镜头三（20-30s）：远景。台词/口播：第三段台词。\n"
                "镜头四（30-40s）：特写。台词/口播：第四段台词。"
            ),
        )]

        with patch("ai8video.generation.merged_video_pipeline.finalize_episode_prompts", return_value=episodes), \
                _patch_postprocess():
            result = pipeline._run_final_episodes(
                request,
                episodes,
                segment_duration_seconds=10,
                progress_session_id="merge4-tts-text",
            )

        text = result.jobs[0].usage["localTtsNarrationText"]
        for expected in ("第一段台词", "第二段台词", "第三段台词", "第四段台词"):
            self.assertIn(expected, text)
        self.assertLess(text.index("第一段台词"), text.index("第四段台词"))
        self.assertEqual(len(result.outcomes[0].meta["segmentRecords"]), 4)

    def test_merge2_cleans_initial_transformed_reference_image_after_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output_dir = Path(tempdir) / "i2i"
            pipeline = _build_pipeline()
            preprocessor = _TempTransformedReferencePreprocessor(output_dir)
            pipeline.reference_image_preprocessor = preprocessor
            request = ParsedRequest(
                raw_text="生成一条",
                mode="single_prompt",
                duration_seconds=10,
                reference_image="/tmp/default.png",
                reference_image_transform_options={"autoChangeBackground": True},
            )
            episodes = [EpisodePrompt(
                index=1,
                title="第一条",
                prompt=(
                    "总时长20秒，由两个连续10秒片段组成。\n"
                    "0-5秒：前半段开场。\n"
                    "5-10秒：前半段推进。\n"
                    "10-15秒：后半段承接。\n"
                    "15-20秒：后半段收束。"
                ),
            )]

            with patch("ai8video.generation.merged_video_pipeline.finalize_episode_prompts", return_value=episodes), \
                    patch("ai8video.generation.reference_image_preprocessor.TRANSFORMED_REFERENCE_DIR", output_dir), \
                    _patch_postprocess():
                result = pipeline._run_final_episodes(
                    request,
                    episodes,
                    segment_duration_seconds=10,
                    progress_session_id="merge-cleanup",
                )

            self.assertEqual(result.jobs[0].status, "succeeded")
            self.assertEqual(len(preprocessor.paths), 1)
            self.assertFalse(preprocessor.paths[0].exists())
            self.assertIn("临时媒体", str(pipeline.client.created[1]["firstFrameSource"]))
            self.assertTrue(Path(pipeline.client.created[1]["firstFrameSource"]).exists())

    def test_segment_prompt_builder_never_submits_full_final_prompt_to_each_segment(self) -> None:
        pipeline = _build_pipeline()
        final_prompt = (
            "总时长20秒，由两个连续10秒片段组成。\n"
            "0-5秒：A。\n"
            "5-10秒：B。\n"
            "10-15秒：C。\n"
            "15-20秒：D。"
        )

        segment1 = pipeline._build_segment_prompt(final_prompt, segment_index=1, segment_duration_seconds=10)
        segment2 = pipeline._build_segment_prompt(final_prompt, segment_index=2, segment_duration_seconds=10)

        self.assertIn("镜头一（0-5s）：A", segment1)
        self.assertIn("镜头二（5-10s）：B", segment1)
        self.assertNotIn("C。", segment1)
        self.assertNotIn("D。", segment1)
        self.assertIn("镜头一（0-5s）：C", segment2)
        self.assertIn("镜头二（5-10s）：D", segment2)
        self.assertNotIn("A。", segment2)
        self.assertNotIn("B。", segment2)
        self.assertNotIn("本片段是", segment1)
        self.assertNotIn("目标时长", segment2)
        self.assertNotIn("总时长20秒", segment1)
        self.assertNotIn("总时长20秒", segment2)

    def test_segment_prompt_builder_splits_aimanju_front_back_headings(self) -> None:
        pipeline = _build_pipeline()
        final_prompt = (
            "一条20秒的短视频，分为前10秒和后10秒，连续叙事。\n"
            "【前10秒（0-5秒）】\n"
            "- 镜头景别：近景仰拍。\n"
            "- 台词/口播：今天正式来了。\n"
            "【前10秒（5-10秒）】\n"
            "- 镜头景别：中近景。\n"
            "- 台词/口播：刚刚体验完。\n"
            "【后10秒（10-20秒）】\n"
            "- 镜头景别：全景。\n"
            "- 台词/口播：新时代已经开始。"
        )

        segment1 = pipeline._build_segment_prompt(final_prompt, segment_index=1, segment_duration_seconds=10)
        segment2 = pipeline._build_segment_prompt(final_prompt, segment_index=2, segment_duration_seconds=10)

        self.assertIn("镜头一（0-5s）：", segment1)
        self.assertIn("镜头二（5-10s）：", segment1)
        self.assertIn("今天正式来了", segment1)
        self.assertIn("刚刚体验完", segment1)
        self.assertNotIn("新时代已经开始", segment1)
        self.assertIn("镜头一（0-5s）：", segment2)
        self.assertIn("镜头二（5-10s）：", segment2)
        self.assertIn("新时代已经开始", segment2)
        self.assertNotIn("今天正式来了", segment2)
        self.assertNotIn("刚刚体验完", segment2)
        self.assertNotIn("【前10秒", segment2)
        self.assertNotIn("【后10秒", segment2)

    def test_segment_prompt_builder_splits_inline_time_blocks(self) -> None:
        pipeline = _build_pipeline()
        final_prompt = (
            "镜头一（0-5s）：前半段开场。"
            "镜头二（5-10s）：前半段推进。"
            "镜头三（10-15s）：后半段承接。"
            "镜头四（15-20s）：后半段收束。"
        )

        segment1 = pipeline._build_segment_prompt(final_prompt, segment_index=1, segment_duration_seconds=10)
        segment2 = pipeline._build_segment_prompt(final_prompt, segment_index=2, segment_duration_seconds=10)

        self.assertIn("前半段开场", segment1)
        self.assertIn("前半段推进", segment1)
        self.assertNotIn("后半段承接", segment1)
        self.assertNotIn("后半段收束", segment1)
        self.assertIn("镜头一（0-5s）：后半段承接", segment2)
        self.assertIn("镜头二（5-10s）：后半段收束", segment2)
        self.assertNotIn("前半段开场", segment2)
        self.assertNotIn("前半段推进", segment2)

    def test_segment_prompt_builder_applies_no_person_guard(self) -> None:
        pipeline = _build_pipeline()
        final_prompt = (
            "镜头一（0-5s）：美女身材特写。画外音：“地铁恢复后，客户通知需要统一。”"
            "镜头二（5-10s）：人物全身正对镜头。画外音：“AI8video 让多语言通知保持一致。”"
        )

        segment = pipeline._build_segment_prompt(
            final_prompt,
            segment_index=1,
            segment_duration_seconds=10,
            task_constraints="本次明确覆盖工具栏用户设置。安全过滤：无人物、无人脸、无身体部位。",
        )

        self.assertIn("无人物、无人脸、无身体部位", segment)
        self.assertIn("地铁恢复后，客户通知需要统一", segment)
        self.assertNotIn("美女", segment)
        self.assertNotIn("身材", segment)
        self.assertNotIn(TAIL_FRAME_PROMPT_SUFFIX, segment)

    def test_custom_safety_marker_builds_task_constraints(self) -> None:
        raw_text = "当次安全过滤：本轮不要求人物出镜，不出现人脸或身体特写。"

        constraints = AI8VideoPipeline._custom_input_task_constraints(raw_text)

        self.assertIsNotNone(constraints)
        self.assertIn("补充约束", constraints or "")
        self.assertIn("不得删除、替换、弱化或反转", constraints or "")
        self.assertIn("无人物", constraints or "")

    def test_custom_safety_requires_explicit_toolbar_override_marker(self) -> None:
        raw_text = "当次安全过滤：本轮无人物。本次覆盖工具栏设置。"

        constraints = AI8VideoPipeline._custom_input_task_constraints(raw_text)

        self.assertIn("本次明确覆盖工具栏用户设置", constraints or "")

    def test_planning_text_requires_full_story_and_detailed_back_half(self) -> None:
        text = AI8VideoMergedPipeline._planning_text("生成一条", 10)

        self.assertIn("先把 0-20 秒（也就是整条 1-20 秒观感）作为同一条完整成片来规划", text)
        self.assertIn("按同一集的四个连续镜头来写", text)
        self.assertIn("前两个镜头合成片段 1，后两个镜头合成片段 2", text)
        self.assertIn("镜头三（10-15s）", text)
        self.assertIn("镜头四（15-20s）", text)
        self.assertIn("口播时长源头约束", text)
        self.assertIn("不要等后置 TTS 再压缩正文", text)

    def test_merge4_planning_text_keeps_four_lenses_one_per_segment(self) -> None:
        text = AI8VideoMergedPipeline._planning_text("生成一条", 10, segment_count=4)

        self.assertIn("先把 0-40 秒（也就是整条 1-40 秒观感）作为同一条完整成片来规划", text)
        self.assertIn("四个镜头分别对应片段 1 到片段 4，一个镜头就是一个视频片段", text)
        self.assertIn("镜头一（0-10s）", text)
        self.assertIn("镜头二（10-20s）", text)
        self.assertIn("镜头三（20-30s）", text)
        self.assertIn("镜头四（30-40s）", text)
        self.assertNotIn("镜头五", text)

    def test_merge2_uses_ai_segment_extractor_before_rule_fallback(self) -> None:
        def llm(_prompt: str) -> str:
            return (
                '{"segment1_prompt":"0-5秒：AI 提取的前半段开场，只讲正式发布。\\n5-10秒：AI 提取的前半段推进，只讲体验震撼。",'
                '"segment2_prompt":"0-5秒：AI 提取的后半段承接，只讲拥抱世界。\\n5-10秒：AI 提取的后半段收束，只讲未来智能。",'
                '"notes":"已按前后两段提取"}'
            )

        pipeline = _build_pipeline()
        pipeline.llm = llm
        request = ParsedRequest(raw_text="生成一条", mode="single_prompt", duration_seconds=10)
        episodes = [EpisodePrompt(
            index=1,
            title="第一条",
            prompt=(
                "一条20秒的短视频，分为前10秒和后10秒，连续叙事。\n"
                "【前10秒（0-5秒）】\n"
                "- 台词/口播：今天正式来了。\n"
                "【后10秒（10-20秒）】\n"
                "- 台词/口播：未来的软件越来越智能。"
            ),
        )]

        with patch("ai8video.generation.merged_video_pipeline.finalize_episode_prompts", return_value=episodes), \
                _patch_postprocess():
            pipeline._run_final_episodes(
                request,
                episodes,
                segment_duration_seconds=10,
                progress_session_id="merge-ai-segment",
            )

        self.assertEqual(len(pipeline.client.created), 2)
        self.assertIn("AI 提取的前半段", pipeline.client.created[0]["prompt"])
        self.assertNotIn("AI 提取的后半段", pipeline.client.created[0]["prompt"])
        self.assertIn("AI 提取的后半段", pipeline.client.created[1]["prompt"])
        self.assertNotIn("AI 提取的前半段", pipeline.client.created[1]["prompt"])
        self.assertIn("镜头一（0-5s）：", pipeline.client.created[1]["prompt"])
        self.assertIn("镜头二（5-10s）：", pipeline.client.created[1]["prompt"])
        self.assertNotIn("本片段是同一集合并视频", pipeline.client.created[0]["prompt"])
        self.assertNotIn("本片段是同一集合并视频", pipeline.client.created[1]["prompt"])

    def test_merge2_does_not_rewrite_extracted_segments_as_complete_videos(self) -> None:
        model_inputs = []

        def llm(prompt: str) -> str:
            model_inputs.append(prompt)
            if "合并视频分段提取模型" in prompt:
                return """
                {
                  "segment1_prompt": "镜头一（0-5s）：客户发来一张资料图。台词/口播：这张图是真的吗？\\n镜头二（5-10s）：团队开始核对图片来源。台词/口播：大家先别急着转发。",
                  "segment2_prompt": "镜头一（0-5s）：团队统一确认资料。台词/口播：确认后再同步给所有人。\\n镜头二（5-10s）：跨语言成员收到同一结论。台词/口播：AI8video 让沟通更稳更准。",
                  "notes": "按原文提取"
                }
                """
            raise AssertionError("分段提取后不应再次调用 LLM 改写")

        pipeline = _build_pipeline()
        pipeline.llm = llm
        request = ParsedRequest(raw_text="生成一条", mode="single_prompt", duration_seconds=10)
        episodes = [EpisodePrompt(
            index=1,
            title="第一条",
            prompt="一条已经完成整稿终审的连续 20 秒视频提示词。",
        )]

        with patch("ai8video.generation.merged_video_pipeline.finalize_episode_prompts", return_value=episodes), \
                _patch_postprocess():
            pipeline._run_final_episodes(
                request,
                episodes,
                segment_duration_seconds=10,
                progress_session_id="merge-segment-source-locked",
            )

        self.assertEqual(len(pipeline.client.created), 2)
        self.assertEqual(sum("合并视频分段提取模型" in item for item in model_inputs), 1)
        first_prompt = pipeline.client.created[0]["prompt"]
        second_prompt = pipeline.client.created[1]["prompt"]
        self.assertIn("这张图是真的吗", first_prompt)
        self.assertNotIn("AI8video 让沟通更稳更准", first_prompt)
        self.assertIn("AI8video 让沟通更稳更准", second_prompt)
        self.assertNotIn("这张图是真的吗", second_prompt)
        self.assertIn(TAIL_FRAME_PROMPT_SUFFIX, first_prompt)
        self.assertIn(TAIL_FRAME_PROMPT_SUFFIX, second_prompt)

    def test_merge2_falls_back_to_rule_split_when_ai_segment_extractor_fails(self) -> None:
        def llm(_prompt: str) -> str:
            raise RuntimeError("extractor down")

        pipeline = _build_pipeline()
        pipeline.llm = llm
        request = ParsedRequest(raw_text="生成一条", mode="single_prompt", duration_seconds=10)
        episodes = [EpisodePrompt(
            index=1,
            title="第一条",
            prompt=(
                "总时长20秒，由两个连续10秒片段组成。\n"
                "0-5秒：A。\n"
                "5-10秒：B。\n"
                "10-15秒：C。\n"
                "15-20秒：D。"
            ),
        )]

        with patch("ai8video.generation.merged_video_pipeline.finalize_episode_prompts", return_value=episodes), \
                _patch_postprocess():
            pipeline._run_final_episodes(
                request,
                episodes,
                segment_duration_seconds=10,
                progress_session_id="merge-ai-fallback",
            )

        self.assertIn("镜头一（0-5s）：A", pipeline.client.created[0]["prompt"])
        self.assertNotIn("C。", pipeline.client.created[0]["prompt"])
        self.assertIn("镜头一（0-5s）：C", pipeline.client.created[1]["prompt"])
        self.assertNotIn("A。", pipeline.client.created[1]["prompt"])

    def test_tail_frame_failure_returns_merge_failure_without_asset_record(self) -> None:
        pipeline = _build_pipeline()
        request = ParsedRequest(raw_text="生成一条", mode="single_prompt", duration_seconds=10)
        episodes = [EpisodePrompt(index=1, title="第一条", prompt="视频提示词")]

        def materialize(job, work_dir, *, name, dry_run=False, duration_seconds=1, timeout_seconds=180):
            path = Path(work_dir) / f"{name}.mp4"
            path.write_bytes(b"segment")
            return path

        with patch("ai8video.generation.merged_video_pipeline.finalize_episode_prompts", return_value=episodes), \
                patch("ai8video.generation.merged_video_pipeline.materialize_segment_video", side_effect=materialize), \
                patch("ai8video.generation.merged_video_pipeline.extract_tail_frame", side_effect=RuntimeError("尾帧失败")), \
                patch("ai8video.generation.merged_video_pipeline.save_failed_video_task") as save_failed:
            result = pipeline._run_final_episodes(
                request,
                episodes,
                segment_duration_seconds=10,
                progress_session_id="merge-fail",
            )

        self.assertEqual(result.jobs[0].status, "failed")
        self.assertIn("尾帧失败", result.jobs[0].error or "")
        self.assertEqual(result.asset_records, [])
        self.assertEqual(len(pipeline.asset_store.records), 0)
        save_failed.assert_called_once()

    def test_concurrent_final_groups_keep_each_group_segments_sequential(self) -> None:
        barrier = threading.Barrier(2)
        client = _BarrierClient(barrier)
        pipeline = _build_pipeline(client=client)
        request = ParsedRequest(
            raw_text="生成两条",
            mode="multi_episode_script",
            episode_count=2,
            duration_seconds=10,
            concurrent_generation=True,
        )
        episodes = [
            EpisodePrompt(index=1, title="第一条", prompt="提示词一"),
            EpisodePrompt(index=2, title="第二条", prompt="提示词二"),
        ]

        with patch("ai8video.generation.merged_video_pipeline.finalize_episode_prompts", return_value=episodes), \
                _patch_postprocess():
            result = pipeline._run_final_episodes(
                request,
                episodes,
                segment_duration_seconds=10,
                progress_session_id="merge-concurrent",
            )

        self.assertEqual([job.episode_index for job in result.jobs], [1, 2])
        per_episode_sources: dict[int, list[str | None]] = {}
        for item in client.created:
            per_episode_sources.setdefault(item["episodeIndex"], []).append(item["firstFrameSource"])
        self.assertEqual(set(per_episode_sources), {1, 2})
        for sources in per_episode_sources.values():
            self.assertEqual(len(sources), 2)
            self.assertIsNone(sources[0])
            self.assertIsNotNone(sources[1])

    def test_cancelled_merge2_stops_before_second_segment(self) -> None:
        session_id = "merge-cancel-before-segment2"
        client = _CancelAfterFirstPollClient(session_id)
        pipeline = _build_pipeline(client=client)
        request = ParsedRequest(raw_text="生成一条", mode="single_prompt", duration_seconds=10)
        episodes = [EpisodePrompt(index=1, title="第一条", prompt="视频提示词")]

        with patch("ai8video.generation.merged_video_pipeline.finalize_episode_prompts", return_value=episodes), \
                _patch_postprocess():
            result = pipeline._run_final_episodes(
                request,
                episodes,
                segment_duration_seconds=10,
                progress_session_id=session_id,
            )

        self.assertEqual(len(client.created), 1)
        self.assertEqual(result.jobs[0].status, "skipped")
        self.assertEqual(result.asset_records, [])
        self.assertEqual(pipeline.asset_store.records, [])
        progress = get_generation_progress(session_id)
        self.assertEqual(progress["status"], "cancelled")
        self.assertEqual(progress["items"][0]["status"], "skipped")

    def test_cancelled_progress_cannot_be_rewritten_by_late_polling_update(self) -> None:
        session_id = "merge-cancel-sticky"
        episode = EpisodePrompt(index=1, title="第一条", prompt="视频提示词")
        start_generation_progress(session_id, [episode])
        cancel_generation_progress(session_id, "用户强行终止")

        mark_job_polling(
            session_id,
            QuickVideoJob(
                episode_index=1,
                job_id="late-job",
                status="pending",
                prompt="视频提示词",
                provider_progress=30,
            ),
        )

        progress = get_generation_progress(session_id)
        self.assertEqual(progress["status"], "cancelled")
        self.assertEqual(progress["items"][0]["status"], "skipped")
        self.assertEqual(progress["items"][0]["statusLabel"], "已取消")
        self.assertNotEqual(progress["items"][0].get("jobId"), "late-job")


if __name__ == "__main__":
    unittest.main()
