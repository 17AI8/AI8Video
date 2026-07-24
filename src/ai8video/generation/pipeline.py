from __future__ import annotations

import inspect
import logging
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ai8video.batch.specialist_agent_observer import record_planner_execution
from ai8video.generation.video_prompt_planner import (
    LLMCallable,
    expand_batch_seed_messages_with_ai,
    infer_smart_video_count_with_ai,
    rewrite_video_with_ai,
    single_prompt_to_video,
    plan_video_prompts_with_ai,
)
from ai8video.assets.asset_store import JsonlAssetStore
from ai8video.generation.business_prompt import finalize_video_prompts
from ai8video.core.config import AI8VideoConfig
from ai8video.assets.default_reference_image import build_reference_image_instruction
from ai8video.generation.generation_progress import (
    GenerationCancelled,
    fail_generation_progress,
    finish_generation_progress,
    generation_stop_reason,
    is_generation_stopped,
    mark_job_archiving,
    mark_job_failed,
    mark_job_preparing_first_frame,
    mark_job_polling,
    mark_job_submitting,
    mark_job_submitted,
    mark_job_succeeded,
    start_generation_progress,
)
from ai8video.integrations.llm_provider import build_openai_compat_llm
from ai8video.knowledge.script_knowledge_rerank import build_script_rerank_llm
from ai8video.knowledge.script_knowledge_query import build_script_query_llm
from ai8video.application.message_parser import parse_employee_message
from ai8video.integrations.direct_video_model_client import AI8VideoModelClient
from ai8video.integrations.video_model_settings import load_video_model_settings
from ai8video.core.models import ArchivedAsset, VideoPrompt, FirstFrameAsset, ParsedRequest, PipelineResult, QuickVideoJob, GenerationOutcome
from ai8video.generation.prompt_trace import append_prompt_trace
from ai8video.generation.tail_frame_chaining import (
    append_tail_frame_chain_prompt,
    build_next_tail_frame_request,
)
from ai8video.generation.output_review import review_final_outputs
from ai8video.generation.reference_image_preprocessor import (
    TRANSFORMED_REFERENCE_DIR,
    ReferenceImagePreprocessor,
    remove_transformed_reference_asset,
)
from ai8video.assets.video_asset_archiver import VideoAssetArchiver, archive_with_progress


CONCURRENT_SUBMIT_STAGGER_SECONDS = 1.0
logger = logging.getLogger(__name__)


