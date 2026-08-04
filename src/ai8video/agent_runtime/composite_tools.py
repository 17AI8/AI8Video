"""High-level tools shared by the main Agent and deterministic Runtime."""

from __future__ import annotations

from dataclasses import asdict, fields
from typing import Any, Callable

from ai8video.application.agent_context import (
    get_run_context,
    latest_successful_action_output,
    update_run_context,
)
from ai8video.application.agent_journal import AgentJournal
from ai8video.application.conversation_store import ConversationStoreError
from ai8video.agent_runtime.bound_runtime import build_bound_pipeline
from ai8video.agent_runtime.generation_observations import (
    aggregate_generation_observations,
    build_failed_generation_observation,
    build_generation_observation,
    retryable_failed_video_indexes,
    terminal_generation_observations,
)
from ai8video.core.models import ParsedRequest, VideoPrompt
from ai8video.application.message_parser import parse_employee_message
from ai8video.generation.generation_batch_context import (
    reset_current_generation_batch_id,
    reset_current_generation_session_id,
    set_current_generation_batch_id,
    set_current_generation_session_id,
)
from ai8video.generation.generation_mode import (
    default_concurrent_generation_enabled,
    default_tail_frame_chaining_enabled,
    default_tail_frame_chaining_mode,
)
from ai8video.generation.generation_progress import (
    claim_generation_batch,
    clear_generation_progress,
    create_generation_batch_id,
    record_generation_execution,
    register_generation_child_batch,
)
from ai8video.generation.output_review import review_final_outputs


TerminalCallback = Callable[[str], None]

PREPARE_VIDEO_PLAN_TOOL = "prepare_video_plan"
REVIEW_VIDEO_PLAN_TOOL = "review_video_plan"
GENERATE_VIDEO_BATCH_TOOL = "generate_video_batch"
INSPECT_GENERATION_RESULT_TOOL = "inspect_generation_result"
ARCHIVE_AND_DELIVER_TOOL = "archive_and_deliver"
TASK_USER_TOOL = "task_user"
AGENT_COMPOSITE_TOOL_NAMES = (
    PREPARE_VIDEO_PLAN_TOOL,
    REVIEW_VIDEO_PLAN_TOOL,
    GENERATE_VIDEO_BATCH_TOOL,
    INSPECT_GENERATION_RESULT_TOOL,
    ARCHIVE_AND_DELIVER_TOOL,
    TASK_USER_TOOL,
)


