from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from ai8video.core.models import (
    ArchivedAsset,
    FirstFrameAsset,
    GenerationOutcome,
    ParsedRequest,
    PipelineResult,
    QuickVideoJob,
    VideoPrompt,
)
from ai8video.generation.business_prompt import finalize_video_prompts
from ai8video.generation.generation_progress import (
    fail_generation_progress,
    finish_generation_progress,
    mark_job_failed,
    mark_job_polling,
    mark_job_preparing_first_frame,
    mark_job_submitted,
    mark_job_submitting,
    start_generation_progress,
)
from ai8video.generation.output_review import review_final_outputs
from ai8video.generation.prompt_trace import append_prompt_trace
from ai8video.generation.video_prompt_planner import rewrite_video_with_ai
from ai8video.generation.iterative_batch_policy import (
    ITERATIVE_VIDEO_DURATION_SECONDS,
    MAX_ITERATIVE_VIDEO_COUNT,
)


@dataclass
class _AdaptiveState:
    request: ParsedRequest
    videos: list[VideoPrompt] = field(default_factory=list)
    jobs: list[QuickVideoJob] = field(default_factory=list)
    outcomes: list[GenerationOutcome] = field(default_factory=list)
    archives: list[ArchivedAsset] = field(default_factory=list)
    asset_records: list[dict[str, Any]] = field(default_factory=list)
    first_frame: FirstFrameAsset | None = None

    def add(self, execution: tuple[VideoPrompt, FirstFrameAsset | None, QuickVideoJob, GenerationOutcome, ArchivedAsset, dict]) -> None:
        video, first_frame, job, outcome, archive, asset_record = execution
        self.videos.append(video)
        self.jobs.append(job)
        self.outcomes.append(outcome)
        self.archives.append(archive)
        if asset_record:
            self.asset_records.append(asset_record)
        if self.first_frame is None and first_frame is not None:
            self.first_frame = first_frame

    def result(self, *, dry_run: bool) -> PipelineResult:
        return PipelineResult(
            request=self.request,
            videos=self.videos,
            first_frame=self.first_frame,
            jobs=self.jobs,
            outcomes=self.outcomes,
            archives=self.archives,
            asset_records=self.asset_records,
            dry_run=dry_run,
        )


