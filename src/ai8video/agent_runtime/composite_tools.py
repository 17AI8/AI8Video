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
from ai8video.assets.default_reference_image import (
    default_reference_image_custom_prompt,
    default_reference_image_path,
    enabled_default_reference_image_options,
)
from ai8video.assets.user_materials import expand_material_mentions
from ai8video.agent_runtime.generation_observations import (
    aggregate_generation_observations,
    build_failed_generation_observation,
    build_generation_observation,
    retryable_failed_video_indexes,
    terminal_generation_observations,
)
from ai8video.core.models import ParsedRequest, VideoPrompt
from ai8video.generation.business_prompt import read_business_prompt
from ai8video.generation.generation_batch_context import (
    reset_current_generation_batch_id,
    reset_current_generation_session_id,
    set_current_generation_batch_id,
    set_current_generation_session_id,
)
from ai8video.generation.generation_mode import (
    default_concurrent_generation_enabled,
    default_manual_video_count,
    default_smart_split_confirmation_enabled,
    default_smart_split_enabled,
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
from ai8video.knowledge.default_script_reference import (
    apply_default_script_reference,
    apply_retrieved_temporary_script_knowledge,
    load_default_script_reference,
    split_temporary_script_knowledge,
)
from ai8video.media.background_music import background_music_track_status
from ai8video.media.motion.html_motion_overlay import default_html_motion_overlay_enabled
from ai8video.media.video_text_overlay import video_text_overlay_status


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
        pipeline = self.pipeline_factory(conversation.get("modelBinding") or {})
        run_context = get_run_context(self.journal.path, run_id)
        planning_input = str(run_context.get("planningInput") or goal).strip()
        control_text, temporary_knowledge = split_temporary_script_knowledge(planning_input)
        planning_text = self._merge_agent_goal(control_text, goal)
        constraints = [
            str(item).strip()
            for item in arguments.get("constraints") or []
            if str(item).strip()
        ]
        if constraints:
            planning_text += "\n\nAgent 识别的任务补充约束：\n- " + "\n- ".join(constraints[:20])
        planning_text, material_context = expand_material_mentions(planning_text)
        planning_text, material_context = apply_default_script_reference(
            planning_text,
            material_context,
            prefer_full=False,
            rerank_llm=getattr(pipeline, "script_rerank_llm", None),
            query_llm=getattr(pipeline, "script_query_llm", None),
        )
        if temporary_knowledge:
            planning_text = apply_retrieved_temporary_script_knowledge(
                planning_text,
                temporary_knowledge,
                query_text=control_text,
                query_llm=getattr(pipeline, "script_query_llm", None),
                rerank_llm=getattr(pipeline, "script_rerank_llm", None),
            )

        smart_split = default_smart_split_enabled()
        agent_video_count = self._optional_count(arguments.get("videoCount"))
        video_count = agent_video_count if smart_split else default_manual_video_count()
        tail_frame_chaining = default_tail_frame_chaining_enabled() if smart_split else False
        concurrent_generation = default_concurrent_generation_enabled() and not tail_frame_chaining
        reference_image = self._selected_reference_image(material_context)
        request = ParsedRequest(
            raw_text=planning_text,
            mode="batch_videos" if smart_split or int(video_count or 1) > 1 else "single_video",
            video_count=video_count,
            reference_image=reference_image,
            reference_image_custom_prompt=(
                default_reference_image_custom_prompt() if reference_image else None
            ),
            style_hint=self._optional_text(arguments.get("styleHint")),
            core_keywords=self._optional_text(arguments.get("coreKeywords")),
            duration_seconds=self._optional_positive_int(
                arguments.get("durationSeconds"),
                field_name="durationSeconds",
                default=10,
            ),
            ratio=self._optional_text(arguments.get("ratio")) or "9:16",
            resolution=self._optional_text(arguments.get("resolution")) or "480p",
            preset=self._optional_text(arguments.get("preset")) or "custom",
            concurrent_generation=concurrent_generation,
            tail_frame_chaining=tail_frame_chaining,
            tail_frame_chaining_mode=default_tail_frame_chaining_mode(),
            html_motion_overlay_enabled=default_html_motion_overlay_enabled(),
            reference_image_transform_options=(
                enabled_default_reference_image_options() if reference_image else None
            ),
        )
        videos = pipeline.plan_request(
            request,
            progress_session_id=conversation["id"],
            smart_split=smart_split,
            smart_split_count_locked=smart_split and agent_video_count is not None,
        )
        shared_settings = self._shared_settings_snapshot(
            request,
            smart_split=smart_split,
            manual_video_count=default_manual_video_count(),
        )
        payload = {
            "status": "completed",
            "request": asdict(request),
            "videos": [asdict(video) for video in videos],
            "videoCount": len(videos),
            "sharedSettings": shared_settings,
            "requiresUserConfirmation": (
                smart_split and default_smart_split_confirmation_enabled()
            ),
            "materials": {
                "imageCount": len(material_context.get("images") or []),
                "scriptCount": len(material_context.get("scripts") or []),
            },
            "summary": f"已准备 {len(videos)} 条视频方案。",
        }
        update_run_context(
            self.journal.path,
            run_id,
            {
                "objective": goal,
                "plannedVideoCount": len(videos),
                "planStatus": "prepared",
                "sharedToolbarSettings": shared_settings,
                "planRequiresUserConfirmation": payload["requiresUserConfirmation"],
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
    def _optional_positive_int(value: Any, *, field_name: str, default: int) -> int:
        if value is None or str(value).strip() == "":
            return default
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise ConversationStoreError(
                "agent_action_input_invalid",
                f"{field_name} 必须是正整数。",
                status=400,
            ) from exc
        if number < 1:
            raise ConversationStoreError(
                "agent_action_input_invalid",
                f"{field_name} 必须是正整数。",
                status=400,
            )
        return number

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _merge_agent_goal(control_text: str, goal: str) -> str:
        source = str(control_text or "").strip()
        objective = str(goal or "").strip()
        if not source:
            return objective
        if not objective or objective == source or objective in source:
            return source
        if source in objective:
            return objective
        return f"{source}\n\nAgent 对本轮任务的理解：\n{objective}"

    @staticmethod
    def _selected_reference_image(
        material_context: dict[str, Any],
    ) -> str | None:
        images = material_context.get("images") if isinstance(material_context, dict) else []
        if isinstance(images, list):
            for item in images:
                if isinstance(item, dict) and str(item.get("path") or "").strip():
                    return str(item["path"]).strip()
        return default_reference_image_path()

    @staticmethod
    def _shared_settings_snapshot(
        request: ParsedRequest,
        *,
        smart_split: bool,
        manual_video_count: int,
    ) -> dict[str, Any]:
        return {
            "systemPromptEnabled": bool(read_business_prompt().strip()),
            "backgroundMusicEnabled": bool(background_music_track_status().get("enabled")),
            "referenceImageEnabled": bool(request.reference_image),
            "knowledgeReferenceEnabled": bool(load_default_script_reference()),
            "flowerTextEnabled": bool(video_text_overlay_status().get("enabled")),
            "concurrentGeneration": bool(request.concurrent_generation),
            "splitMode": "smart" if smart_split else "manual",
            "manualVideoCount": int(manual_video_count),
            "tailFrameChaining": bool(request.tail_frame_chaining),
            "tailFrameChainingMode": request.tail_frame_chaining_mode,
            "htmlMotionOverlayEnabled": bool(request.html_motion_overlay_enabled),
        }

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