class AgentCompositeTools:
    def __init__(
        self,
        journal: AgentJournal,
        *,
        terminal_callback: TerminalCallback | None = None,
        pipeline_factory: Callable[[dict[str, Any]], Any] = build_bound_pipeline,
        task_starter: Callable[..., Any] | None = None,
    ) -> None:
        self.journal = journal
        self.terminal_callback = terminal_callback
        self.pipeline_factory = pipeline_factory
        self.task_starter = task_starter or self._default_task_starter

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        run_id: str,
        action_id: str,
        conversation: dict[str, Any],
    ) -> dict[str, Any]:
        handlers = {
            PREPARE_VIDEO_PLAN_TOOL: self._prepare_video_plan,
            REVIEW_VIDEO_PLAN_TOOL: self._review_video_plan,
            GENERATE_VIDEO_BATCH_TOOL: self._generate_video_batch,
            INSPECT_GENERATION_RESULT_TOOL: self._inspect_generation_result,
            ARCHIVE_AND_DELIVER_TOOL: self._archive_and_deliver,
            TASK_USER_TOOL: self._task_user,
        }
        handler = handlers.get(tool_name)
        if handler is None:
            raise ConversationStoreError("agent_tool_not_allowed", f"不支持 Agent 工具：{tool_name}")
        return handler(
            arguments,
            run_id=run_id,
            action_id=action_id,
            conversation=conversation,
        )

    def _prepare_video_plan(
        self,
        arguments: dict[str, Any],
        *,
        run_id: str,
        action_id: str,
        conversation: dict[str, Any],
    ) -> dict[str, Any]:
        del action_id
        goal = str(arguments.get("goal") or "").strip()
        if not goal:
            raise ConversationStoreError("agent_goal_required", "准备视频方案需要明确目标。", status=400)
        request = parse_employee_message(goal)
        requested_count = self._optional_count(arguments.get("videoCount"))
        if requested_count is not None:
            request.video_count = requested_count
            request.mode = "batch_videos" if requested_count > 1 else "single_video"
        elif request.video_count is None:
            request.video_count = 1
            request.mode = "single_video"
        request.concurrent_generation = default_concurrent_generation_enabled()
        request.tail_frame_chaining = default_tail_frame_chaining_enabled()
        request.tail_frame_chaining_mode = default_tail_frame_chaining_mode()
        pipeline = self.pipeline_factory(conversation.get("modelBinding") or {})
        videos = pipeline.plan_request(
            request,
            progress_session_id=conversation["id"],
            smart_split=bool(request.video_count and request.video_count > 1),
            smart_split_count_locked=request.video_count is not None,
        )
        payload = {
            "status": "completed",
            "request": asdict(request),
            "videos": [asdict(video) for video in videos],
            "videoCount": len(videos),
            "summary": f"已准备 {len(videos)} 条视频方案。",
        }
        update_run_context(
            self.journal.path,
            run_id,
            {
                "objective": goal,
                "requestedVideoCount": int(request.video_count or len(videos) or 1),
                "planStatus": "prepared",
            },
        )
        return payload

    def _review_video_plan(
        self,
        arguments: dict[str, Any],
        *,
        run_id: str,
        action_id: str,
        conversation: dict[str, Any],
    ) -> dict[str, Any]:
        del arguments, action_id
        plan = self._required_plan(run_id, reviewed=False)
        videos = self._videos_from_payload(plan.get("videos"))
        pipeline = self.pipeline_factory(conversation.get("modelBinding") or {})
        reviewed = review_final_outputs(
            videos,
            llm=getattr(pipeline, "llm", None),
            trace_session_id=conversation["id"],
        )
        changes = sum(
            1 for before, after in zip(videos, reviewed)
            if before.prompt != after.prompt
        )
        issues = []
        advisories = []
        for video in reviewed:
            review = (video.keyword_guidance or {}).get("post_review") or {}
            issues.extend(str(item) for item in review.get("violations") or [])
            advisories.extend(str(item) for item in review.get("userAdvisories") or [])
        payload = {
            "status": "completed",
            "verdict": "accept" if all(video.prompt.strip() for video in reviewed) else "reject",
            "issues": list(dict.fromkeys(issues))[:20],
            "suggestedChanges": [],
            "evidence": [f"审核 {len(reviewed)} 条方案", f"自动修正 {changes} 条"],
            "userAdvisories": list(dict.fromkeys(advisories))[:10],
            "request": plan.get("request") or {},
            "videos": [asdict(video) for video in reviewed],
            "videoCount": len(reviewed),
        }
        update_run_context(
            self.journal.path,
            run_id,
            {"planStatus": "reviewed", "reviewVerdict": payload["verdict"]},
        )
        return payload

    def _generate_video_batch(
        self,
        arguments: dict[str, Any],
        *,
        run_id: str,
        action_id: str,
        conversation: dict[str, Any],
    ) -> dict[str, Any]:
        plan = self._required_plan(run_id, reviewed=True)
        videos = self._videos_from_payload(plan.get("videos"))
        retry_failed_only = bool(arguments.get("retryFailedOnly"))
        previous_batch_id = str(get_run_context(self.journal.path, run_id).get("generationBatchId") or "").strip()
        if retry_failed_only:
            failed_indexes = retryable_failed_video_indexes(self.journal.list_observations(run_id))
            videos = [video for video in videos if int(video.index) in failed_indexes]
            if not videos:
                raise ConversationStoreError(
                    "agent_no_retryable_failures",
                    "当前没有可安全重试的失败视频。",
                )
        count = int(arguments.get("count") or len(videos))
        if count != len(videos):
            raise ConversationStoreError(
                "agent_plan_count_mismatch",
                "生成数量必须与已审核方案数量一致。",
                details={"planned": len(videos), "requested": count},
            )
        request_data = plan.get("request") if isinstance(plan.get("request"), dict) else {}
        request_model = self._request_from_payload(request_data)
        request_model.video_count = len(videos)
        request_model.mode = "batch_videos" if len(videos) > 1 else "single_video"
        batch_id = create_generation_batch_id(conversation["id"])
        if retry_failed_only:
            clear_generation_progress(conversation["id"])
            if previous_batch_id:
                register_generation_child_batch(
                    session_id=conversation["id"],
                    generation_batch_id=batch_id,
                    parent_generation_batch_id=previous_batch_id,
                    batch_kind="agent_failed_retry",
                    task_type="agent_video_generation",
                    request_snapshot={"agentRunId": run_id, "agentActionId": action_id},
                )
        claim_generation_batch(conversation["id"], batch_id)
        record_generation_execution(
            session_id=conversation["id"],
            generation_batch_id=batch_id,
            execution_state="queued",
            request_snapshot={
                "agentRunId": run_id,
                "agentActionId": action_id,
                "videoCount": len(videos),
                "retryFailedOnly": retry_failed_only,
                "parentGenerationBatchId": previous_batch_id or None,
            },
        )
        update_run_context(
            self.journal.path,
            run_id,
            {
                "generationBatchId": batch_id,
                "generationStatus": "queued",
                "activeActionId": action_id,
                "retryFailedOnly": retry_failed_only,
            },
        )
        task = self.task_starter(
            conversation["id"],
            batch_id,
            self._run_generation,
            args=(
                run_id,
                action_id,
                conversation,
                request_model,
                videos,
            ),
        )
        return {
            "status": "pending",
            "runtimeState": getattr(task, "state", "queued"),
            "generationBatchId": batch_id,
            "actionId": action_id,
            "videoCount": len(videos),
            "retryFailedOnly": retry_failed_only,
            "summary": "视频批次已交给 Runtime；后续轮询不会触发主 Agent 决策。",
        }

    def _run_generation(
        self,
        task,
        run_id: str,
        action_id: str,
        conversation: dict[str, Any],
        request_model: ParsedRequest,
        videos: list[VideoPrompt],
    ) -> None:
        batch_id = str(task.generation_batch_id)
        batch_token = set_current_generation_batch_id(batch_id)
        session_token = set_current_generation_session_id(conversation["id"])
        try:
            record_generation_execution(
                session_id=conversation["id"],
                generation_batch_id=batch_id,
                execution_state="running",
                worker_id=getattr(task, "worker_id", None),
            )
            pipeline = self.pipeline_factory(conversation.get("modelBinding") or {})
            result = pipeline.run_planned_request(
                request_model,
                videos,
                progress_session_id=conversation["id"],
            )
            payload = result.to_dict()
            observation = build_generation_observation(payload, batch_id)
            self.journal.complete_action(action_id, payload)
            self.journal.record_observation(
                run_id,
                action_id=action_id,
                kind="generation_terminal",
                state=observation["status"],
                payload=observation,
                terminal=True,
            )
            update_run_context(
                self.journal.path,
                run_id,
                {
                    "generationStatus": observation["status"],
                    "generatedVideoCount": observation["successCount"],
                    "failedVideoCount": observation["failedCount"],
                    "latestGenerationObservation": observation,
                },
            )
            record_generation_execution(
                session_id=conversation["id"],
                generation_batch_id=batch_id,
                execution_state="completed",
                worker_id=getattr(task, "worker_id", None),
                result_snapshot={
                    "successCount": observation["successCount"],
                    "failedCount": observation["failedCount"],
                },
            )
        except Exception as exc:
            observation = build_failed_generation_observation(
                batch_id=batch_id,
                video_indexes=(video.index for video in videos),
                error=str(exc),
            )
            self.journal.complete_action(action_id, observation)
            self.journal.record_observation(
                run_id,
                action_id=action_id,
                kind="generation_terminal",
                state="failed",
                payload=observation,
                terminal=True,
            )
            update_run_context(
                self.journal.path,
                run_id,
                {"generationStatus": "failed", "latestGenerationObservation": observation},
            )
            record_generation_execution(
                session_id=conversation["id"],
                generation_batch_id=batch_id,
                execution_state="failed",
                worker_id=getattr(task, "worker_id", None),
                error=exc,
            )
        finally:
            reset_current_generation_session_id(session_token)
            reset_current_generation_batch_id(batch_token)
            if self.terminal_callback:
                self.terminal_callback(run_id)

    def _inspect_generation_result(
        self,
        arguments: dict[str, Any],
        *,
        run_id: str,
        action_id: str,
        conversation: dict[str, Any],
    ) -> dict[str, Any]:
        del arguments, action_id, conversation
        observations = terminal_generation_observations(self.journal.list_observations(run_id))
        if not observations:
            raise ConversationStoreError("agent_generation_not_terminal", "当前还没有可检查的终态生成结果。")
        generation = aggregate_generation_observations(observations)
        return {
            "status": "completed",
            "generation": generation,
            "summary": self._result_summary(generation),
        }

    def _archive_and_deliver(
        self,
        arguments: dict[str, Any],
        *,
        run_id: str,
        action_id: str,
        conversation: dict[str, Any],
    ) -> dict[str, Any]:
        del action_id, conversation
        inspection = latest_successful_action_output(
            self.journal.path,
            run_id,
            ("inspect_generation_result",),
        )
        if not inspection:
            raise ConversationStoreError("agent_inspection_required", "交付前必须先检查生成结果。")
        generation = inspection.get("generation") if isinstance(inspection.get("generation"), dict) else {}
        success_count = int(generation.get("successCount") or 0)
        failed_count = int(generation.get("failedCount") or 0)
        if failed_count and not bool(arguments.get("includePartialSuccess")):
            raise ConversationStoreError(
                "agent_partial_delivery_requires_choice",
                "当前只有部分结果成功，需要用户确认是否先交付成功部分。",
            )
        payload = {
            "status": "completed",
            "deliveryState": "partial" if failed_count else "complete",
            "successCount": success_count,
            "failedCount": failed_count,
            "assets": generation.get("assets") or [],
            "publishedExternally": False,
            "summary": self._result_summary(generation),
        }
        update_run_context(
            self.journal.path,
            run_id,
            {"deliveryStatus": payload["deliveryState"], "finalDelivery": payload},
        )
        return payload

    def _task_user(
        self,
        arguments: dict[str, Any],
        *,
        run_id: str,
        action_id: str,
        conversation: dict[str, Any],
    ) -> dict[str, Any]:
        del action_id, conversation
        payload = {
            "status": "waiting_user",
            "question": str(arguments.get("question") or "").strip(),
            "reason": str(arguments.get("reason") or "").strip(),
            "choices": [str(item) for item in arguments.get("choices") or []][:6],
        }
        update_run_context(self.journal.path, run_id, {"pendingUserQuestion": payload})
        return payload

    def _required_plan(self, run_id: str, *, reviewed: bool) -> dict[str, Any]:
        tools = ("review_video_plan",) if reviewed else ("review_video_plan", "prepare_video_plan")
        plan = latest_successful_action_output(self.journal.path, run_id, tools)
        if not plan:
            code = "agent_plan_review_required" if reviewed else "agent_plan_required"
            message = "生成前必须先完成方案审核。" if reviewed else "当前还没有可用的视频方案。"
            raise ConversationStoreError(code, message)
        return plan

    @staticmethod
    def _request_from_payload(payload: dict[str, Any]) -> ParsedRequest:
        allowed = {item.name for item in fields(ParsedRequest)}
        clean = {key: value for key, value in payload.items() if key in allowed}
        return ParsedRequest(**clean)

    @staticmethod
    def _videos_from_payload(payload: Any) -> list[VideoPrompt]:
        if not isinstance(payload, list):
            raise ConversationStoreError("agent_plan_invalid", "视频方案结构无效。")
        videos = [VideoPrompt(**item) for item in payload if isinstance(item, dict)]
        if not videos:
            raise ConversationStoreError("agent_plan_invalid", "视频方案为空。")
        return videos

    @staticmethod
    def _optional_count(value: Any) -> int | None:
        if value is None:
            return None
        count = int(value)
        if count < 1:
            raise ConversationStoreError("agent_video_count_invalid", "视频数量必须是正整数。")
        return count

    @staticmethod
    def _result_summary(payload: dict[str, Any]) -> str:
        success = int(payload.get("successCount") or 0)
        failed = int(payload.get("failedCount") or 0)
        if success and failed:
            return f"本轮 {success} 条成功、{failed} 条失败，可先交付成功部分。"
        if success:
            return f"本轮 {success} 条视频已完成并归档。"
        return f"本轮生成失败 {failed or 1} 条，需要决定是否调整方案后重试。"

    @staticmethod
    def _default_task_starter(session_id: str, batch_id: str, target, *, args: tuple[Any, ...]):
        from ai8video.application.facade import start_external_generation_task

        return start_external_generation_task(
            session_id=session_id,
            generation_batch_id=batch_id,
            target=target,
            args=args,
        )