class AdaptiveBatchRunner:
    def __init__(self, pipeline: Any) -> None:
        self.pipeline = pipeline

    def run(
        self,
        request: ParsedRequest,
        videos: list[VideoPrompt],
        *,
        progress_session_id: str | None,
    ) -> PipelineResult:
        ordered = sorted(videos, key=lambda item: item.index)
        self._validate(request, ordered)
        if not self.pipeline.config.dry_run:
            self.pipeline.client.guard.assert_can_create_count(len(ordered))
        start_generation_progress(progress_session_id, ordered, concurrent=False)
        state = _AdaptiveState(request=request)
        previous_review: tuple[int, dict[str, Any]] | None = None
        try:
            for position, planned_video in enumerate(ordered):
                try:
                    candidate = self._prepare_candidate(request, planned_video, previous_review, progress_session_id)
                    execution = self._execute_one(request, candidate, progress_session_id)
                except Exception as exc:
                    execution = self._failed_execution(request, planned_video, exc, progress_session_id)
                state.add(execution)
                review = _generated_review(execution[0])
                stop_reason = _stop_reason(execution[3], execution[4], review)
                if stop_reason and position + 1 < len(ordered):
                    self._append_skipped(state, ordered[position + 1 :], stop_reason)
                    fail_generation_progress(progress_session_id, stop_reason, pending_error=stop_reason)
                    break
                previous_review = (execution[0].index, review) if _review_is_usable(review) else None
        finally:
            finish_generation_progress(progress_session_id)
        return state.result(dry_run=self.pipeline.config.dry_run)

    def _validate(self, request: ParsedRequest, videos: list[VideoPrompt]) -> None:
        if not request.iterative_generation:
            raise ValueError("自适应批量执行需要 iterative_generation=true")
        if not 1 <= len(videos) <= MAX_ITERATIVE_VIDEO_COUNT:
            raise ValueError(f"自适应批量只允许 1 到 {MAX_ITERATIVE_VIDEO_COUNT} 条视频")
        if int(request.duration_seconds or 0) != ITERATIVE_VIDEO_DURATION_SECONDS:
            raise ValueError("自适应批量每条视频必须固定为 10 秒")
        forced = int(getattr(self.pipeline.client.guard, "forced_duration_seconds", 0) or 0)
        if forced not in {0, ITERATIVE_VIDEO_DURATION_SECONDS}:
            raise ValueError("真实生成护栏的强制时长与自适应批量固定 10 秒冲突")

    def _prepare_candidate(
        self,
        request: ParsedRequest,
        planned_video: VideoPrompt,
        previous_review: tuple[int, dict[str, Any]] | None,
        session_id: str | None,
    ) -> VideoPrompt:
        candidate = planned_video
        if previous_review is not None:
            source_index, review = previous_review
            candidate = self._apply_feedback(request, candidate, source_index, review, session_id)
        finalized = finalize_video_prompts(
            [candidate],
            llm=getattr(self.pipeline, "llm", None),
            trace_session_id=session_id,
            task_constraints=self.pipeline._reference_task_constraints(request),
        )[0]
        return review_final_outputs(
            [finalized],
            llm=getattr(self.pipeline, "llm", None),
            trace_session_id=session_id,
        )[0]

    def _apply_feedback(
        self,
        request: ParsedRequest,
        video: VideoPrompt,
        source_index: int,
        review: dict[str, Any],
        session_id: str | None,
    ) -> VideoPrompt:
        feedback = _feedback_instruction(source_index, review)
        guidance = dict(video.keyword_guidance or {})
        guidance["iteration"] = _iteration_guidance(source_index, review, bool(feedback))
        annotated = replace(video, keyword_guidance=guidance)
        if not feedback:
            return annotated
        append_prompt_trace(
            "iteration_feedback_applied",
            session_id=session_id,
            payload={"videoIndex": video.index, "sourceVideoIndex": source_index, "feedback": feedback},
        )
        return rewrite_video_with_ai(
            annotated,
            feedback,
            style_hint=request.style_hint,
            core_keywords=request.core_keywords,
            task_constraints=self.pipeline._reference_task_constraints(request),
            llm=getattr(self.pipeline, "llm", None),
            allow_mock=self.pipeline.config.dry_run,
            trace_session_id=session_id,
        )

    def _execute_one(
        self,
        request: ParsedRequest,
        video: VideoPrompt,
        session_id: str | None,
    ) -> tuple[VideoPrompt, FirstFrameAsset | None, QuickVideoJob, GenerationOutcome, ArchivedAsset, dict]:
        self.pipeline._trace_final_video_prompt(request, video, session_id)
        mark_job_preparing_first_frame(session_id, video)
        first_frame = self.pipeline._prepare_video_first_frame(request, video, progress_session_id=session_id)
        mark_job_submitting(session_id, video)
        self.pipeline._trace_video_submit(request, video, first_frame, session_id)
        job = self.pipeline.client.create_job(
            text=video.prompt,
            video_index=video.index,
            first_frame=first_frame,
            duration_seconds=ITERATIVE_VIDEO_DURATION_SECONDS,
            ratio=request.ratio,
            resolution=request.resolution,
            preset=request.preset,
        )
        self.pipeline._trace_video_job_created(
            request,
            video,
            job,
            session_id,
            duration_seconds=ITERATIVE_VIDEO_DURATION_SECONDS,
        )
        mark_job_submitted(session_id, video, job)
        mark_job_polling(session_id, job)
        completed_job = self.pipeline._poll_job(job, session_id)
        outcome, archive, asset_record = self.pipeline._record_completed_job(
            request, video, completed_job, first_frame, session_id,
        )
        return video, first_frame, completed_job, outcome, archive, asset_record

    def _failed_execution(
        self,
        request: ParsedRequest,
        video: VideoPrompt,
        exc: Exception,
        session_id: str | None,
    ) -> tuple[VideoPrompt, None, QuickVideoJob, GenerationOutcome, ArchivedAsset, dict]:
        error = str(exc).strip() or exc.__class__.__name__
        job = QuickVideoJob(video.index, f"iteration-failed-{video.index}", status="failed", prompt=video.prompt, error=error)
        outcome = GenerationOutcome(video.index, job.job_id, "failed", "failed", [error], {"kind": "iteration_failure"})
        archive = ArchivedAsset(video.index, job.job_id, self.pipeline.config.archive_backend, "failed", error=error)
        mark_job_failed(session_id, video.index, error, job_id=job.job_id)
        return video, None, job, outcome, archive, {}

    def _append_skipped(self, state: _AdaptiveState, videos: list[VideoPrompt], reason: str) -> None:
        for video in videos:
            guidance = dict(video.keyword_guidance or {})
            guidance["iterationSkipped"] = {"reason": reason}
            skipped_video = replace(video, keyword_guidance=guidance)
            job_id = f"iteration-skipped-{video.index}"
            state.videos.append(skipped_video)
            state.jobs.append(QuickVideoJob(video.index, job_id, status="skipped", prompt=video.prompt, error=reason))
            state.outcomes.append(GenerationOutcome(video.index, job_id, "skipped", "skipped", [reason]))
            state.archives.append(ArchivedAsset(video.index, job_id, self.pipeline.config.archive_backend, "failed", error=reason))