class AI8VideoPipeline:
    def __init__(
        self,
        config: AI8VideoConfig | None = None,
        llm: LLMCallable | None = None,
    ):
        self.config = config or AI8VideoConfig.from_env()
        self.llm = llm or build_openai_compat_llm(self.config)
        interpreter_timeout = max(5, min(30, int(os.getenv("AI8VIDEO_REQUEST_INTERPRETER_TIMEOUT_SECONDS", "20"))))
        self.request_interpreter_llm = llm or build_openai_compat_llm(
            self.config,
            timeout_seconds=interpreter_timeout,
            system_prompt="你是AI8video 的员工自然语言请求理解器，只返回严格 JSON。",
        )
        self.script_rerank_llm = llm or build_script_rerank_llm(self.config)
        self.script_query_llm = llm or build_script_query_llm(self.config)
        self.client = AI8VideoModelClient(self.config)
        self.asset_store = JsonlAssetStore(self.config.asset_store_path)
        self.archiver = VideoAssetArchiver(self.config)
        self.reference_image_preprocessor = ReferenceImagePreprocessor(self.config, llm=self.llm)

    def run_from_message(self, message: str, *, progress_session_id: str | None = None) -> PipelineResult:
        request = parse_employee_message(message)
        return self.run_request(request, progress_session_id=progress_session_id)

    def run_request(self, request: ParsedRequest, *, progress_session_id: str | None = None) -> PipelineResult:
        videos = self.plan_request(request, progress_session_id=progress_session_id)
        return self.run_planned_request(request, videos, progress_session_id=progress_session_id)

    def plan_request(
        self,
        request: ParsedRequest,
        *,
        progress_session_id: str | None = None,
        smart_split: bool = False,
    ) -> list[VideoPrompt]:
        allow_mock_planning = self.config.dry_run
        task_constraints = self._reference_task_constraints(request)
        target_duration = self._effective_video_duration_seconds(request.duration_seconds)
        video_count = request.video_count
        if smart_split:
            video_count = infer_smart_video_count_with_ai(
                request.raw_text,
                llm=self.llm,
                duration_seconds=target_duration,
                trace_session_id=progress_session_id,
            )
        if smart_split or request.mode == "batch_videos":
            if not video_count:
                raise ValueError("video_count is required for video planning")
            videos = plan_video_prompts_with_ai(
                request.raw_text,
                video_count,
                request.style_hint,
                request.core_keywords,
                task_constraints=task_constraints,
                final_duration_seconds=target_duration,
                llm=self.llm,
                allow_mock=allow_mock_planning,
                trace_session_id=progress_session_id,
            )
        else:
            videos = single_prompt_to_video(request.raw_text, request.style_hint, request.core_keywords)
        record_planner_execution(
            videos,
            session_id=progress_session_id,
            source_stage="planning_output",
        )
        return videos

    def _effective_video_duration_seconds(self, requested: int | None) -> int:
        settings = load_video_model_settings(
            llm_base_url=getattr(self.config, "llm_base_url", None),
            llm_api_key=getattr(self.config, "llm_api_key", None),
        )
        duration = settings.seconds if requested in (None, 10) else int(requested)
        guard = getattr(getattr(self, "client", None), "guard", None)
        if not self.config.dry_run and guard and guard.forced_duration_seconds > 0:
            duration = guard.forced_duration_seconds
        return max(1, int(duration))

    def run_planned_request(
        self,
        request: ParsedRequest,
        videos: list[VideoPrompt],
        *,
        progress_session_id: str | None = None,
    ) -> PipelineResult:
        return self._run_videos(request, videos, progress_session_id=progress_session_id)

    def rewrite_video(
        self,
        request: ParsedRequest,
        video: VideoPrompt,
        rewrite_instruction: str,
        *,
        progress_session_id: str | None = None,
    ) -> PipelineResult:
        revised_video = rewrite_video_with_ai(
            video,
            rewrite_instruction,
            style_hint=request.style_hint,
            core_keywords=request.core_keywords,
            task_constraints=self._reference_task_constraints(request),
            llm=self.llm,
            allow_mock=self.config.dry_run,
            trace_session_id=progress_session_id,
        )
        return self._run_videos(request, [revised_video], progress_session_id=progress_session_id)

    def retry_video(
        self,
        request: ParsedRequest,
        video: VideoPrompt,
        first_frame: FirstFrameAsset,
        *,
        progress_session_id: str | None = None,
    ) -> PipelineResult:
        start_generation_progress(progress_session_id, [video], concurrent=False)
        try:
            mark_job_submitting(progress_session_id, video)
            job = self.client.create_job(
                text=video.prompt,
                video_index=video.index,
                first_frame=first_frame,
                duration_seconds=request.duration_seconds,
                ratio=request.ratio,
                resolution=request.resolution,
                preset=request.preset,
            )
            mark_job_submitted(progress_session_id, video, job)
            mark_job_polling(progress_session_id, job)
            completed_job = self._poll_job(job, progress_session_id)
            outcome, archive, asset_record = self._record_completed_job(
                request, video, completed_job, first_frame, progress_session_id,
            )
            return PipelineResult(
                request=request,
                videos=[video],
                first_frame=first_frame,
                jobs=[completed_job],
                outcomes=[outcome],
                archives=[archive],
                asset_records=[asset_record],
                dry_run=self.config.dry_run,
            )
        except Exception as exc:
            mark_job_failed(progress_session_id, video.index, exc)
            raise
        finally:
            finish_generation_progress(progress_session_id)

    def expand_seed_messages(
        self,
        seed_messages: list[str],
        target_count: int,
        *,
        style_hint: str | None = None,
        failure_reasons: list[str] | None = None,
    ) -> list[str]:
        return expand_batch_seed_messages_with_ai(
            seed_messages,
            target_count,
            style_hint=style_hint,
            failure_reasons=failure_reasons,
            llm=self.llm,
            allow_mock=self.config.dry_run,
        )

    def _run_videos(
        self,
        request: ParsedRequest,
        videos: list[VideoPrompt],
        *,
        progress_session_id: str | None = None,
    ) -> PipelineResult:
        if not self.config.dry_run and self.client.guard.forced_duration_seconds > 0:
            request.duration_seconds = self.client.guard.forced_duration_seconds
        if not self.config.dry_run:
            self.client.guard.assert_can_create_count(len(videos))
        result_first_frame = None

        start_generation_progress(
            progress_session_id,
            videos,
            concurrent=bool(request.concurrent_generation and not request.tail_frame_chaining and len(videos) > 1),
        )
        logger.info(
            "ai8video generation start session=%s videos=%s concurrent=%s reference=%s transform=%s custom_prompt=%s",
            progress_session_id,
            len(videos),
            bool(request.concurrent_generation and not request.tail_frame_chaining and len(videos) > 1),
            bool(request.reference_image),
            bool(request.reference_image_transform_options and any(request.reference_image_transform_options.values())),
            bool(str(request.reference_image_custom_prompt or "").strip()),
        )
        if request.concurrent_generation and not request.tail_frame_chaining and len(videos) > 1:
            return self._run_videos_concurrently(request, videos, progress_session_id=progress_session_id)

        jobs = []
        outcomes = []
        archives = []
        asset_records = []
        final_videos = []
        first_frames = []
        task_constraints = self._reference_task_constraints(request)
        tail_dir = None
        try:
            finalized_video_queue = finalize_video_prompts(
                videos,
                llm=getattr(self, "llm", None),
                trace_session_id=progress_session_id,
                task_constraints=task_constraints,
            )
            finalized_video_queue = review_final_outputs(
                finalized_video_queue,
                llm=getattr(self, "llm", None),
                trace_session_id=progress_session_id,
            )
            if request.tail_frame_chaining:
                finalized_video_queue = [append_tail_frame_chain_prompt(video) for video in finalized_video_queue]
            active_request = request
            tail_dir = tempfile.TemporaryDirectory(prefix="ai8video-tail-chain-")
            for position, final_video in enumerate(finalized_video_queue):
                final_videos.append(final_video)
                self._trace_final_video_prompt(active_request, final_video, progress_session_id)
                try:
                    mark_job_preparing_first_frame(progress_session_id, final_video)
                    first_frame = self._prepare_video_first_frame(
                        active_request,
                        final_video,
                        progress_session_id=progress_session_id,
                    )
                    first_frames.append(first_frame)
                    if result_first_frame is None:
                        result_first_frame = first_frame
                    mark_job_submitting(progress_session_id, final_video)
                    self._trace_video_submit(
                        active_request,
                        final_video,
                        first_frame,
                        progress_session_id,
                    )
                    job = self.client.create_job(
                        text=final_video.prompt,
                        video_index=final_video.index,
                        first_frame=first_frame,
                        duration_seconds=active_request.duration_seconds,
                        ratio=active_request.ratio,
                        resolution=active_request.resolution,
                        preset=active_request.preset,
                    )
                    self._trace_video_job_created(
                        active_request,
                        final_video,
                        job,
                        progress_session_id,
                        duration_seconds=active_request.duration_seconds,
                    )
                    mark_job_submitted(progress_session_id, final_video, job)
                    mark_job_polling(progress_session_id, job)
                    completed_job = self._poll_job(job, progress_session_id)
                    outcome, archive, asset_record = self._record_completed_job(
                        active_request,
                        final_video,
                        completed_job,
                        first_frame,
                        progress_session_id,
                    )
                except Exception as exc:
                    mark_job_failed(progress_session_id, final_video.index, exc)
                    fail_generation_progress(progress_session_id, exc)
                    raise
                jobs.append(completed_job)
                outcomes.append(outcome)
                archives.append(archive)
                asset_records.append(asset_record)
                if request.tail_frame_chaining and position < len(finalized_video_queue) - 1:
                    active_request = build_next_tail_frame_request(
                        active_request,
                        completed_job,
                        archive,
                        Path(tail_dir.name) / f"video-{final_video.index}-tail.png",
                    )
        finally:
            if tail_dir is not None:
                tail_dir.cleanup()
            if len(jobs) + sum(1 for item in asset_records if item) >= len(videos):
                finish_generation_progress(progress_session_id)

        return PipelineResult(
            request=request,
            videos=final_videos,
            first_frame=result_first_frame,
            jobs=jobs,
            outcomes=outcomes,
            archives=archives,
            asset_records=asset_records,
            dry_run=self.config.dry_run,
        )

    def _run_videos_concurrently(
        self,
        request: ParsedRequest,
        videos: list[VideoPrompt],
        *,
        progress_session_id: str | None = None,
    ) -> PipelineResult:
        first_frame_by_index = {}
        task_constraints = self._reference_task_constraints(request)
        try:
            final_videos = finalize_video_prompts(
                videos,
                llm=getattr(self, "llm", None),
                trace_session_id=progress_session_id,
                task_constraints=task_constraints,
            )
            final_videos = review_final_outputs(
                final_videos,
                llm=getattr(self, "llm", None),
                trace_session_id=progress_session_id,
            )
            for video in final_videos:
                self._trace_final_video_prompt(request, video, progress_session_id)
            ordered_final_videos = sorted(final_videos, key=lambda item: item.index)
            logger.info(
                "ai8video concurrent submit start session=%s videos=%s",
                progress_session_id,
                len(ordered_final_videos),
            )
            video_by_index = {video.index: video for video in final_videos}
            created_jobs = []
            submit_failed_jobs = []
            submit_worker_count = len(ordered_final_videos)
            with ThreadPoolExecutor(max_workers=submit_worker_count) as executor:
                future_map = {
                    executor.submit(
                        self._submit_video_job,
                        request,
                        video,
                        progress_session_id,
                        submit_offset=offset,
                    ): video
                    for offset, video in enumerate(ordered_final_videos)
                }
                for future in as_completed(future_map):
                    video = future_map[future]
                    try:
                        first_frame, job = future.result()
                        first_frame_by_index[video.index] = first_frame
                        created_jobs.append(job)
                    except Exception as exc:
                        mark_job_failed(progress_session_id, video.index, exc)
                        submit_failed_jobs.append(
                            QuickVideoJob(
                                video_index=video.index,
                                job_id=f"create-failed-{video.index}",
                                status="failed",
                                prompt=video.prompt,
                                error=str(exc),
                            )
                        )

            result_first_frame = first_frame_by_index.get(ordered_final_videos[0].index)

            jobs_by_index = {}
            outcomes_by_index = {}
            archives_by_index = {}
            asset_records_by_index = {}
            for failed_job in submit_failed_jobs:
                video = video_by_index[failed_job.video_index]
                outcome, archive, asset_record = self._record_completed_job(
                    request,
                    video,
                    failed_job,
                    first_frame_by_index.get(video.index),
                    progress_session_id,
                )
                jobs_by_index[video.index] = failed_job
                outcomes_by_index[video.index] = outcome
                archives_by_index[video.index] = archive
                asset_records_by_index[video.index] = asset_record

            worker_count = min(len(created_jobs), 3)
            if worker_count > 0:
                with ThreadPoolExecutor(max_workers=worker_count) as executor:
                    for job in created_jobs:
                        mark_job_polling(progress_session_id, job)
                    future_map = {executor.submit(self._poll_job, job, progress_session_id): job for job in created_jobs}
                    for future in as_completed(future_map):
                        job = future_map[future]
                        try:
                            completed_job = future.result()
                        except Exception as exc:
                            completed_job = self._job_from_poll_exception(job, exc)
                        video = video_by_index[completed_job.video_index]
                        outcome, archive, asset_record = self._record_completed_job(
                            request,
                            video,
                            completed_job,
                            first_frame_by_index.get(video.index),
                            progress_session_id,
                        )
                        jobs_by_index[video.index] = completed_job
                        outcomes_by_index[video.index] = outcome
                        archives_by_index[video.index] = archive
                        asset_records_by_index[video.index] = asset_record

            ordered_indexes = [video.index for video in ordered_final_videos]
            jobs = [jobs_by_index[index] for index in ordered_indexes if index in jobs_by_index]
            outcomes = [outcomes_by_index[index] for index in ordered_indexes if index in outcomes_by_index]
            archives = [archives_by_index[index] for index in ordered_indexes if index in archives_by_index]
            asset_records = [asset_records_by_index[index] for index in ordered_indexes if index in asset_records_by_index]
            if len(asset_records) >= len(videos):
                finish_generation_progress(progress_session_id)

            return PipelineResult(
                request=request,
                videos=ordered_final_videos,
                first_frame=result_first_frame,
                jobs=jobs,
                outcomes=outcomes,
                archives=archives,
                asset_records=asset_records,
                dry_run=self.config.dry_run,
            )
        finally:
            pass

    @staticmethod
    def _reference_task_constraints(request: ParsedRequest) -> str | None:
        blocks: list[str] = []
        if getattr(request, "reference_image", None):
            blocks.append(build_reference_image_instruction(
                getattr(request, "reference_image_transform_options", None),
                getattr(request, "reference_image_custom_prompt", None),
            ))
        custom_constraints = AI8VideoPipeline._custom_input_task_constraints(request.raw_text)
        if custom_constraints:
            blocks.append(custom_constraints)
        return "\n".join(block for block in blocks if block.strip()) or None

    @staticmethod
    def _custom_input_task_constraints(raw_text: str) -> str | None:
        text = str(raw_text or "")
        markers = (
            "当次安全过滤",
            "本次用户自定义输入中的安全过滤",
            "安全过滤",
            "安全与画面类别要求",
            "连续叙事要求",
        )
        if not any(marker in text for marker in markers):
            return None
        override_markers = (
            "本次覆盖工具栏设置",
            "本次临时覆盖工具栏设置",
            "本次明确覆盖用户设置",
        )
        explicit_override = any(marker in text for marker in override_markers)
        constraints = [
            "本次用户自定义输入中的安全过滤和连续叙事要求属于当前任务补充约束。",
            "除非用户明确要求本次覆盖工具栏设置，否则不得删除、替换、弱化或反转用户可编辑业务模型系统提示词、参考图和其他工具栏设置。",
            "不得新增用户自定义输入明确排除的真实主体可识别、第三方露出、外貌化或低俗化、可读文字、营销化收尾等风险类别。",
            "视频提示词必须围绕用户自定义输入中的同一事件链路连续推进，不得改成无关镜头拼贴。",
        ]
        if explicit_override:
            constraints.insert(0, "本次明确覆盖工具栏用户设置")
        no_person_markers = (
            "无人物",
            "无人脸",
            "无身体",
            "不要求人物出镜",
            "不出现人脸",
            "身体特写",
            "人物会触发风险",
        )
        if any(marker in text for marker in no_person_markers):
            constraints.append("本次如果要求无人物、无人脸或无身体部位，最终提示词必须使用物件、空间、背影以外的安全场景承载。")
        return "\n".join(constraints)

    def _submit_video_job(
        self,
        request: ParsedRequest,
        video: VideoPrompt,
        progress_session_id: str | None,
        *,
        submit_offset: int,
    ) -> tuple[object, QuickVideoJob]:
        delay = max(0.0, float(CONCURRENT_SUBMIT_STAGGER_SECONDS))
        if submit_offset > 0 and delay > 0:
            time.sleep(delay * submit_offset)
        mark_job_preparing_first_frame(progress_session_id, video)
        logger.info(
            "ai8video video first-frame start session=%s video=%s",
            progress_session_id,
            video.index,
        )
        first_frame = self._prepare_video_first_frame(
            request,
            video,
            progress_session_id=progress_session_id,
        )
        mark_job_submitting(progress_session_id, video)
        logger.info(
            "ai8video video video-submit start session=%s video=%s first_frame=%s",
            progress_session_id,
            video.index,
            bool(first_frame),
        )
        self._trace_video_submit(
            request,
            video,
            first_frame,
            progress_session_id,
        )
        job = self.client.create_job(
            text=video.prompt,
            video_index=video.index,
            first_frame=first_frame,
            duration_seconds=request.duration_seconds,
            ratio=request.ratio,
            resolution=request.resolution,
            preset=request.preset,
        )
        self._trace_video_job_created(
            request,
            video,
            job,
            progress_session_id,
            duration_seconds=request.duration_seconds,
        )
        mark_job_submitted(progress_session_id, video, job)
        return first_frame, job

    def _archive_transformed_first_frame(
        self,
        first_frame: FirstFrameAsset | None,
        video: VideoPrompt,
        progress_session_id: str | None,
    ) -> FirstFrameAsset | None:
        source = str(getattr(first_frame, "source", "") or "").strip()
        if first_frame is None or not source:
            return first_frame
        source_path = Path(source).expanduser()
        try:
            source_path.resolve().relative_to(TRANSFORMED_REFERENCE_DIR.resolve())
        except (OSError, ValueError):
            return first_frame
        if not source_path.is_file():
            return first_frame
        session_key = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (progress_session_id or "manual"))
        destination = Path(self.config.archive_local_dir) / "first-frames" / session_key / source_path.name
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            source_path.replace(destination)
            first_frame.source = str(destination)
        except OSError as exc:
            logger.warning("ai8video first-frame archive failed source=%s error=%s", source_path, exc)
        return first_frame

    def _cleanup_transformed_first_frames(self, first_frames, progress_session_id: str | None) -> None:
        seen = set()
        for first_frame in first_frames:
            source = getattr(first_frame, "source", None)
            if not source or source in seen:
                continue
            seen.add(source)
            try:
                removed = remove_transformed_reference_asset(source)
            except Exception as exc:
                logger.warning(
                    "ai8video transformed first-frame cleanup failed session=%s source=%s error=%s",
                    progress_session_id,
                    source,
                    exc,
                )
                continue
            if removed:
                logger.info("ai8video transformed first-frame removed session=%s source=%s", progress_session_id, source)

    def _job_from_poll_exception(self, job: QuickVideoJob, exc: Exception) -> QuickVideoJob:
        try:
            latest = self.client.get_job(job.job_id, job.video_index, job.prompt)
        except Exception:
            latest = None
        if isinstance(latest, QuickVideoJob) and latest.status in {"succeeded", "failed"}:
            if not latest.error and latest.status == "failed":
                latest.error = str(exc)
            return latest
        return QuickVideoJob(
            video_index=job.video_index,
            job_id=job.job_id,
            status="failed",
            prompt=job.prompt,
            error=str(exc),
            provider_status=getattr(latest, "provider_status", None),
            provider_progress=getattr(latest, "provider_progress", None),
        )

    def _record_completed_job(
        self,
        request: ParsedRequest,
        video: VideoPrompt,
        completed_job: QuickVideoJob,
        first_frame,
        progress_session_id: str | None,
    ) -> tuple[GenerationOutcome, ArchivedAsset, dict]:
        first_frame = self._archive_transformed_first_frame(
            first_frame,
            video,
            progress_session_id,
        )
        outcome = _build_generation_outcome(completed_job, video)
        if not _is_generated_job(completed_job):
            error = "；".join(outcome.reasons) or completed_job.error or "生成失败"
            archive = ArchivedAsset(
                video_index=video.index,
                job_id=completed_job.job_id,
                backend=self.config.archive_backend,
                status="failed",
                error=error,
                meta={"reason": "生成未成功，不创建视频归档"},
            )
            asset_record = self.asset_store.append(request, video, completed_job, outcome, first_frame, archive)
            mark_job_failed(
                progress_session_id,
                video.index,
                error,
                job_id=completed_job.job_id,
                asset_record=asset_record,
            )
            return outcome, archive, asset_record

        mark_job_archiving(progress_session_id, completed_job)
        try:
            archive = archive_with_progress(
                self.archiver.archive,
                request,
                video,
                completed_job,
                outcome,
                progress_session_id=progress_session_id,
            )
        except Exception as exc:
            archive = ArchivedAsset(
                video_index=video.index,
                job_id=completed_job.job_id,
                backend=self.config.archive_backend,
                status="error",
                error=str(exc),
                meta={"reason": "归档或后处理失败，不创建成功结果"},
            )
        asset_record = self.asset_store.append(request, video, completed_job, outcome, first_frame, archive)
        if archive.status in {"archived", "stored", "simulated", "disabled"}:
            mark_job_succeeded(progress_session_id, completed_job, asset_record)
        else:
            mark_job_failed(
                progress_session_id,
                video.index,
                archive.error or "归档或后处理失败",
                job_id=completed_job.job_id,
                asset_record=asset_record,
            )
        return outcome, archive, asset_record

    def _prepare_video_first_frame(
        self,
        request: ParsedRequest,
        video: VideoPrompt,
        *,
        progress_session_id: str | None = None,
    ):
        parameters = inspect.signature(self.reference_image_preprocessor.prepare_first_frame).parameters
        if "trace_session_id" in parameters:
            return self.reference_image_preprocessor.prepare_first_frame(
                request,
                video=video,
                trace_session_id=progress_session_id,
            )
        if "video" in parameters:
            return self.reference_image_preprocessor.prepare_first_frame(request, video=video)
        return self.reference_image_preprocessor.prepare_first_frame(request)

    def _trace_video_submit(
        self,
        request: ParsedRequest,
        video: VideoPrompt,
        first_frame: FirstFrameAsset | None,
        progress_session_id: str | None,
        *,
        segment_label: str | None = None,
        duration_seconds: int | None = None,
    ) -> None:
        append_prompt_trace(
            "video_submit",
            session_id=progress_session_id,
            payload={
                "videoIndex": video.index,
                "title": video.title,
                "durationSeconds": duration_seconds if duration_seconds is not None else request.duration_seconds,
                "ratio": request.ratio,
                "resolution": request.resolution,
                "preset": request.preset,
                "segmentLabel": segment_label,
                "videoModel": _current_video_settings_trace(self.client),
                "hasFirstFrame": first_frame is not None,
                "firstFrame": _summarize_first_frame(first_frame),
            },
        )

    def _trace_video_job_created(
        self,
        request: ParsedRequest,
        video: VideoPrompt,
        job: QuickVideoJob,
        progress_session_id: str | None,
        *,
        segment_label: str | None = None,
        duration_seconds: int | None = None,
    ) -> None:
        append_prompt_trace(
            "video_job_created",
            session_id=progress_session_id,
            payload={
                "videoIndex": video.index,
                "title": video.title,
                "durationSeconds": duration_seconds if duration_seconds is not None else request.duration_seconds,
                "ratio": request.ratio,
                "resolution": request.resolution,
                "preset": request.preset,
                "segmentLabel": segment_label,
                "jobId": job.job_id,
                "status": job.status,
                "videoModel": _current_video_settings_trace(self.client, job=job),
                "hasVideoUrl": bool(job.video_url),
                "videoUrl": job.video_url or "",
                "storageKey": job.storage_key,
                "providerStatus": job.provider_status,
                "providerProgress": job.provider_progress,
                "stageLabel": job.stage_label,
            },
        )

    def _trace_final_video_prompt(
        self,
        request: ParsedRequest,
        video: VideoPrompt,
        progress_session_id: str | None,
    ) -> None:
        append_prompt_trace(
            "final_video_prompt",
            session_id=progress_session_id,
            payload={
                "videoIndex": video.index,
                "title": video.title,
                "prompt": video.prompt,
                "sourceSummary": video.source_summary,
                "keywordGuidance": video.keyword_guidance,
                "request": {
                    "mode": request.mode,
                    "videoCount": request.video_count,
                    "styleHint": request.style_hint,
                    "coreKeywords": request.core_keywords,
                    "durationSeconds": request.duration_seconds,
                    "ratio": request.ratio,
                    "resolution": request.resolution,
                    "preset": request.preset,
                    "concurrentGeneration": request.concurrent_generation,
                    "htmlMotionOverlayEnabled": request.html_motion_overlay_enabled,
                    "hasReferenceImage": bool(request.reference_image),
                    "referenceImageTransformOptions": request.reference_image_transform_options,
                    "referenceImageCustomPrompt": request.reference_image_custom_prompt,
                },
            },
        )

    def _poll_job(self, job: QuickVideoJob, progress_session_id: str | None) -> QuickVideoJob:
        def callback(latest: QuickVideoJob) -> None:
            if is_generation_stopped(progress_session_id):
                raise GenerationCancelled(generation_stop_reason(progress_session_id))
            mark_job_polling(progress_session_id, latest)
            if is_generation_stopped(progress_session_id):
                raise GenerationCancelled(generation_stop_reason(progress_session_id))

        if is_generation_stopped(progress_session_id):
            raise GenerationCancelled(generation_stop_reason(progress_session_id))
        try:
            parameters = inspect.signature(self.client.poll_job).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "progress_callback" in parameters:
            return self.client.poll_job(job, progress_callback=callback)
        if is_generation_stopped(progress_session_id):
            raise GenerationCancelled(generation_stop_reason(progress_session_id))
        completed = self.client.poll_job(job)
        if is_generation_stopped(progress_session_id):
            raise GenerationCancelled(generation_stop_reason(progress_session_id))
        mark_job_polling(progress_session_id, completed)
        return completed


