from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from ai8video.batch.specialist_agent_observer import (
    record_planner_execution,
    observe_reviewer_shadow,
)
from ai8video.generation.video_prompt_planner import (
    LLMCallable,
    infer_smart_video_count_with_ai,
    plan_video_prompts_with_ai,
    single_prompt_to_video,
)
from ai8video.generation.business_prompt import (
    _apply_custom_safety_guard,
    _custom_safety_requires_no_person,
    finalize_video_prompts,
    read_business_prompt,
)
from ai8video.core.config import AI8VideoConfig
from ai8video.media.ffmpeg_utils import probe_media_duration_seconds
from ai8video.generation.generation_progress import (
    GenerationCancelled,
    fail_generation_progress,
    finish_generation_progress,
    generation_stop_reason,
    is_generation_stopped,
    mark_job_archiving,
    mark_job_failed,
    mark_job_polling,
    mark_job_preparing_first_frame,
    mark_job_submitted,
    mark_job_submitting,
    mark_job_succeeded,
    start_generation_progress,
)
from ai8video.core.models import ArchivedAsset, VideoPrompt, FirstFrameAsset, GenerationOutcome, ParsedRequest, PipelineResult, QuickVideoJob
from ai8video.generation.pipeline import AI8VideoPipeline
from ai8video.generation.prompt_trace import append_prompt_trace
from ai8video.generation.tail_frame_chaining import (
    append_tail_frame_chain_prompt,
    build_next_tail_frame_request,
)
from ai8video.media.local_tts import extract_dialogue_text, prepare_narration_text
from ai8video.media.narration_review import narration_review_status, review_narration_text
from ai8video.assets.user_files import USER_FILE_ROOT
from ai8video.assets.user_recycle_bin import save_failed_video_task
from ai8video.media.video_segment_postprocess import concat_videos, extract_tail_frame, materialize_segment_video
from ai8video.assets.video_asset_archiver import archive_with_progress
from ai8video.core.paths import PROJECT_ROOT


MERGE_TEMP_MEDIA_DIR = (USER_FILE_ROOT / "临时媒体" / "视频合并").resolve()
TAIL_FRAME_PROMPT_SUFFIX = "所有主体最后一秒尽可能全身正对着镜头。"
LENS_LABELS = ("镜头一", "镜头二", "镜头三", "镜头四")
TIME_BLOCK_RE = re.compile(
    r"(?m)^"
    r"(?P<label>\s*(?:[-*]\s*)?"
    r"(?:[【\[]?[^\n（(]{0,40}[（(])?"
    r"(?P<start>\d{1,3})\s*[-—~至到]\s*(?P<end>\d{1,3})\s*(?:秒|s|S)"
    r"(?:[）)][】\]]?|[】\]])?[ \t]*[：:]?)"
)
FRONT_BACK_HEADING_RE = re.compile(
    r"(?m)^"
    r"(?P<label>\s*(?:[-*]\s*)?[【\[]?(?P<part>前|后)\s*\d{1,3}\s*秒[】\]]?[ \t]*[：:]?)"
)