def _generated_review(video: VideoPrompt) -> dict[str, Any]:
    guidance = video.keyword_guidance if isinstance(video.keyword_guidance, dict) else {}
    review = guidance.get("generated_output_review")
    return dict(review) if isinstance(review, dict) else {}


def _review_is_usable(review: dict[str, Any]) -> bool:
    return str(review.get("status") or "") in {"completed", "partial", "simulated"}


def _stop_reason(outcome: GenerationOutcome, archive: ArchivedAsset, review: dict[str, Any]) -> str | None:
    if outcome.decision != "generated":
        return "前一条视频生成失败，后续迭代已停止，避免继续盲目提交。"
    if archive.status not in {"archived", "stored", "simulated", "disabled"}:
        return "前一条视频本地后处理或归档失败，后续迭代已停止。"
    if not _review_is_usable(review):
        return "前一条成片审查不可用，后续视频已停止，避免在没有反馈时继续生成。"
    return None


def _feedback_instruction(source_index: int, review: dict[str, Any]) -> str:
    issues = list(review.get("issues") or [])[:5]
    improvements = list(review.get("improvements") or [])[:5]
    constraints = list(review.get("nextPromptConstraints") or [])[:5]
    if not improvements and not constraints:
        return ""
    return (
        f"上一条（第 {source_index} 条）成片审查发现的问题：{'；'.join(issues) or '无明确阻塞问题'}。\n"
        f"可迁移优化：{'；'.join(improvements) or '无'}。\n"
        f"下一条执行约束：{'；'.join(constraints) or '沿用可迁移优化'}。\n"
        "只吸收可迁移的镜头与质量经验；必须保留当前这条自己的主题、场景和差异化内容。"
    )


def _iteration_guidance(source_index: int, review: dict[str, Any], applied: bool) -> dict[str, Any]:
    return {
        "sourceVideoIndex": source_index,
        "applied": applied,
        "sourceReviewStatus": review.get("status"),
        "appliedConstraints": list(review.get("nextPromptConstraints") or [])[:5],
        "appliedImprovements": list(review.get("improvements") or [])[:5],
    }