def _summarize_first_frame(first_frame: FirstFrameAsset | None) -> dict[str, object] | None:
    if first_frame is None:
        return None
    source = first_frame.source or first_frame.first_frame_image_url or first_frame.first_frame_storage_key or ""
    text = str(source or "").strip()
    if text.startswith("data:image/"):
        display = text.split(",", 1)[0] + ",<redacted>"
        kind = "data"
    elif text.startswith(("http://", "https://")):
        display = text.split("?", 1)[0]
        kind = "url"
    elif text:
        display = text
        kind = "local"
    else:
        display = ""
        kind = "empty"
    return {
        "sourceKind": kind,
        "source": display,
        "hasSource": bool(text),
        "hasStorageKey": bool(first_frame.first_frame_storage_key),
        "hasImageUrl": bool(first_frame.first_frame_image_url),
        "hasToken": bool(first_frame.first_frame_token),
    }


def _current_video_settings_trace(client: AI8VideoModelClient, *, job: QuickVideoJob | None = None) -> dict[str, str]:
    usage = getattr(job, "usage", None) if job is not None else None
    if isinstance(usage, dict):
        traced = {
            "template": str(usage.get("template") or "").strip(),
            "model": str(usage.get("model") or "").strip(),
            "provider": str(usage.get("provider") or "").strip(),
        }
        if any(traced.values()):
            return {key: value for key, value in traced.items() if value}
    try:
        settings = client._load_current_settings()
    except Exception:
        settings = getattr(client, "settings", None)
    return {
        "template": str(getattr(settings, "template", "") or "").strip(),
        "model": str(getattr(settings, "model", "") or "").strip(),
        "provider": str(getattr(settings, "provider", "") or "").strip(),
    }


def _is_generated_job(job: QuickVideoJob) -> bool:
    status = str(job.status or "").strip().lower()
    if status not in {"succeeded", "completed"}:
        return False
    return bool(job.video_url or job.local_video_path)


def _build_generation_outcome(job: QuickVideoJob, video: VideoPrompt) -> GenerationOutcome:
    generated = _is_generated_job(job)
    reasons: list[str] = []
    if not generated:
        status = str(job.status or "").strip()
        if status:
            reasons.append(f"生成状态：{status}")
        if job.error:
            reasons.append(str(job.error))
        if not (job.video_url or job.local_video_path):
            reasons.append("没有返回可归档视频")
    return GenerationOutcome(
        video_index=video.index,
        job_id=job.job_id,
        status=job.status,
        decision="generated" if generated else "failed",
        reasons=reasons,
        meta={"kind": "generation_outcome"},
    )