class AI8VideoMergedPipeline(AI8VideoPipeline):
    """Independent merged-video control flow; the normal pipeline does not branch into this class."""

    def __init__(
        self,
        config: AI8VideoConfig | None = None,
        llm: LLMCallable | None = None,
        *,
        segment_count: int = 2,
    ):
        super().__init__(config=config, llm=llm)
        self.segment_count = max(2, int(segment_count or 2))

    def run_request(self, request: ParsedRequest, *, progress_session_id: str | None = None) -> PipelineResult:
        videos = self.plan_request(
            request,
            progress_session_id=progress_session_id,
        )
        return self.run_planned_request(request, videos, progress_session_id=progress_session_id)

    def plan_request(
        self,
        request: ParsedRequest,
        *,
        progress_session_id: str | None = None,
        smart_split: bool = False,
    ) -> list[VideoPrompt]:
        _, videos, _ = self.plan_merged_request(
            request,
            progress_session_id=progress_session_id,
            smart_split=smart_split,
        )
        return videos

    def plan_merged_request(
        self,
        request: ParsedRequest,
        *,
        progress_session_id: str | None = None,
        smart_split: bool = False,
    ) -> tuple[ParsedRequest, list[VideoPrompt], int]:
        segment_duration = self._segment_duration_seconds(request)
        final_request = replace(request, duration_seconds=segment_duration * self.segment_count)
        planning_text = self._planning_text(request.raw_text, segment_duration, segment_count=self.segment_count)
        allow_mock_planning = self.config.dry_run
        task_constraints = self._merged_task_constraints(final_request, segment_duration)
        video_count = final_request.video_count
        if smart_split:
            video_count = infer_smart_video_count_with_ai(
                planning_text,
                llm=self.llm,
                duration_seconds=final_request.duration_seconds,
                trace_session_id=progress_session_id,
            )
        if smart_split or final_request.mode == "batch_videos":
            if not video_count:
                raise ValueError("video_count is required for video planning")
            videos = plan_video_prompts_with_ai(
                planning_text,
                video_count,
                final_request.style_hint,
                final_request.core_keywords,
                task_constraints=task_constraints,
                final_duration_seconds=final_request.duration_seconds,
                llm=self.llm,
                allow_mock=allow_mock_planning,
                trace_session_id=progress_session_id,
            )
        else:
            videos = single_prompt_to_video(planning_text, final_request.style_hint, final_request.core_keywords)
        record_planner_execution(
            videos,
            session_id=progress_session_id,
            source_stage="merged_planning_output",
            merge_mode=self._merge_mode,
        )
        return final_request, videos, segment_duration

    def run_planned_request(
        self,
        request: ParsedRequest,
        videos: list[VideoPrompt],
        *,
        progress_session_id: str | None = None,
    ) -> PipelineResult:
        segment_duration = self._segment_duration_seconds(request)
        final_request = replace(request, duration_seconds=segment_duration * self.segment_count)
        return self._run_final_videos(
            final_request,
            videos,
            segment_duration_seconds=segment_duration,
            progress_session_id=progress_session_id,
        )

    def _run_final_videos(
        self,
        request: ParsedRequest,
        videos: list[VideoPrompt],
        *,
        segment_duration_seconds: int,
        progress_session_id: str | None = None,
    ) -> PipelineResult:
        if not self.config.dry_run and self.client.guard.forced_duration_seconds > 0:
            segment_duration_seconds = self.client.guard.forced_duration_seconds
            request = replace(request, duration_seconds=segment_duration_seconds * self.segment_count)
        if not self.config.dry_run:
            self.client.guard.assert_can_create_count(len(videos) * self.segment_count)
        task_constraints = self._merged_task_constraints(request, segment_duration_seconds)

        start_generation_progress(
            progress_session_id,
            videos,
            concurrent=bool(request.concurrent_generation and not request.tail_frame_chaining and len(videos) > 1),
        )

        final_videos = finalize_video_prompts(
            videos,
            llm=getattr(self, "llm", None),
            trace_session_id=progress_session_id,
            task_constraints=task_constraints,
        )
        ordered_videos = sorted(final_videos, key=lambda item: item.index)
        if request.tail_frame_chaining:
            ordered_videos = [append_tail_frame_chain_prompt(video) for video in ordered_videos]
        observe_reviewer_shadow(
            ordered_videos,
            session_id=progress_session_id,
            review_source="deterministic_finalization",
            merge_mode=self._merge_mode,
        )
        for video in ordered_videos:
            self._trace_merged_final_video_prompt(request, video, progress_session_id, segment_duration_seconds)

        if request.concurrent_generation and not request.tail_frame_chaining and len(ordered_videos) > 1:
            results = self._run_groups_concurrently(
                request,
                ordered_videos,
                segment_duration_seconds=segment_duration_seconds,
                progress_session_id=progress_session_id,
            )
        else:
            results = []
            active_request = request
            with tempfile.TemporaryDirectory(prefix="ai8video-tail-chain-") as tail_dir:
                for position, video in enumerate(ordered_videos):
                    result = self._run_one_final_video(
                        active_request,
                        video,
                        segment_duration_seconds=segment_duration_seconds,
                        progress_session_id=progress_session_id,
                    )
                    results.append(result)
                    if request.tail_frame_chaining and position < len(ordered_videos) - 1:
                        active_request = build_next_tail_frame_request(
                            active_request,
                            result[0],
                            result[2],
                            Path(tail_dir) / f"video-{video.index}-tail.png",
                        )

        jobs = [item[0] for item in results]
        outcomes = [item[1] for item in results]
        archives = [item[2] for item in results]
        asset_records = [item[3] for item in results if item[3]]
        if len(results) >= len(ordered_videos):
            finish_generation_progress(progress_session_id)
        return PipelineResult(
            request=request,
            videos=ordered_videos,
            first_frame=None,
            jobs=jobs,
            outcomes=outcomes,
            archives=archives,
            asset_records=asset_records,
            dry_run=self.config.dry_run,
        )

    def _run_groups_concurrently(
        self,
        request: ParsedRequest,
        videos: list[VideoPrompt],
        *,
        segment_duration_seconds: int,
        progress_session_id: str | None,
    ) -> list[tuple[QuickVideoJob, GenerationOutcome, ArchivedAsset, dict | None]]:
        results_by_index: dict[int, tuple[QuickVideoJob, GenerationOutcome, ArchivedAsset, dict | None]] = {}
        with ThreadPoolExecutor(max_workers=len(videos)) as executor:
            future_map = {
                executor.submit(
                    self._run_one_final_video,
                    request,
                    video,
                    segment_duration_seconds=segment_duration_seconds,
                    progress_session_id=progress_session_id,
                ): video.index
                for video in videos
            }
            for future in as_completed(future_map):
                index = future_map[future]
                results_by_index[index] = future.result()
        return [results_by_index[video.index] for video in videos if video.index in results_by_index]

    def _run_one_final_video(
        self,
        request: ParsedRequest,
        video: VideoPrompt,
        *,
        segment_duration_seconds: int,
        progress_session_id: str | None,
    ) -> tuple[QuickVideoJob, GenerationOutcome, ArchivedAsset, dict | None]:
        segment_records: list[dict] = []
        segment_videos: list[Path] = []
        merged_path: Path | None = None
        first_frame: FirstFrameAsset | None = None
        task_constraints = self._merged_task_constraints(request, segment_duration_seconds)
        try:
            merge_mode = self._merge_mode
            with tempfile.TemporaryDirectory(prefix=f"ai8video-{merge_mode}-{video.index}-") as tempdir:
                work_dir = Path(tempdir)
                try:
                    self._raise_if_cancelled(progress_session_id)
                    first_frame = self._prepare_initial_first_frame(request, video, progress_session_id)
                    self._raise_if_cancelled(progress_session_id)
                    segment_prompts = self._build_segment_prompts(
                        video,
                        segment_duration_seconds=segment_duration_seconds,
                        progress_session_id=progress_session_id,
                        task_constraints=task_constraints,
                    )
                    self._raise_if_cancelled(progress_session_id)
                    next_first_frame = first_frame
                    completed_segments: list[QuickVideoJob] = []
                    for segment_index in range(1, self.segment_count + 1):
                        segment_video = self._segment_video(
                            video,
                            segment_index,
                            segment_duration_seconds=segment_duration_seconds,
                            progress_session_id=progress_session_id,
                            segment_prompt=segment_prompts.get(segment_index),
                            task_constraints=task_constraints,
                        )
                        segment = self._create_and_poll_segment(
                            request,
                            segment_video,
                            next_first_frame,
                            segment_duration_seconds,
                            progress_session_id,
                            label=f"片段 {segment_index}",
                            segment_index=segment_index,
                        )
                        completed_segments.append(segment)
                        segment_record = _job_record(
                            segment,
                            f"segment{segment_index}",
                            first_frame=next_first_frame if segment_index > 1 else None,
                        )
                        segment_record["segmentPrompt"] = segment_video.prompt
                        segment_record["narrationText"] = self._segment_narration_text(
                            segment_video.prompt,
                            video=video,
                            segment_index=segment_index,
                            progress_session_id=progress_session_id,
                        )
                        segment_records.append(segment_record)
                        self._raise_if_cancelled(progress_session_id)
                        segment_video = materialize_segment_video(
                            segment,
                            work_dir,
                            name=f"segment-{segment_index}",
                            dry_run=self.config.dry_run,
                            duration_seconds=segment_duration_seconds,
                            timeout_seconds=self.config.archive_download_timeout_seconds,
                        )
                        segment_videos.append(segment_video)
                        self._raise_if_cancelled(progress_session_id)

                        if segment_index >= self.segment_count:
                            break

                        mark_job_polling(
                            progress_session_id,
                            QuickVideoJob(
                                video_index=video.index,
                                job_id=segment.job_id,
                                status="pending",
                                prompt=video.prompt,
                                stage_label=f"提取片段 {segment_index} 尾帧中",
                                segment_index=segment_index,
                                segment_label=f"片段 {segment_index}",
                            ),
                        )
                        self._raise_if_cancelled(progress_session_id)
                        tail_frame = extract_tail_frame(segment_video, work_dir / f"segment-{segment_index}-tail.png")
                        visible_tail_frame = self._copy_tail_frame_to_user_temp(
                            tail_frame,
                            video=video,
                            segment_index=segment_index,
                            progress_session_id=progress_session_id,
                        )
                        segment_records[-1]["rawTailFramePath"] = str(tail_frame)
                        segment_records[-1]["tailFramePath"] = str(visible_tail_frame)
                        segment_records[-1]["tailFrameLifecycle"] = "user-visible-temp"
                        next_first_frame = FirstFrameAsset(
                            first_frame_image_url=str(visible_tail_frame),
                            source=str(visible_tail_frame),
                        )
                        mark_job_polling(progress_session_id, segment)
                        self._raise_if_cancelled(progress_session_id)

                    merged_path = work_dir / f"merged-{video.index}.mp4"
                    mark_job_polling(
                        progress_session_id,
                        QuickVideoJob(
                            video_index=video.index,
                            job_id=completed_segments[-1].job_id,
                            status="pending",
                            prompt=video.prompt,
                            stage_label="合并中",
                        ),
                    )
                    self._raise_if_cancelled(progress_session_id)
                    merge_meta = concat_videos(segment_videos, merged_path)
                    self._raise_if_cancelled(progress_session_id)
                    raw_local_tts_narration_text = _merged_local_tts_narration_text(segment_records, video)
                    local_tts_duration_fit = self._fit_local_tts_narration_to_duration(
                        raw_local_tts_narration_text,
                        video=video,
                        target_duration_seconds=_merged_video_duration_seconds(merged_path)
                        or (segment_duration_seconds * self.segment_count),
                        progress_session_id=progress_session_id,
                    )
                    local_tts_narration_text = local_tts_duration_fit["text"]
                    merged_job = QuickVideoJob(
                        video_index=video.index,
                        job_id=f"{merge_mode}-{'-'.join(segment.job_id for segment in completed_segments)}",
                        status="succeeded",
                        prompt=video.prompt,
                        storage_key=f"merged-video/{video.index}.mp4",
                        local_video_path=str(merged_path),
                        usage={
                            "mode": merge_mode,
                            "segmentDurationSeconds": segment_duration_seconds,
                            "finalDurationSeconds": segment_duration_seconds * self.segment_count,
                            "segments": segment_records,
                            "localTtsNarrationText": local_tts_narration_text,
                            "localTtsNarrationRawText": raw_local_tts_narration_text,
                            "localTtsNarrationDurationFit": {
                                key: value for key, value in local_tts_duration_fit.items() if key != "text"
                            },
                        },
                    )
                    outcome = GenerationOutcome(
                        video_index=video.index,
                        job_id=merged_job.job_id,
                        status="succeeded",
                        decision="generated",
                        reasons=[],
                        meta={
                            "kind": "merged_generation_outcome",
                            "mergeMode": merge_mode,
                            "segmentRecords": segment_records,
                            "merge": merge_meta,
                            "localTtsNarrationText": local_tts_narration_text,
                            "localTtsNarrationRawText": raw_local_tts_narration_text,
                            "localTtsNarrationDurationFit": {
                                key: value for key, value in local_tts_duration_fit.items() if key != "text"
                            },
                        },
                    )
                    mark_job_archiving(progress_session_id, merged_job)
                    self._raise_if_cancelled(progress_session_id)
                    archive = archive_with_progress(
                        self.archiver.archive_local_file,
                        merged_path,
                        request,
                        video=video,
                        job=merged_job,
                        outcome=outcome,
                        extra_meta={
                            "mergeMode": merge_mode,
                            "segmentRecords": segment_records,
                            "merge": merge_meta,
                            "localTtsNarrationText": local_tts_narration_text,
                            "localTtsNarrationRawText": raw_local_tts_narration_text,
                            "localTtsNarrationDurationFit": {
                                key: value for key, value in local_tts_duration_fit.items() if key != "text"
                            },
                        },
                        progress_session_id=progress_session_id,
                    )
                    asset_record = self.asset_store.append(request, video, merged_job, outcome, None, archive)
                    mark_job_succeeded(progress_session_id, merged_job, asset_record)
                    return merged_job, outcome, archive, asset_record
                except GenerationCancelled:
                    raise
                except Exception as exc:
                    self._save_failed_partial_videos(
                        video,
                        exc,
                        segment_videos=segment_videos,
                        merged_path=merged_path,
                        segment_records=segment_records,
                        progress_session_id=progress_session_id,
                    )
                    return self._merged_failure_result(
                        request,
                        video,
                        exc,
                        segment_records=segment_records,
                        progress_session_id=progress_session_id,
                    )
        except GenerationCancelled as exc:
            return self._merged_cancelled_result(
                request,
                video,
                exc,
                segment_records=segment_records,
                progress_session_id=progress_session_id,
            )
        except Exception as exc:
            self._save_failed_partial_videos(
                video,
                exc,
                segment_videos=segment_videos,
                merged_path=merged_path,
                segment_records=segment_records,
                progress_session_id=progress_session_id,
            )
            return self._merged_failure_result(
                request,
                video,
                exc,
                segment_records=segment_records,
                progress_session_id=progress_session_id,
            )
        finally:
            self._cleanup_transformed_first_frames([first_frame], progress_session_id)

    def _prepare_initial_first_frame(
        self,
        request: ParsedRequest,
        video: VideoPrompt,
        progress_session_id: str | None,
    ) -> FirstFrameAsset | None:
        mark_job_preparing_first_frame(progress_session_id, video)
        return self._prepare_video_first_frame(request, video, progress_session_id=progress_session_id)

    def _save_failed_partial_videos(
        self,
        video: VideoPrompt,
        exc: Exception,
        *,
        segment_videos: list[Path],
        merged_path: Path | None,
        segment_records: list[dict],
        progress_session_id: str | None,
    ) -> None:
        videos = [path for path in segment_videos if Path(path).is_file()]
        if merged_path and merged_path.is_file():
            videos.append(merged_path)
        if not videos:
            return
        reason = str(exc).strip() or exc.__class__.__name__
        job = QuickVideoJob(
            video_index=video.index,
            job_id=f"{self._merge_mode}-failed-{video.index}",
            status="failed",
            prompt=video.prompt,
            error=reason,
        )
        try:
            save_failed_video_task(
                video=video,
                job=job,
                reason=reason,
                videos=videos,
                meta={
                    "source": "merged-partial-failure",
                    "mergeMode": self._merge_mode,
                    "segmentRecords": segment_records,
                    "progressSessionId": progress_session_id,
                },
            )
        except Exception:
            return

    def _create_and_poll_segment(
        self,
        request: ParsedRequest,
        video: VideoPrompt,
        first_frame: FirstFrameAsset | None,
        segment_duration_seconds: int,
        progress_session_id: str | None,
        *,
        label: str,
        segment_index: int,
    ) -> QuickVideoJob:
        self._raise_if_cancelled(progress_session_id)
        mark_job_submitting(progress_session_id, video)
        self._raise_if_cancelled(progress_session_id)
        self._trace_video_submit(
            request,
            video,
            first_frame,
            progress_session_id,
            segment_label=label,
            duration_seconds=segment_duration_seconds,
        )
        job = self.client.create_job(
            text=video.prompt,
            video_index=video.index,
            first_frame=first_frame,
            duration_seconds=segment_duration_seconds,
            ratio=request.ratio,
            resolution=request.resolution,
            preset=request.preset,
        )
        job.segment_index = segment_index
        job.segment_label = label
        job.stage_label = f"{label}已提交"
        self._trace_video_job_created(
            request,
            video,
            job,
            progress_session_id,
            segment_label=label,
            duration_seconds=segment_duration_seconds,
        )
        self._raise_if_cancelled(progress_session_id)
        mark_job_submitted(progress_session_id, video, job)
        mark_job_polling(progress_session_id, job)
        completed = self._poll_job(job, progress_session_id)
        self._raise_if_cancelled(progress_session_id)
        if str(completed.status or "").strip().lower() not in {"succeeded", "completed"}:
            raise RuntimeError(completed.error or f"{label}生成失败")
        if not (completed.video_url or completed.local_video_path):
            raise RuntimeError(f"{label}没有返回可用视频")
        return completed

    def _merged_failure_result(
        self,
        request: ParsedRequest,
        video: VideoPrompt,
        exc: Exception,
        *,
        segment_records: list[dict],
        progress_session_id: str | None,
    ) -> tuple[QuickVideoJob, GenerationOutcome, ArchivedAsset, None]:
        error = str(exc).strip() or exc.__class__.__name__
        merge_mode = self._merge_mode
        job = QuickVideoJob(
            video_index=video.index,
            job_id=f"{merge_mode}-failed-{video.index}",
            status="failed",
            prompt=video.prompt,
            error=error,
            usage={"mode": merge_mode, "segmentRecords": segment_records},
        )
        outcome = GenerationOutcome(
            video_index=video.index,
            job_id=job.job_id,
            status="failed",
            decision="failed",
            reasons=[error],
            meta={
                "kind": "merged_generation_outcome",
                "mergeMode": merge_mode,
                "segmentRecords": segment_records,
            },
        )
        archive = ArchivedAsset(
            video_index=video.index,
            job_id=job.job_id,
            backend=self.config.archive_backend,
            status="failed",
            error=error,
            meta={"reason": "合并模式失败，不创建普通视频资产", "segmentRecords": segment_records},
        )
        mark_job_failed(progress_session_id, video.index, error, job_id=job.job_id)
        fail_generation_progress(progress_session_id, error, skip_pending=False)
        return job, outcome, archive, None

    def _merged_cancelled_result(
        self,
        request: ParsedRequest,
        video: VideoPrompt,
        exc: Exception,
        *,
        segment_records: list[dict],
        progress_session_id: str | None,
    ) -> tuple[QuickVideoJob, GenerationOutcome, ArchivedAsset, None]:
        del progress_session_id
        reason = str(exc).strip() or "用户强行终止，本地停止等待结果回填"
        merge_mode = self._merge_mode
        job = QuickVideoJob(
            video_index=video.index,
            job_id=f"{merge_mode}-cancelled-{video.index}",
            status="skipped",
            prompt=video.prompt,
            error=reason,
            usage={"mode": merge_mode, "segmentRecords": segment_records},
        )
        outcome = GenerationOutcome(
            video_index=video.index,
            job_id=job.job_id,
            status="skipped",
            decision="failed",
            reasons=[reason],
            meta={
                "kind": "merged_generation_outcome",
                "mergeMode": merge_mode,
                "cancelled": True,
                "segmentRecords": segment_records,
            },
        )
        archive = ArchivedAsset(
            video_index=video.index,
            job_id=job.job_id,
            backend=self.config.archive_backend,
            status="skipped",
            error=reason,
            meta={"reason": "合并模式已强行终止，不创建普通视频资产", "segmentRecords": segment_records},
        )
        return job, outcome, archive, None

    @staticmethod
    def _raise_if_cancelled(progress_session_id: str | None) -> None:
        if is_generation_stopped(progress_session_id):
            raise GenerationCancelled(generation_stop_reason(progress_session_id))

    def _segment_video(
        self,
        video: VideoPrompt,
        segment_index: int,
        *,
        segment_duration_seconds: int,
        progress_session_id: str | None = None,
        segment_prompt: str | None = None,
        task_constraints: str | None = None,
    ) -> VideoPrompt:
        title = f"{video.title} · 片段 {segment_index}"
        raw_segment_prompt = segment_prompt or self._build_segment_prompt(
            video.prompt,
            segment_index=segment_index,
            segment_duration_seconds=segment_duration_seconds,
            split_single_block=self.segment_count == 2,
        )
        final_segment_prompt = raw_segment_prompt
        append_prompt_trace(
            "merged_segment_video_prompt",
            session_id=progress_session_id,
            payload={
                "videoIndex": video.index,
                "title": title,
                "segmentIndex": segment_index,
                "segmentDurationSeconds": segment_duration_seconds,
                "prompt": final_segment_prompt,
                "sourcePrompt": video.prompt,
            },
        )
        return VideoPrompt(
            index=video.index,
            title=title,
            prompt=final_segment_prompt,
            source_summary=video.source_summary,
            keyword_guidance={
                **(video.keyword_guidance or {}),
                "mergeMode": self._merge_mode,
                "segmentIndex": segment_index,
            },
        )

    @property
    def _merge_mode(self) -> str:
        return f"merge{self.segment_count}"

    def _trace_merged_final_video_prompt(
        self,
        request: ParsedRequest,
        video: VideoPrompt,
        progress_session_id: str | None,
        segment_duration_seconds: int,
    ) -> None:
        append_prompt_trace(
            "merged_final_video_prompt",
            session_id=progress_session_id,
            payload={
                "videoIndex": video.index,
                "title": video.title,
                "prompt": video.prompt,
                "segmentDurationSeconds": segment_duration_seconds,
                "finalDurationSeconds": segment_duration_seconds * self.segment_count,
                "mergeMode": self._merge_mode,
                "request": {
                    "mode": request.mode,
                    "videoCount": request.video_count,
                    "durationSeconds": request.duration_seconds,
                    "ratio": request.ratio,
                    "resolution": request.resolution,
                    "preset": request.preset,
                    "concurrentGeneration": request.concurrent_generation,
                },
            },
        )

    def _segment_duration_seconds(self, request: ParsedRequest) -> int:
        return self._effective_video_duration_seconds(request.duration_seconds)

    def _merged_task_constraints(self, request: ParsedRequest, segment_duration_seconds: int) -> str:
        return _join_constraint_blocks(
            self._reference_task_constraints(request),
            self._duration_task_constraints(segment_duration_seconds, self.segment_count),
        )

    @staticmethod
    def _duration_task_constraints(segment_duration_seconds: int, segment_count: int) -> str:
        segment_count = max(2, int(segment_count or 2))
        final_duration = max(1, int(segment_duration_seconds or 10)) * segment_count
        target_seconds = max(1, final_duration - 1)
        min_chars = max(20, target_seconds * 4)
        max_chars = max(min_chars + 10, target_seconds * 6)
        return (
            f"最终成片固定约 {final_duration} 秒，文本源头必须先写好能在约 {target_seconds} 秒内"
            f"自然读完的完整中文口播，建议 {min_chars}-{max_chars} 个汉字。"
            "口播必须有开场钩子、事件/痛点解释、转折承接和结尾落点；"
            "热点承接类内容要保留热点主体和因果，不得压成关键词或流水账。"
            "后置 TTS 只负责朗读和轻量语速适配，不能依赖后置 TTS 重写正文。"
        )

    @staticmethod
    def _planning_text(raw_text: str, segment_duration_seconds: int, *, segment_count: int = 2) -> str:
        segment_count = max(2, int(segment_count or 2))
        final_duration = segment_duration_seconds * segment_count
        lens_lines = []
        template_lines = []
        if segment_count == 2:
            midpoint = max(1, segment_duration_seconds // 2)
            lens_ranges = [
                (0, midpoint),
                (midpoint, segment_duration_seconds),
                (segment_duration_seconds, segment_duration_seconds + midpoint),
                (segment_duration_seconds + midpoint, segment_duration_seconds * 2),
            ]
            segment_mapping_text = "前两个镜头合成片段 1，后两个镜头合成片段 2"
        else:
            lens_ranges = [
                ((lens_index - 1) * segment_duration_seconds, lens_index * segment_duration_seconds)
                for lens_index in range(1, 5)
            ]
            segment_mapping_text = "四个镜头分别对应片段 1 到片段 4，一个镜头就是一个视频片段"
        for lens_index, (start, end) in enumerate(lens_ranges, start=1):
            lens_label = _lens_label(lens_index)
            lens_lines.append(f"{lens_label}（{start}-{end}s）")
            template_lines.append(f"{lens_label}（{start}-{end}s）：...")
        first_segment_count_text = "前后两个片段" if segment_count == 2 else f"{segment_count} 个片段"
        return (
            f"{raw_text}\n\n"
            f"合并模式规划要求：每个最终视频总时长约 {final_duration} 秒，"
            f"由 {segment_count} 个连续的 {segment_duration_seconds} 秒片段组成；规划时按最终成片节奏设计。"
            f"先把 0-{final_duration} 秒（也就是整条 1-{final_duration} 秒观感）作为同一条完整成片来规划，再写内部时间轴；"
            f"不要把{first_segment_count_text}规划成独立短视频。每个后续片段必须能接住上一片段最后一帧继续发展。\n"
            f"每条最终视频必须按同一条完整视频的四个连续镜头来写，不要写成多个独立小视频；{segment_mapping_text}："
            f"{'、'.join(lens_lines)}。"
            f"所有镜头都必须有镜头景别、场景描述、运镜动作、人物动作、台词/口播、音效和情绪推进，不能只写成一句整体收束。\n"
            f"口播时长源头约束：最终成片约 {final_duration} 秒，必须在文本生成阶段就写好能自然读完的完整口播；"
            f"不要等后置 TTS 再压缩正文。口播要有开场、事件解释、转折和落点，不能写成流水账短句。\n"
            f"输出每条视频提示词时请统一使用以下结构，方便后续文本模型准确提取 {segment_count} 个视频片段：\n"
            f"{chr(10).join(template_lines)}"
        )

    def _build_segment_prompts(
        self,
        video: VideoPrompt,
        *,
        segment_duration_seconds: int,
        progress_session_id: str | None,
        task_constraints: str | None = None,
    ) -> dict[int, str]:
        if self.llm is not None:
            try:
                return self._build_segment_prompts_with_ai(
                    video,
                    segment_duration_seconds=segment_duration_seconds,
                    progress_session_id=progress_session_id,
                    task_constraints=task_constraints,
                )
            except Exception as exc:
                append_prompt_trace(
                    "merged_segment_extract_model_error",
                    session_id=progress_session_id,
                    payload={
                        "videoIndex": video.index,
                        "errorType": exc.__class__.__name__,
                        "error": str(exc),
                    },
                )
        return {
            1: self._build_segment_prompt(
                video.prompt,
                segment_index=1,
                segment_duration_seconds=segment_duration_seconds,
                split_single_block=self.segment_count == 2,
                task_constraints=task_constraints,
            ),
            **{
                segment_index: self._build_segment_prompt(
                    video.prompt,
                    segment_index=segment_index,
                    segment_duration_seconds=segment_duration_seconds,
                    split_single_block=self.segment_count == 2,
                    task_constraints=task_constraints,
                )
                for segment_index in range(2, self.segment_count + 1)
            },
        }

    def _build_segment_prompts_with_ai(
        self,
        video: VideoPrompt,
        *,
        segment_duration_seconds: int,
        progress_session_id: str | None,
        task_constraints: str | None = None,
    ) -> dict[int, str]:
        model_prompt = self._build_segment_extraction_prompt(
            video,
            segment_duration_seconds,
            segment_count=self.segment_count,
        )
        append_prompt_trace(
            "merged_segment_extract_model_input",
            session_id=progress_session_id,
            payload={
                "videoIndex": video.index,
                "title": video.title,
                "segmentDurationSeconds": segment_duration_seconds,
                "prompt": model_prompt,
            },
        )
        raw = self.llm(model_prompt)
        append_prompt_trace(
            "merged_segment_extract_model_output",
            session_id=progress_session_id,
            payload={
                "videoIndex": video.index,
                "raw": raw,
            },
        )
        data = _parse_json_object(raw)
        segment_prompts: dict[int, str] = {}
        for segment_index in range(1, self.segment_count + 1):
            raw_segment = str(
                data.get(f"segment{segment_index}_prompt")
                or data.get(f"segment{segment_index}")
                or ""
            ).strip()
            self._validate_ai_segment_body(
                raw_segment,
                segment_index=segment_index,
                source_prompt=video.prompt,
                min_time_blocks=2 if self.segment_count == 2 else 1,
            )
            wrapped = self._wrap_segment_body(
                    self._normalize_local_segment_body(raw_segment),
                    segment_index=segment_index,
                    segment_duration_seconds=segment_duration_seconds,
                )
            guarded = _apply_custom_safety_guard(wrapped, task_constraints)
            segment_prompts[segment_index] = self._append_tail_frame_instruction(
                guarded,
                task_constraints=task_constraints,
            )
        return segment_prompts

    def _segment_narration_text(
        self,
        prompt: str,
        *,
        video: VideoPrompt,
        segment_index: int,
        progress_session_id: str | None,
    ) -> str:
        candidate = self._extract_segment_narration(
            prompt,
            video=video,
            segment_index=segment_index,
            progress_session_id=progress_session_id,
        )
        return self._review_segment_narration(
            prompt,
            candidate,
            video=video,
            segment_index=segment_index,
            progress_session_id=progress_session_id,
        )

    def _extract_segment_narration(
        self,
        prompt: str,
        *,
        video: VideoPrompt,
        segment_index: int,
        progress_session_id: str | None,
    ) -> str:
        if self.llm is not None:
            try:
                model_prompt = self._build_narration_extraction_prompt(
                    prompt,
                    video=video,
                    segment_index=segment_index,
                )
                append_prompt_trace(
                    "local_tts_narration_model_input",
                    session_id=progress_session_id,
                    payload={
                        "videoIndex": video.index,
                        "segmentIndex": segment_index,
                        "title": video.title,
                        "prompt": model_prompt,
                    },
                )
                raw = self.llm(model_prompt)
                append_prompt_trace(
                    "local_tts_narration_model_output",
                    session_id=progress_session_id,
                    payload={
                        "videoIndex": video.index,
                        "segmentIndex": segment_index,
                        "raw": raw,
                    },
                )
                data = _parse_json_object(raw)
                text = str(
                    data.get("narration_text")
                    or data.get("dialogue")
                    or data.get("spoken_text")
                    or ""
                ).strip()
                cleaned = prepare_narration_text(text)
                if cleaned:
                    return cleaned
            except Exception as exc:
                append_prompt_trace(
                    "local_tts_narration_model_error",
                    session_id=progress_session_id,
                    payload={
                        "videoIndex": video.index,
                        "segmentIndex": segment_index,
                        "errorType": exc.__class__.__name__,
                        "error": str(exc),
                    },
                )
        return _segment_narration_text(prompt)

    def _review_segment_narration(
        self,
        prompt: str,
        candidate: str,
        *,
        video: VideoPrompt,
        segment_index: int,
        progress_session_id: str | None,
    ) -> str:
        review_count = int(narration_review_status()["reviewCount"])
        if review_count <= 0 or not candidate:
            return candidate
        if self.llm is None:
            return ""
        try:
            result = review_narration_text(
                self.llm,
                video_prompt=prompt,
                candidate_text=candidate,
                business_prompt=read_business_prompt(),
                review_count=review_count,
            )
            append_prompt_trace(
                "local_tts_narration_review",
                session_id=progress_session_id,
                payload={
                    "videoIndex": video.index,
                    "segmentIndex": segment_index,
                    **result,
                },
            )
            return str(result.get("text") or "").strip() if result.get("passes") else ""
        except Exception as exc:
            append_prompt_trace(
                "local_tts_narration_review_error",
                session_id=progress_session_id,
                payload={
                    "videoIndex": video.index,
                    "segmentIndex": segment_index,
                    "errorType": exc.__class__.__name__,
                    "error": str(exc),
                },
            )
            return ""

    @staticmethod
    def _build_narration_extraction_prompt(
        prompt: str,
        *,
        video: VideoPrompt,
        segment_index: int,
    ) -> str:
        return f"""你是AI8video 的 TTS 台词抽取模型。

任务：从视频模型子提示词中抽取“应该被 TTS 读出来的台词/口播/旁白/画外音/解说正文”。

判断原则：
1. 只抽取观众应该听到的人声内容，保持原句顺序和标点。
2. 不要读镜头说明、景别、人物动作、表情、运镜、情绪语气、音效、环境声、背景音乐、配乐、BGM 或制作要求。
3. 如果候选里有引号内台词，优先只取引号内正文；如果没有引号，再根据语义抽取明确的口播正文。
4. 不要新增、润色或改写台词；只能删除不该读的制作说明。
5. 如果没有可读台词，返回空字符串。

只返回严格 JSON 对象，不要解释。格式：
{{
  "narration_text": "TTS 应该读出的完整正文"
}}

当前视频序号：{video.index}
当前标题：{video.title}
当前片段：{segment_index}

视频模型子提示词：
{prompt}
"""

    def _fit_local_tts_narration_to_duration(
        self,
        text: str,
        *,
        video: VideoPrompt,
        target_duration_seconds: float | int | None,
        progress_session_id: str | None,
        allow_model_rewrite: bool = False,
    ) -> dict[str, Any]:
        cleaned = prepare_narration_text(text)
        target = _clean_duration_seconds(target_duration_seconds)
        if not cleaned:
            return {
                "text": "",
                "status": "skipped",
                "reason": "empty narration text",
                "targetDurationSeconds": target,
            }
        if not allow_model_rewrite:
            return {
                "text": cleaned,
                "status": "source_locked",
                "targetDurationSeconds": target,
                "notes": "口播已在文本源头按最终视频时长规划，后置 TTS 不改写正文。",
            }
        if self.llm is None:
            return {
                "text": cleaned,
                "status": "skipped",
                "reason": "llm unavailable",
                "targetDurationSeconds": target,
            }
        try:
            model_prompt = self._build_tts_duration_fit_prompt(
                cleaned,
                video=video,
                target_duration_seconds=target,
            )
            append_prompt_trace(
                "local_tts_duration_fit_model_input",
                session_id=progress_session_id,
                payload={
                    "videoIndex": video.index,
                    "title": video.title,
                    "targetDurationSeconds": target,
                    "prompt": model_prompt,
                },
            )
            raw = self.llm(model_prompt)
            append_prompt_trace(
                "local_tts_duration_fit_model_output",
                session_id=progress_session_id,
                payload={
                    "videoIndex": video.index,
                    "raw": raw,
                },
            )
            data = _parse_json_object(raw)
            fitted = prepare_narration_text(
                data.get("narration_text")
                or data.get("spoken_text")
                or data.get("dialogue")
                or ""
            )
            if not fitted:
                raise ValueError("duration fit model returned empty narration_text")
            return {
                "text": fitted,
                "status": "model_accepted" if fitted == cleaned else "model_adjusted",
                "targetDurationSeconds": target,
                "estimatedSeconds": _optional_float(data.get("estimated_seconds")),
                "notes": str(data.get("notes") or "").strip(),
            }
        except Exception as exc:
            append_prompt_trace(
                "local_tts_duration_fit_model_error",
                session_id=progress_session_id,
                payload={
                    "videoIndex": video.index,
                    "errorType": exc.__class__.__name__,
                    "error": str(exc),
                },
            )
            return {
                "text": cleaned,
                "status": "model_error",
                "reason": str(exc),
                "targetDurationSeconds": target,
            }

    @staticmethod
    def _build_tts_duration_fit_prompt(
        text: str,
        *,
        video: VideoPrompt,
        target_duration_seconds: float,
    ) -> str:
        return f"""你是AI8video 的 TTS 时长校准模型。

任务：检查并必要时改写已抽取的 TTS 台词，让它能在目标视频时长内自然读完。

判断原则：
1. 只输出观众应该听到的人声正文，不要加入镜头说明、音效、情绪标签、BGM、制作要求或解释。
2. 尽量保留原文卖点、顺序和语气；如果原文自然语速会超时，优先合并冗余表达、压缩重复卖点，而不是机械截断。
3. 如果原文已经适合目标时长，原样返回。
4. 不要新增事实、品牌、日期、数字或用户没有给出的承诺。
5. 输出文本必须适合直接交给 MiMo TTS 朗读。
6. 不得把因果叙事改成流水账短句；必须保留开场钩子、事件/痛点解释、转折承接和结尾落点。
7. 热点承接类台词必须保留热点事件主体、普通用户能理解的背景和自然承接，不得压成几个关键词或数字罗列。

只返回严格 JSON 对象，不要解释。格式：
{{
  "narration_text": "校准后的 TTS 正文",
  "estimated_seconds": 12.3,
  "notes": "一句话说明是否改写"
}}

当前视频序号：{video.index}
当前标题：{video.title}
目标视频时长：{target_duration_seconds:.2f} 秒

已抽取 TTS 台词：
{text}
"""

    @staticmethod
    def _build_segment_extraction_prompt(
        video: VideoPrompt,
        segment_duration_seconds: int,
        *,
        segment_count: int = 2,
    ) -> str:
        segment_count = max(2, int(segment_count or 2))
        final_duration = segment_duration_seconds * segment_count
        segment_rules = []
        json_lines = []
        half = max(1, segment_duration_seconds // 2)
        for segment_index in range(1, segment_count + 1):
            start = (segment_index - 1) * segment_duration_seconds
            end = segment_index * segment_duration_seconds
            comma = "," if segment_index < segment_count else ""
            if segment_count == 2:
                local_first_start = start
                local_first_end = start + half
                local_second_start = start + half
                local_second_end = end
                segment_rules.append(
                    f"{segment_index}. `segment{segment_index}_prompt` 只能包含最终视频 {start}-{end} 秒剧情；"
                    f"内部时间轴必须从 0 秒重新开始，例如原始 {local_first_start}-{local_first_end} 秒写成镜头一（0-{half}s），"
                    f"原始 {local_second_start}-{local_second_end} 秒写成镜头二（{half}-{segment_duration_seconds}s）。"
                )
                json_lines.append(
                    f'  "segment{segment_index}_prompt": "镜头一（0-{half}s）：...\\n镜头二（{half}-{segment_duration_seconds}s）：..."{comma}'
                )
            else:
                segment_rules.append(
                    f"{segment_index}. `segment{segment_index}_prompt` 只能包含最终视频 {start}-{end} 秒的第 {segment_index} 个镜头；"
                    f"内部时间轴必须从 0 秒重新开始，例如原始 {start}-{end} 秒写成镜头一（0-{segment_duration_seconds}s）。"
                )
                json_lines.append(
                    f'  "segment{segment_index}_prompt": "镜头一（0-{segment_duration_seconds}s）：..."{comma}'
                )
        rhythm_rule = (
            f"每个 `segmentN_prompt` 都必须保留内部镜头节奏，不能偷懒写成一句 0-{segment_duration_seconds} 秒整体概述；"
            f"如果原文某段只有整体描述，你要在不新增事实、不新增卖点的前提下，把已有动作、运镜、情绪和台词自然拆成镜头一（0-{half}s）、镜头二（{half}-{segment_duration_seconds}s）两个连续镜头。"
            if segment_count == 2 else
            f"每个 `segmentN_prompt` 只保留一个镜头，不要再拆成两个镜头；但这个镜头内部仍要保留景别、场景、运镜、人物动作、台词/口播、音效和情绪推进。"
        )
        return f"""你是AI8video 的合并视频分段提取模型。

任务：把一集最终视频提示词，提取成 {segment_count} 个连续的 {segment_duration_seconds} 秒视频模型子提示词。

硬性规则：
1. 只做语义提取和时间轴重映射，不要改写成新剧情，不要新增人物、场景、卖点或台词。
{chr(10).join(segment_rules)}
{segment_count + 2}. {rhythm_rule}
{segment_count + 3}. 保留每段里的镜头景别、场景描述、运镜动作、人物动作、台词/口播、音效建议；不要删除台词。
{segment_count + 4}. {segment_count} 个片段合起来必须仍像同一条 0-{final_duration} 秒成片，而不是多个互不相干的短视频；每个后续片段的镜头一都要在画面内容里自然承接上一镜头尾帧中的主体、服装、空间关系、光线和动作方向。
{segment_count + 5}. 如果原文使用 `镜头一（0-5s）`、`镜头三（10-15s）`、`【前10秒（0-5秒）】`、`【后10秒（10-20秒）】` 这类标题，按标题语义提取，不要把标题当正文保留。
{segment_count + 6}. 输出不得包含“总时长{final_duration}秒”“由多个连续片段组成”“本片段是”“只生成”“目标时长”“下面时间轴”等链路说明句。

只返回严格 JSON 对象，不要解释。格式：
{{
{chr(10).join(json_lines)},
  "notes": "一句话说明如何提取"
}}

当前视频标题：
{video.title}

来源摘要：
{video.source_summary or "（无）"}

关键词指导：
{json.dumps(video.keyword_guidance or {}, ensure_ascii=False, indent=2)}

最终视频提示词：
{video.prompt}
"""

    @staticmethod
    def _wrap_segment_body(body: str, *, segment_index: int, segment_duration_seconds: int) -> str:
        del segment_index, segment_duration_seconds
        return str(body or "").strip()

    @classmethod
    def _normalize_local_segment_body(cls, body: str) -> str:
        text = str(body or "").strip()
        blocks = cls._time_blocks(text)
        if not blocks:
            return text
        pieces: list[str] = []
        for ordinal, block in enumerate(blocks, start=1):
            pieces.append(cls._normalize_segment_time_block(block["text"], offset=0, lens_ordinal=ordinal))
        return "\n".join(piece.strip() for piece in pieces if piece.strip()).strip()

    @staticmethod
    def _validate_ai_segment_body(
        body: str,
        *,
        segment_index: int,
        source_prompt: str,
        min_time_blocks: int = 2,
    ) -> None:
        text = str(body or "").strip()
        if not text:
            raise ValueError(f"segment {segment_index} prompt is empty")
        if segment_index >= 2 and len(list(TIME_BLOCK_RE.finditer(text))) < min_time_blocks:
            raise ValueError(f"segment {segment_index} prompt must keep enough internal time blocks")
        source = str(source_prompt or "")
        if len(text) > max(240, int(len(source) * 0.85)):
            if ("前10秒" in text and "后10秒" in text) or ("总时长" in text and "片段" in text):
                raise ValueError(f"segment {segment_index} prompt looks like a full final prompt")

    @classmethod
    def _build_segment_prompt(
        cls,
        prompt: str,
        *,
        segment_index: int,
        segment_duration_seconds: int,
        split_single_block: bool = True,
        task_constraints: str | None = None,
    ) -> str:
        body = cls._extract_segment_body(
            prompt,
            segment_index=segment_index,
            segment_duration_seconds=segment_duration_seconds,
            split_single_block=split_single_block,
        )
        guarded = _apply_custom_safety_guard(body, task_constraints)
        return cls._append_tail_frame_instruction(guarded, task_constraints=task_constraints)

    @classmethod
    def _extract_segment_body(
        cls,
        prompt: str,
        *,
        segment_index: int,
        segment_duration_seconds: int,
        split_single_block: bool = True,
    ) -> str:
        text = cls._strip_final_duration_intro(str(prompt or "").strip(), segment_duration_seconds)
        blocks = cls._time_blocks(text)
        if not blocks:
            marker_body = cls._front_back_marker_body(text, segment_index, segment_duration_seconds)
            if marker_body:
                return marker_body
            return cls._fallback_segment_body(text, segment_index)
        segment_start = (segment_index - 1) * segment_duration_seconds
        segment_end = segment_index * segment_duration_seconds
        selected = [
            block for block in blocks
            if segment_start <= block["start"] < segment_end
        ]
        if not selected:
            marker_body = cls._front_back_marker_body(text, segment_index, segment_duration_seconds)
            if marker_body:
                return marker_body
            return cls._fallback_segment_body(text, segment_index)
        pieces: list[str] = []
        for ordinal, block in enumerate(selected, start=1):
            pieces.append(cls._normalize_segment_time_block(
                block["text"],
                offset=segment_start,
                lens_ordinal=ordinal,
            ))
        if split_single_block and segment_index >= 2 and len(pieces) == 1 and len(list(TIME_BLOCK_RE.finditer(pieces[0]))) < 2:
            return cls._split_single_block_into_internal_rhythm(
                pieces[0],
                segment_duration_seconds=segment_duration_seconds,
            )
        return "\n".join(piece.strip() for piece in pieces if piece.strip()).strip()

    @staticmethod
    def _strip_final_duration_intro(prompt: str, segment_duration_seconds: int) -> str:
        lines = []
        for line in str(prompt or "").splitlines():
            stripped = line.strip()
            if not stripped:
                lines.append(line)
                continue
            if (
                re.search(r"总时长\s*约?\s*\d{1,3}\s*秒", stripped)
            ) and "片段" in stripped:
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    @staticmethod
    def _time_blocks(prompt: str) -> list[dict]:
        prompt = _normalize_inline_time_block_boundaries(prompt)
        matches = list(TIME_BLOCK_RE.finditer(prompt))
        blocks: list[dict] = []
        for idx, match in enumerate(matches):
            start = match.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(prompt)
            try:
                start_seconds = int(match.group("start"))
                end_seconds = int(match.group("end"))
            except ValueError:
                continue
            blocks.append({
                "start": start_seconds,
                "end": end_seconds,
                "text": prompt[start:end].strip(),
            })
        return blocks

    @staticmethod
    def _normalize_segment_time_block(block_text: str, *, offset: int, lens_ordinal: int = 1) -> str:
        def repl(match: re.Match[str]) -> str:
            start = max(0, int(match.group("start")) - offset)
            end = max(start, int(match.group("end")) - offset)
            return f"{_lens_label(lens_ordinal)}（{start}-{end}s）："

        return TIME_BLOCK_RE.sub(repl, block_text, count=1)

    @staticmethod
    def _front_back_marker_body(prompt: str, segment_index: int, segment_duration_seconds: int) -> str | None:
        prompt = _normalize_inline_time_block_boundaries(prompt)
        matches = list(FRONT_BACK_HEADING_RE.finditer(prompt))
        if not matches:
            return None
        wanted = "前" if segment_index == 1 else "后"
        for idx, match in enumerate(matches):
            if match.group("part") != wanted:
                continue
            start = match.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(prompt)
            body = prompt[start:end].strip()
            if not body:
                return None
            normalized = FRONT_BACK_HEADING_RE.sub(f"镜头一（0-{segment_duration_seconds}s）：", body, count=1).strip()
            if segment_index == 2 and len(list(TIME_BLOCK_RE.finditer(normalized))) < 2:
                return AI8VideoMergedPipeline._split_single_block_into_internal_rhythm(
                    normalized,
                    segment_duration_seconds=segment_duration_seconds,
                )
            return normalized
        return None

    @staticmethod
    def _fallback_segment_body(prompt: str, segment_index: int) -> str:
        parts = [part.strip() for part in re.split(r"\n\s*\n+", prompt) if part.strip()]
        if len(parts) >= 2:
            midpoint = max(1, len(parts) // 2)
            selected = parts[:midpoint] if segment_index == 1 else parts[midpoint:]
            return "\n\n".join(selected).strip()
        return (
            f"只生成原始提示词中第 {segment_index} 个连续片段的剧情，"
            f"不要覆盖另一半内容。\n{prompt}"
        ).strip()

    @staticmethod
    def _split_single_block_into_internal_rhythm(block_text: str, *, segment_duration_seconds: int) -> str:
        half = max(1, segment_duration_seconds // 2)
        text = str(block_text or "").strip()
        match = TIME_BLOCK_RE.search(text)
        body = text[match.end():].strip() if match else text
        first_body, second_body = _split_body_for_internal_rhythm(body, half_seconds=half)
        return (
            f"镜头一（0-{half}s）：\n{first_body.strip()}\n"
            f"镜头二（{half}-{segment_duration_seconds}s）：\n{second_body.strip()}"
        ).strip()

    @staticmethod
    def _append_tail_frame_instruction(prompt: str, *, task_constraints: str | None = None) -> str:
        text = str(prompt or "").strip()
        if _custom_safety_requires_no_person(task_constraints):
            return text
        if TAIL_FRAME_PROMPT_SUFFIX in text:
            return text
        return f"{text}\n{TAIL_FRAME_PROMPT_SUFFIX}".strip()

    @staticmethod
    def _copy_tail_frame_to_user_temp(
        tail_frame: Path,
        *,
        video: VideoPrompt,
        segment_index: int,
        progress_session_id: str | None,
    ) -> Path:
        session_part = re.sub(r"[^0-9A-Za-z_-]+", "-", str(progress_session_id or "unknown")).strip("-") or "unknown"
        target_dir = MERGE_TEMP_MEDIA_DIR / session_part
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{video.index:02d}-segment-{segment_index}-tail.png"
        shutil.copy2(tail_frame, target)
        return target


def _job_record(job: QuickVideoJob, role: str, *, first_frame: FirstFrameAsset | None = None) -> dict:
    return {
        "role": role,
        "videoIndex": job.video_index,
        "jobId": job.job_id,
        "status": job.status,
        "videoUrl": job.video_url,
        "localVideoPath": job.local_video_path,
        "storageKey": job.storage_key,
        "finalFrameStorageKey": job.final_frame_storage_key,
        "firstFrame": None if first_frame is None else first_frame.__dict__,
    }


def _segment_narration_text(prompt: str) -> str:
    text = extract_dialogue_text(prompt)
    return prepare_narration_text(text) if text else ""


def _merged_local_tts_narration_text(segment_records: list[dict], video: VideoPrompt) -> str:
    pieces: list[str] = []
    for record in segment_records:
        text = str(record.get("narrationText") or "").strip()
        if text:
            pieces.append(_ensure_tts_sentence_boundary(text))
    if pieces:
        return prepare_narration_text("\n".join(pieces))
    return ""


def _merged_video_duration_seconds(video_path: Path) -> float | None:
    try:
        duration = probe_media_duration_seconds(video_path)
    except Exception:
        return None
    return _optional_float(duration)


def _ensure_tts_sentence_boundary(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    if re.search(r"[。！？!?…]$", cleaned):
        return cleaned
    return f"{cleaned}。"


def _clean_duration_seconds(value: float | int | str | None) -> float:
    number = _optional_float(value)
    if number is None:
        return 1.0
    return round(max(1.0, number), 3)


def _join_constraint_blocks(*blocks: str | None) -> str:
    return "\n".join(str(block).strip() for block in blocks if str(block or "").strip())


def _optional_float(value: Any) -> float | None:
    try:
        number = float(str(value))
    except Exception:
        return None
    if number != number:
        return None
    return round(number, 3)


def _parse_json_object(raw: str) -> dict:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    if not text.startswith("{"):
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            text = match.group(0)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data


def _split_body_for_internal_rhythm(body: str, *, half_seconds: int) -> tuple[str, str]:
    lines = [line.strip() for line in str(body or "").splitlines() if line.strip()]
    if len(lines) >= 2:
        split_at = max(1, len(lines) // 2)
        first_lines = lines[:split_at]
        second_lines = lines[split_at:]
        if second_lines:
            second_lines = [
                f"延续 0-{half_seconds} 秒的主体、服装、空间关系、光线和动作方向。",
                *second_lines,
            ]
        return "\n".join(first_lines), "\n".join(second_lines or first_lines)

    text = " ".join(lines).strip()
    if not text:
        continuation = f"延续 0-{half_seconds} 秒的主体、服装、空间关系、光线和动作方向，继续完成后半段剧情。"
        return continuation, continuation

    sentences = [item.strip() for item in re.split(r"(?<=[。！？；;])\s*", text) if item.strip()]
    if len(sentences) >= 2:
        split_at = max(1, len(sentences) // 2)
        return "".join(sentences[:split_at]), (
            f"延续 0-{half_seconds} 秒的主体、服装、空间关系、光线和动作方向。"
            f"{''.join(sentences[split_at:])}"
        )

    return (
        f"{text}\n只执行这一段内容的起势和推进，不完成最终收束。",
        f"延续 0-{half_seconds} 秒的主体、服装、空间关系、光线和动作方向，继续完成这一段内容的后半程：{text}",
    )


def _lens_label(ordinal: int) -> str:
    if 1 <= ordinal <= len(LENS_LABELS):
        return LENS_LABELS[ordinal - 1]
    return f"镜头{ordinal}"


def _normalize_inline_time_block_boundaries(prompt: str) -> str:
    text = str(prompt or "")
    if not text:
        return ""
    text = re.sub(
        r"(?<!^)(?<!\n)(?<![【\[])(?=镜头[一二三四五六七八九十\d]+\s*[（(]\s*"
        r"\d{1,3}\s*[-—~至到]\s*\d{1,3}\s*(?:秒|s|S)[）)][】\]]?[ \t]*[：:]?)",
        "\n",
        text,
    )
    text = re.sub(
        r"(?<!^)(?<!\n)(?<![【\[])(?=[【\[]?(?:前|后)\s*\d{1,3}\s*秒[（(]\s*"
        r"\d{1,3}\s*[-—~至到]\s*\d{1,3}\s*(?:秒|s|S)[）)][】\]]?[ \t]*[：:]?)",
        "\n",
        text,
    )
    return re.sub(
        r"(?<!^)(?<!\n)(?<![\d（(])(?=\d{1,3}\s*[-—~至到]\s*\d{1,3}\s*(?:秒|s|S)[ \t]*[：:])",
        "\n",
        text,
    )
