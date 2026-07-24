from __future__ import annotations

import re
import logging
import inspect

from ai8video.application.message_parser import (
    detect_concurrent_generation_decision,
    detect_html_motion_overlay_decision,
    detect_batch_mode,
    detect_mode_hint,
    detect_reference_decision,
    detect_rewrite_instruction,
    extract_batch_seed_messages,
    extract_batch_target_count,
    extract_duration_seconds,
    extract_video_count,
    extract_video_index,
    extract_core_keywords,
    extract_reference_image,
    extract_style_hint,
)
from ai8video.assets.user_materials import expand_material_mentions
from ai8video.assets.default_reference_image import (
    default_reference_image_custom_prompt,
    default_reference_image_path,
    enabled_default_reference_image_options,
)
from ai8video.knowledge.default_script_reference import (
    apply_default_script_reference,
    split_temporary_script_knowledge,
)
from ai8video.generation.generation_mode import (
    default_concurrent_generation_enabled,
    default_smart_split_confirmation_enabled,
    default_smart_split_enabled,
    default_tail_frame_chaining_enabled,
)
from ai8video.media.motion.html_motion_overlay import default_html_motion_overlay_enabled
from ai8video.batch.daily_batch_runner import DailyBatchRunner
from ai8video.core.models import ChatReply, ConversationState, VideoPrompt, ParsedRequest
from ai8video.generation.pipeline import AI8VideoPipeline
from ai8video.application.request_interpreter import interpret_generation_request_with_ai
from ai8video.media.video_merge_mode import load_video_merge_mode, normalize_video_merge_mode

logger = logging.getLogger(__name__)


class AI8VideoConversationController:
    """围绕短视频流水线维护会话状态并分发业务操作。"""

    def __init__(
        self,
        pipeline: AI8VideoPipeline,
        *,
        merged_pipeline_factory=None,
        merge_mode_loader=None,
    ):
        self.pipeline = pipeline
        self._merged_pipeline = None
        self._merged_pipelines: dict[str, object] = {}
        self._merged_pipeline_factory = merged_pipeline_factory
        self._merge_mode_loader = merge_mode_loader or load_video_merge_mode
        self.sessions: dict[str, ConversationState] = {}

    def get_welcome_reply(self) -> ChatReply:
        return ChatReply(
            text=(
                "把提示词、脚本素材或批量视频需求直接发我；批量生成请写清目标视频数量。"
                "参考图可以下一句再给。如果暂时不用参考图，直接回复“不用参考图”。\n"
                "如果要批量跑量，也可以直接说“今天先跑两条商务风”，"
                "再把候选内容逐行发我，或者一次性发“候选：A；B；C”。"
            ),
            stage="collecting",
            meta={"operation": "welcome"},
        )

    def handle_message(self, session_id: str, message: str) -> ChatReply:
        text, temporary_script_knowledge = split_temporary_script_knowledge(message)
        text = text.strip()
        if not text:
            return ChatReply(
                text="我还没收到内容。直接把提示词、脚本素材、批量视频需求或参考图路径发我就行。",
                stage="collecting",
                meta={"operation": "collect"},
            )
        state = self.sessions.get(session_id)
        if state is None:
            state = ConversationState(session_id=session_id)
            self.sessions[session_id] = state
        if state.awaiting == "smart_split_confirmation":
            return self._handle_smart_split_followup(state, text)
        ai_interpretation = self._interpret_request_with_ai(text)
        if state.completed_runs > 0 and self._is_rewrite_request(text, ai_interpretation):
            return self._handle_rewrite(state, text)
        elif state.completed_runs > 0 and self._looks_like_base_request(text, ai_interpretation):
            state = ConversationState(session_id=session_id)
            self.sessions[session_id] = state

        text, material_context = expand_material_mentions(text)
        control_text = text
        default_script_reference_applied = False
        if self._should_apply_default_script_reference(state, control_text):
            before_text = text
            text, material_context = apply_default_script_reference(
                text,
                material_context,
                prefer_full=self._prefer_full_script_reference(state, control_text),
                rerank_llm=getattr(self.pipeline, "script_rerank_llm", None),
                query_llm=getattr(self.pipeline, "script_query_llm", None),
            )
            default_script_reference_applied = text != before_text
            if default_script_reference_applied:
                ai_interpretation = {}

        if state.awaiting == "batch_seed_messages":
            return self._handle_batch_seed_followup(state, text, ai_interpretation)

        content_completion_satisfied = False
        if state.awaiting == "content_completion":
            content_completion_satisfied = self._handle_content_completion_followup(state, text, ai_interpretation)

        if state.awaiting == "core_keywords":
            self._handle_core_keywords_followup(state, text, ai_interpretation)

        if state.awaiting == "concurrent_generation":
            decision = (ai_interpretation or {}).get("concurrent_generation")
            if decision is None:
                decision = detect_concurrent_generation_decision(text)
            if decision is not None:
                state.draft.concurrent_generation = decision
                state.awaiting = None

        if not default_script_reference_applied and self._is_batch_request(text, ai_interpretation):
            return self._handle_batch_request(state, text, ai_interpretation)

        if temporary_script_knowledge:
            text = f"{text.rstrip()}\n\n{temporary_script_knowledge}"
        self._merge_message(state, text, control_text=control_text, ai_interpretation=ai_interpretation)

        if not state.draft.raw_text:
            state.awaiting = "raw_text"
            return ChatReply(
                text=self._prepend_material_receipt(
                    "先把提示词或脚本素材发我；如果要批量生成，请顺手写清楚要做几条独立视频。",
                    material_context,
                ),
                stage="collecting",
                awaiting=state.awaiting,
                draft=state.draft,
                meta={"operation": "collect"},
            )

        if (
            state.draft.mode == "batch_videos"
            and not state.draft.video_count
            and not default_smart_split_enabled()
        ):
            state.awaiting = "video_count"
            return ChatReply(
                text=self._prepend_material_receipt(
                    "收到素材了。你希望生成几条独立视频？直接回复“6 条”或只回数字都可以。",
                    material_context,
                ),
                stage="collecting",
                awaiting=state.awaiting,
                draft=state.draft,
                meta={"operation": "collect"},
            )

        missing_requirements = self._detect_missing_requirements(
            state,
            material_context,
            ai_interpretation,
            content_completion_satisfied=content_completion_satisfied,
        )
        if missing_requirements:
            self._apply_smart_dialogue_completion(state)
            content_completion_satisfied = True

        self._apply_default_reference_image(state)

        if state.draft.reference_image_enabled is None:
            state.draft.reference_image_enabled = False

        if state.draft.reference_image_enabled and not state.draft.reference_image:
            state.awaiting = "reference_image"
            return ChatReply(
                text=(
                    "你明确要求使用参考图，但参考图标签页当前没有选中图片。"
                    "请先在标签页选择，或把桌面文件名、本地路径或图片地址发我。"
                ),
                stage="collecting",
                awaiting=state.awaiting,
                draft=state.draft,
                meta={"operation": "collect"},
            )

        if not content_completion_satisfied and self._needs_core_keywords(state, material_context, ai_interpretation):
            state.awaiting = "core_keywords"
            return self._build_core_keywords_reply(state, material_context)

        state.awaiting = None
        self._apply_default_generation_mode(state)
        self._apply_default_html_motion_overlay(state)
        request = state.draft.to_request()
        if default_smart_split_enabled() and self._supports_planned_generation():
            state.planned_videos = self._plan_generation_request(
                request,
                progress_session_id=state.session_id,
                smart_split=True,
            )
            state.draft.video_count = len(state.planned_videos)
            state.draft.mode = "batch_videos" if len(state.planned_videos) > 1 else "single_video"
            if default_smart_split_confirmation_enabled():
                state.awaiting = "smart_split_confirmation"
                return self._build_smart_split_confirmation_reply(state)
            request = state.draft.to_request()
            result = self._run_planned_generation_request(
                request,
                state.planned_videos,
                progress_session_id=state.session_id,
            )
        else:
            result = self._run_generation_request(request, progress_session_id=state.session_id)
        state.last_result = result.to_dict()
        state.completed_runs += 1
        return ChatReply(
            text=self._prepend_material_receipt(
                self._build_generation_result_text(state, result),
                material_context,
            ),
            stage="completed",
            draft=state.draft,
            result=result,
            meta={"operation": "generate", "materials": material_context},
        )

    def _handle_smart_split_followup(self, state: ConversationState, text: str) -> ChatReply:
        compact = re.sub(r"\s+", "", text)
        if compact in {"确认分集", "确认并继续", "确认", "继续生成", "开始生成"}:
            if not state.planned_videos:
                raise RuntimeError("智能分集方案已失效，请重新发送原始需求")
            state.awaiting = None
            request = state.draft.to_request()
            result = self._run_planned_generation_request(
                request,
                state.planned_videos,
                progress_session_id=state.session_id,
            )
            state.last_result = result.to_dict()
            state.completed_runs += 1
            return ChatReply(
                text=self._build_generation_result_text(state, result),
                stage="completed",
                draft=state.draft,
                result=result,
                meta={"operation": "generate"},
            )
        if compact not in {"重新分集", "重分", "重新规划"}:
            state.draft.raw_text = self._append_draft_text(
                state.draft.raw_text,
                f"分集调整要求：{text}",
            )
        request = state.draft.to_request()
        state.planned_videos = self._plan_generation_request(
            request,
            progress_session_id=state.session_id,
            smart_split=True,
        )
        state.draft.video_count = len(state.planned_videos)
        state.draft.mode = "batch_videos" if len(state.planned_videos) > 1 else "single_video"
        return self._build_smart_split_confirmation_reply(state)

    def _build_smart_split_confirmation_reply(self, state: ConversationState) -> ChatReply:
        lines = [f"已智能分为 {len(state.planned_videos)} 集："]
        for video in state.planned_videos:
            summary = video.source_summary or "已生成独立内容方案"
            lines.append(f"{video.index}. {video.title}：{summary}")
        lines.append("确认后进入视频生成；也可以直接回复调整要求，我会重新分集。")
        return ChatReply(
            text="\n".join(lines),
            stage="collecting",
            awaiting="smart_split_confirmation",
            draft=state.draft,
            meta={
                "operation": "collect",
                "guide": {
                    "kind": "smart_split_confirmation",
                    "title": "确认智能分集",
                    "summary": f"Planner 已完成 {len(state.planned_videos)} 集规划。",
                    "actions": [
                        {"kind": "send", "label": "确认并继续", "value": "确认分集"},
                        {"kind": "send", "label": "重新分集", "value": "重新分集"},
                    ],
                },
            },
        )

    def _build_generation_result_text(self, state: ConversationState, result) -> str:
        jobs = result.jobs or []
        succeeded = sum(
            1
            for job in jobs
            if str(job.status or "").strip().lower() in {"succeeded", "completed"}
            and (job.video_url or job.local_video_path or job.storage_key)
        )
        total = len(result.videos or [])
        failed = max(0, total - succeeded)
        prefix = f"{self._build_scope_summary(state)}，{self._build_reference_summary(state)}。"
        if failed and succeeded:
            return f"{prefix}本轮 {succeeded} 条已生成，{failed} 条生成失败，下面是结果。"
        if failed:
            return f"{prefix}本轮 {failed} 条生成失败，下面是结果。"
        return f"{prefix}本轮已经完成视频方案规划、传图、创建任务和归档，下面是结果。"

    def _handle_content_completion_followup(
        self,
        state: ConversationState,
        text: str,
        ai_interpretation: dict | None = None,
    ) -> bool:
        if not text:
            return False
        if self._is_smart_dialogue_request(text, ai_interpretation):
            addition = self._build_smart_dialogue_instruction(state)
        else:
            addition = f"补充台词/口播文案：\n{text.strip()}"
        state.draft.raw_text = self._append_draft_text(state.draft.raw_text, addition)
        state.awaiting = None
        state.draft.content_completion_mode = None
        return True

    def _apply_smart_dialogue_completion(self, state: ConversationState) -> None:
        state.draft.raw_text = self._append_draft_text(
            state.draft.raw_text,
            self._build_smart_dialogue_instruction(state),
        )
        state.awaiting = None
        state.draft.content_completion_mode = None

    def _handle_core_keywords_followup(
        self,
        state: ConversationState,
        text: str,
        ai_interpretation: dict | None = None,
    ) -> None:
        if not text:
            return
        if self._ai_intent(ai_interpretation) == "core_keywords_followup" and (ai_interpretation or {}).get("core_keywords"):
            state.draft.core_keywords = str((ai_interpretation or {}).get("core_keywords") or "").strip()
        elif re.search(r"^(不用|不需要|没有|跳过|无)\s*(关键词|核心主题|主题)?$", text.strip()):
            state.draft.core_keywords = "按用户提供的原文自行提炼核心主题，但不得偏离用户原始要求"
        elif re.fullmatch(r"\d+\s*(?:个|条|支|段|部|[sS秒分钟]*)?", text.strip()):
            state.draft.core_keywords = "按用户提供的原文自行提炼核心主题，但不得偏离用户原始要求"
        else:
            state.draft.core_keywords = extract_core_keywords(text) or text.strip()
        state.awaiting = None

    def _handle_rewrite(self, state: ConversationState, text: str) -> ChatReply:
        if not state.last_result:
            return ChatReply(
                text="我这里还没有上一轮结果，先生成一轮，再告诉我要重做第几条视频。",
                stage="collecting",
                meta={"operation": "collect"},
            )
        ai_interpretation = self._interpret_request_with_ai(text)
        video_index = (ai_interpretation or {}).get("rewrite_video_index") or extract_video_index(text)
        rewrite_instruction = (ai_interpretation or {}).get("rewrite_instruction") or detect_rewrite_instruction(text) or text
        if not video_index:
            return ChatReply(
                text="你直接说“第 3 条视频重做，改得更像老板真实开会”这种格式就行。",
                stage="collecting",
                meta={"operation": "collect"},
            )

        video = self._find_video(state.last_result, video_index)
        if video is None:
            total = len(state.last_result.get("videos") or [])
            return ChatReply(
                text=f"当前这一轮只有 {total} 条视频，我没找到你说的第 {video_index} 条。你可以换成现有序号继续改。",
                stage="collecting",
                meta={"operation": "collect"},
            )

        request = self._build_rewrite_request(state, text)
        result = self.pipeline.rewrite_video(
            request,
            video,
            rewrite_instruction,
            progress_session_id=state.session_id,
        )
        merged = self._merge_result_payload(state.last_result, result.to_dict())
        state.last_result = merged
        state.completed_runs += 1
        style_hint = (ai_interpretation or {}).get("style_hint") or extract_style_hint(text)
        if style_hint:
            state.draft.style_hint = self._merge_style_hints(state.draft.style_hint, style_hint)
        return ChatReply(
            text=(
                f"收到。我只重做第 {video_index} 条视频，其他视频保持不动。"
                "这次已经按你的修改要求重新生成并回写到当前结果里。"
            ),
            stage="completed",
            draft=state.draft,
            result_payload=merged,
            meta={
                "operation": "rewrite",
                "rewrittenVideoIndex": video_index,
                "rewriteInstruction": rewrite_instruction,
            },
        )

    def _handle_batch_request(
        self,
        state: ConversationState,
        text: str,
        ai_interpretation: dict | None = None,
    ) -> ChatReply:
        target_pass_count = (ai_interpretation or {}).get("batch_target_count") or extract_batch_target_count(text) or 30
        style_hint = (ai_interpretation or {}).get("style_hint") or extract_style_hint(text)
        seed_messages = (ai_interpretation or {}).get("batch_seed_messages") or extract_batch_seed_messages(text)
        state.batch_request = {
            "targetPassCount": target_pass_count,
            "styleHint": style_hint,
        }
        if seed_messages:
            return self._run_batch(state, seed_messages)

        state.awaiting = "batch_seed_messages"
        return ChatReply(
            text=(
                f"收到。我会按 {target_pass_count} 条生成目标来跑一轮批量生产。"
                "现在把候选提示词、候选选题或候选剧本逐行发我，一行一条；"
                "也可以一次性发“候选：A；B；C”。"
            ),
            stage="collecting",
            awaiting=state.awaiting,
            meta={
                "operation": "batch_collect",
                "targetPassCount": target_pass_count,
                "styleHint": style_hint,
            },
        )

    def _handle_batch_seed_followup(
        self,
        state: ConversationState,
        text: str,
        ai_interpretation: dict | None = None,
    ) -> ChatReply:
        seed_messages = (ai_interpretation or {}).get("batch_seed_messages") or extract_batch_seed_messages(text)
        if not seed_messages:
            target_pass_count = int((state.batch_request or {}).get("targetPassCount") or 30)
            return ChatReply(
                text=(
                    f"我还没拿到可执行的候选列表。请把候选提示词或候选剧本逐行发我，"
                    f"这轮默认还是按 {target_pass_count} 条生成目标来跑。"
                ),
                stage="collecting",
                awaiting="batch_seed_messages",
                meta={
                    "operation": "batch_collect",
                    "targetPassCount": target_pass_count,
                },
            )
        return self._run_batch(state, seed_messages)

    def _run_batch(self, state: ConversationState, seed_messages: list[str]) -> ChatReply:
        batch_request = state.batch_request or {}
        target_pass_count = int(batch_request.get("targetPassCount") or 30)
        style_hint = str(batch_request.get("styleHint") or "").strip() or None
        initial_budget = max(len(seed_messages), int(target_pass_count * 1.5))
        max_budget = max(initial_budget, int(target_pass_count * 2))
        runner = DailyBatchRunner(
            pipeline=self.pipeline,
            config=self.pipeline.config,
            target_pass_count=target_pass_count,
            initial_candidate_budget=initial_budget,
            max_candidate_budget=max_budget,
        )
        report = runner.run(
            seed_messages,
            style_hint=style_hint,
            trigger="conversation_controller",
            source="chat",
            session_id=state.session_id,
        )
        state.awaiting = None
        state.batch_request = None
        state.completed_runs += 1
        return ChatReply(
            text=(
                f"收到。我已经按 {len(seed_messages)} 条候选内容跑完一轮批量生产。"
                "下面是本轮的生成、失败和补量统计。"
            ),
            stage="completed",
            result_payload=report,
            meta={
                "operation": "batch_run",
                "targetPassCount": target_pass_count,
                "seedCount": len(seed_messages),
                "styleHint": style_hint,
            },
        )

    def _detect_missing_requirements(
        self,
        state: ConversationState,
        material_context: dict | None,
        ai_interpretation: dict | None = None,
        *,
        content_completion_satisfied: bool = False,
    ) -> list[dict[str, str]]:
        missing: list[dict[str, str]] = []
        if not content_completion_satisfied and self._needs_dialogue_completion(state, material_context, ai_interpretation):
            missing.append({
                "key": "dialogue",
                "label": "台词 / 口播文案",
                "reason": "当前已经识别到生成目标，但还缺少可直接落到视频里的文案或对白。",
            })
        return missing

    def _build_missing_info_reply(self, state: ConversationState, missing_requirements: list[dict[str, str]]) -> ChatReply:
        video_count = state.draft.video_count or 1
        duration = state.draft.duration_seconds or 10
        reference_ready = bool(state.draft.reference_image)
        return ChatReply(
            text=(
                f"{self._build_scope_summary(state)}。当前条数、时长"
                f"{'和参考素材' if reference_ready else ''}我已经记下，"
                "但还缺少可直接生成的视频台词/口播文案。"
                "你可以自己补一句，或者点“AI8智能生成”让我先补齐再继续。"
            ),
            stage="collecting",
            awaiting="content_completion",
            draft=state.draft,
            meta={
                "operation": "collect",
                "guide": {
                    "kind": "missing_info",
                    "title": "生成前先补齐关键信息",
                    "summary": f"已识别 {video_count} 条、{duration} 秒的生成目标，请先补齐台词/口播文案。",
                    "missingFields": missing_requirements,
                    "actions": [
                        {
                            "kind": "fill",
                            "label": "我来补充台词",
                            "value": "补充台词：",
                        },
                        {
                            "kind": "send",
                            "label": "AI8智能生成",
                            "value": "AI8智能生成台词，并继续沿用我刚才的素材、条数、时长和风格要求。",
                        },
                    ],
                },
            },
        )

    @staticmethod
    def _needs_core_keywords(
        state: ConversationState,
        material_context: dict | None = None,
        ai_interpretation: dict | None = None,
    ) -> bool:
        if str((ai_interpretation or {}).get("core_keywords") or "").strip():
            return False
        if (material_context or {}).get("scripts"):
            return False
        if (ai_interpretation or {}).get("needs_core_keywords") is True:
            return True
        if (ai_interpretation or {}).get("needs_core_keywords") is False and AI8VideoConversationController._ai_intent(ai_interpretation) == "generation":
            return False
        return (
            state.draft.mode == "batch_videos"
            and (state.draft.video_count or 0) > 1
            and not str(state.draft.core_keywords or "").strip()
        )

    def _build_core_keywords_reply(self, state: ConversationState, material_context: dict | None = None) -> ChatReply:
        video_count = state.draft.video_count or 2
        return ChatReply(
            text=self._prepend_material_receipt(
                (
                    f"已识别要生成 {video_count} 条视频。为避免核心主题被长文档冲淡，"
                    "请先确认这一轮必须围绕的关键词 / 核心主题。"
                    "例如：“核心主题：新品卖点、目标受众、实际使用场景”。"
                    "如果没有指定，回复“跳过关键词”。"
                ),
                material_context,
            ),
            stage="collecting",
            awaiting="core_keywords",
            draft=state.draft,
            meta={
                "operation": "collect",
                "guide": {
                    "kind": "core_keywords",
                    "title": "确认核心主题",
                    "summary": "先锁定本轮关键词，避免长文档里的重点被平均化。",
                    "actions": [
                        {
                            "kind": "fill",
                            "label": "填写关键词",
                            "value": "核心主题：",
                        },
                        {
                            "kind": "send",
                            "label": "跳过关键词",
                            "value": "跳过关键词",
                        },
                    ],
                },
            },
        )

    def _build_concurrent_generation_reply(self, state: ConversationState, material_context: dict | None = None) -> ChatReply:
        video_count = state.draft.video_count or 2
        return ChatReply(
            text=self._prepend_material_receipt(
                (
                    f"已识别要生成 {video_count} 条视频。要不要开启并发模式？"
                    "并发模式会一次性提交多条生成请求，整体更快；"
                    "普通模式会一条生成完再生成下一条，更稳但更慢。"
                    "直接回复“并发模式”或“普通模式”。"
                ),
                material_context,
            ),
            stage="collecting",
            awaiting="concurrent_generation",
            draft=state.draft,
            meta={
                "operation": "collect",
                "guide": {
                    "kind": "concurrent_generation",
                    "title": "选择生成提交方式",
                    "summary": f"本轮共 {video_count} 条视频。并发更快，普通模式更稳。",
                    "actions": [
                        {
                            "kind": "send",
                            "label": "并发模式",
                            "value": "并发模式",
                        },
                        {
                            "kind": "send",
                            "label": "普通模式",
                            "value": "普通模式",
                        },
                    ],
                },
            },
        )

    @staticmethod
    def _build_material_receipt(material_context: dict | None) -> str:
        if not material_context:
            return ""
        scripts = list(material_context.get("scripts") or [])
        images = list(material_context.get("images") or [])
        lines: list[str] = []
        if scripts:
            script_parts = []
            for item in scripts:
                name = str(item.get("name") or item.get("relativePath") or "剧本素材").strip()
                count = int(item.get("contentCharCount") or 0)
                preview = str(item.get("contentPreview") or item.get("preview") or "").strip()
                detail = f"剧本参考 {name}" if item.get("source") == "defaultScriptReference" else f"@{name}"
                if count > 0:
                    detail += f"（正文约 {count} 字"
                    if preview:
                        detail += f"，开头：{preview}"
                    detail += "）"
                script_parts.append(detail)
            lines.append("已读取剧本素材：" + "；".join(script_parts))
        if images:
            image_names = [f"@{str(item.get('name') or item.get('relativePath') or '图片素材').strip()}" for item in images]
            lines.append("已读取参考图素材：" + "、".join(image_names))
        return "\n".join(lines)

    def _prepend_material_receipt(self, text: str, material_context: dict | None) -> str:
        receipt = self._build_material_receipt(material_context)
        if not receipt:
            return text
        return f"{receipt}\n\n{text}"

    @staticmethod
    def _should_apply_default_script_reference(state: ConversationState, text: str) -> bool:
        if state.awaiting not in {None, "raw_text", "content_completion"}:
            return False
        if AI8VideoConversationController._uses_saved_script_reference(text):
            return True
        if AI8VideoConversationController._is_saved_script_control_message(text):
            return True
        return bool(re.fullmatch(r"\s*\d{1,3}\s*(?:个|条|集|支)?\s*", text or ""))

    @staticmethod
    def _is_saved_script_control_message(text: str) -> bool:
        value = str(text or "").strip()
        if not value:
            return False
        compact = re.sub(r"\s+", "", value)
        if re.search(r"(开场|台词|对白|旁白|口播|文案|镜头|老板说|客户说|说：|讲：)", value):
            return False
        if len(compact) > 48:
            return False
        if re.fullmatch(r"(?:开始|直接|现在|继续|帮我|按(?:上面|当前|默认|已选|选中|标签|表单|对话框)?(?:设置|配置|信息)?)?(?:生成|跑|执行|制作)(?:\d{1,3})?(?:个|条|集|支|段)?(?:视频|短视频)?", compact):
            return True
        if re.fullmatch(r"(?:并发模式|普通模式|不用参考图|不需要参考图|无参考图|不要参考图)", compact):
            return True
        return False

    def _prefer_full_script_reference(self, state: ConversationState, text: str) -> bool:
        value = str(text or "").strip()
        if self._is_saved_script_control_message(value):
            return True
        count_match = re.fullmatch(r"\s*(\d{1,3})\s*(?:个|条|集|支)?\s*", value)
        if count_match:
            return int(count_match.group(1)) > 5
        if re.search(r"(全文|完整原文|完整剧本|逐字|按原顺序|全篇|全部内容|完整覆盖)", value):
            return True
        video_count = extract_video_count(value) or self._extract_plain_number(value)
        if not video_count:
            match = re.search(r"(\d{1,3})\s*(?:个|条|支|段)", value)
            video_count = int(match.group(1)) if match else None
        video_count = video_count or state.draft.video_count
        return bool(video_count and video_count > 5)

    def _merge_message(
        self,
        state: ConversationState,
        text: str,
        *,
        control_text: str | None = None,
        ai_interpretation: dict | None = None,
    ) -> None:
        controls = control_text if control_text is not None else text
        if ai_interpretation is None:
            ai_interpretation = self._interpret_request_with_ai(text)
        if not state.draft.raw_text and self._looks_like_base_request(text, ai_interpretation):
            state.draft.raw_text = text

        count = (
            (ai_interpretation or {}).get("video_count")
            or extract_video_count(controls)
            or self._extract_plain_number(controls)
            or extract_video_count(text)
            or self._extract_plain_number(text)
        )
        if count:
            state.draft.video_count = count

        mode_hint = (ai_interpretation or {}).get("mode") or detect_mode_hint(controls)
        if mode_hint:
            state.draft.mode = mode_hint
        elif not state.draft.mode and state.draft.video_count and state.draft.video_count > 1:
            state.draft.mode = "batch_videos"
        elif not state.draft.mode and state.draft.raw_text:
            state.draft.mode = "single_video"

        reference_image = extract_reference_image(controls) or extract_reference_image(text)
        if reference_image:
            state.draft.reference_image = reference_image
            state.draft.reference_image_enabled = True
            if state.draft.reference_image_transform_options is None:
                state.draft.reference_image_transform_options = enabled_default_reference_image_options()
            if state.draft.reference_image_custom_prompt is None:
                state.draft.reference_image_custom_prompt = default_reference_image_custom_prompt()

        reference_decision = (ai_interpretation or {}).get("reference_image_decision")
        if reference_decision is None:
            reference_decision = detect_reference_decision(controls)
        if reference_decision is False:
            state.draft.reference_image_enabled = False
            state.draft.reference_image = None
        elif reference_decision is True and state.draft.reference_image_enabled is None:
            if self._uses_saved_reference_image(controls):
                self._apply_default_reference_image(state)
                if not state.draft.reference_image:
                    state.draft.reference_image_enabled = True
            else:
                state.draft.reference_image_enabled = True

        concurrent_decision = (ai_interpretation or {}).get("concurrent_generation")
        if concurrent_decision is None:
            concurrent_decision = detect_concurrent_generation_decision(controls)
        if concurrent_decision is not None:
            state.draft.concurrent_generation = concurrent_decision

        html_motion_decision = (ai_interpretation or {}).get("html_motion_overlay")
        if html_motion_decision is None:
            html_motion_decision = detect_html_motion_overlay_decision(controls)
        if html_motion_decision is not None:
            state.draft.html_motion_overlay_enabled = html_motion_decision

        core_keywords = (ai_interpretation or {}).get("core_keywords") or extract_core_keywords(controls)
        if core_keywords:
            state.draft.core_keywords = core_keywords

        style_hint = (ai_interpretation or {}).get("style_hint") or extract_style_hint(controls)
        if style_hint:
            state.draft.style_hint = self._merge_style_hints(state.draft.style_hint, style_hint)

        ai_duration = (ai_interpretation or {}).get("duration_seconds")
        if ai_duration:
            state.draft.duration_seconds = ai_duration
        elif re.search(r"\d+\s*(?:秒|[sS])", controls) or state.draft.duration_seconds is None:
            state.draft.duration_seconds = extract_duration_seconds(controls)

    def _interpret_request_with_ai(self, text: str) -> dict | None:
        llm = getattr(self.pipeline, "request_interpreter_llm", None) or getattr(self.pipeline, "llm", None)
        if llm is None:
            return None
        try:
            logger.info("ai8video request interpretation start")
            interpretation = interpret_generation_request_with_ai(text, llm=llm)
            logger.info(
                "ai8video request interpretation done intent=%s video_count=%s confidence=%s",
                (interpretation or {}).get("intent"),
                (interpretation or {}).get("video_count"),
                (interpretation or {}).get("confidence"),
            )
        except Exception as exc:
            logger.warning("ai8video request interpretation failed; using local fallback: %s", exc)
            return None
        confidence = float((interpretation or {}).get("confidence") or 0)
        if confidence < 0.35:
            return None
        return interpretation

    def _needs_dialogue_completion(
        self,
        state: ConversationState,
        material_context: dict | None,
        ai_interpretation: dict | None = None,
    ) -> bool:
        if (ai_interpretation or {}).get("needs_content_completion") is True:
            return True
        if (ai_interpretation or {}).get("needs_content_completion") is False and self._ai_intent(ai_interpretation) == "generation":
            return False
        if state.awaiting == "content_completion" and state.draft.content_completion_mode is None:
            return False
        if (material_context or {}).get("scripts"):
            return False
        text = str(state.draft.raw_text or "").strip()
        if not text:
            return False
        compact = re.sub(r"\s+", "", text)
        if len(compact) >= 160:
            return False
        keyword_signals = (
            "剧本", "台词", "对白", "口播", "旁白", "文案", "镜头", "开场", "结尾",
            "第一句", "第二句", "第三句", "老板说", "客户说", "说：", "讲：",
        )
        if any(token in text for token in keyword_signals):
            return False
        sentence_count = len(re.findall(r"[。！？!?；;\n]", text))
        if sentence_count >= 3 and len(compact) >= 72:
            return False
        has_generation_frame = bool(
            state.draft.reference_image
            or (state.draft.video_count and state.draft.video_count > 1)
            or re.search(r"\d+\s*(?:秒|[sS])", text)
            or any(token in text for token in ("素材", "参考图", "图片", "@"))
        )
        if not has_generation_frame:
            return False
        request_signals = ("生成", "短视频", "视频", "素材", "参考图", "图片", "文案", "口播", "产品", "教程", "探店")
        return any(token in text for token in request_signals)

    @staticmethod
    def _is_smart_dialogue_request(text: str, ai_interpretation: dict | None = None) -> bool:
        if AI8VideoConversationController._ai_intent(ai_interpretation) == "content_completion_followup":
            style_hint = str((ai_interpretation or {}).get("style_hint") or "")
            if "智能" in style_hint or "自动" in style_hint:
                return True
        return bool(re.search(r"(AI8|智能|自动).{0,6}(生成|补全).{0,8}(台词|对白|文案|口播)", text))

    def _build_smart_dialogue_instruction(self, state: ConversationState) -> str:
        style = f"并延续“{state.draft.style_hint}”风格" if state.draft.style_hint else "并保持当前风格"
        return (
            "口播文案补全要求：\n"
            f"请先根据上面的生成目标自动补全适合直接生成短视频的中文台词/口播文案，{style}。"
            "文案要包含开场钩子、主体冲突和收尾落点；口播只写情绪和语气，不要添加用户未要求的声线、性别或身份设定，让视频模型根据画面主体自行判断，再继续后续生成。"
        )

    @staticmethod
    def _append_draft_text(current: str | None, addition: str) -> str:
        base = str(current or "").rstrip()
        extra = str(addition or "").strip()
        if not extra:
            return base
        if not base:
            return extra
        if extra in base:
            return base
        return f"{base}\n\n{extra}"

    def _run_generation_request(self, request: ParsedRequest, *, progress_session_id: str | None = None):
        mode = normalize_video_merge_mode(self._merge_mode_loader())
        if mode in {"merge2", "merge4"}:
            return self._merged_video_pipeline(mode).run_request(request, progress_session_id=progress_session_id)
        return self.pipeline.run_request(request, progress_session_id=progress_session_id)

    def _plan_generation_request(
        self,
        request: ParsedRequest,
        *,
        progress_session_id: str | None = None,
        smart_split: bool = False,
    ) -> list[VideoPrompt]:
        mode = normalize_video_merge_mode(self._merge_mode_loader())
        pipeline = self._merged_video_pipeline(mode) if mode in {"merge2", "merge4"} else self.pipeline
        return pipeline.plan_request(
            request,
            progress_session_id=progress_session_id,
            smart_split=smart_split,
        )

    def _supports_planned_generation(self) -> bool:
        mode = normalize_video_merge_mode(self._merge_mode_loader())
        pipeline = self._merged_video_pipeline(mode) if mode in {"merge2", "merge4"} else self.pipeline
        pipeline_type = type(pipeline)
        return callable(getattr(pipeline_type, "plan_request", None)) and callable(
            getattr(pipeline_type, "run_planned_request", None)
        )

    def _run_planned_generation_request(
        self,
        request: ParsedRequest,
        videos: list[VideoPrompt],
        *,
        progress_session_id: str | None = None,
    ):
        mode = normalize_video_merge_mode(self._merge_mode_loader())
        pipeline = self._merged_video_pipeline(mode) if mode in {"merge2", "merge4"} else self.pipeline
        return pipeline.run_planned_request(
            request,
            videos,
            progress_session_id=progress_session_id,
        )

    def _merged_video_pipeline(self, mode: str = "merge2"):
        mode = normalize_video_merge_mode(mode)
        if mode not in {"merge2", "merge4"}:
            mode = "merge2"
        if mode in self._merged_pipelines:
            return self._merged_pipelines[mode]
        segment_count = 4 if mode == "merge4" else 2
        if self._merged_pipeline_factory is not None:
            try:
                signature = inspect.signature(self._merged_pipeline_factory)
                if "segment_count" in signature.parameters:
                    pipeline = self._merged_pipeline_factory(segment_count=segment_count)
                elif "mode" in signature.parameters:
                    pipeline = self._merged_pipeline_factory(mode=mode)
                else:
                    pipeline = self._merged_pipeline_factory()
            except (TypeError, ValueError):
                pipeline = self._merged_pipeline_factory()
            self._merged_pipelines[mode] = pipeline
            self._merged_pipeline = pipeline
            return pipeline
        from ai8video.generation.merged_video_pipeline import AI8VideoMergedPipeline

        pipeline = AI8VideoMergedPipeline(
            config=getattr(self.pipeline, "config", None),
            llm=getattr(self.pipeline, "llm", None),
            segment_count=segment_count,
        )
        self._merged_pipelines[mode] = pipeline
        self._merged_pipeline = pipeline
        return pipeline

    @staticmethod
    def _is_batch_request(text: str, ai_interpretation: dict | None = None) -> bool:
        if AI8VideoConversationController._ai_intent(ai_interpretation) == "batch_run":
            return True
        return detect_batch_mode(text)

    @staticmethod
    def _find_video(result_payload: dict, video_index: int) -> VideoPrompt | None:
        for item in result_payload.get("videos") or []:
            if int(item.get("index") or 0) == video_index:
                return VideoPrompt(
                    index=video_index,
                    title=str(item.get("title") or f"视频 {video_index}"),
                    prompt=str(item.get("prompt") or "").strip(),
                    source_summary=str(item.get("source_summary") or "").strip(),
                )
        return None

    def _build_rewrite_request(self, state: ConversationState, text: str) -> ParsedRequest:
        if not state.draft.raw_text:
            previous_request = (state.last_result or {}).get("request") or {}
            state.draft.raw_text = str(previous_request.get("raw_text") or previous_request.get("rawText") or text).strip()
        draft_request = state.draft.to_request()
        return ParsedRequest(
            raw_text=text,
            mode="single_video",
            video_count=1,
            reference_image=draft_request.reference_image,
            reference_image_custom_prompt=draft_request.reference_image_custom_prompt,
            reference_image_transform_options=draft_request.reference_image_transform_options,
            style_hint=draft_request.style_hint,
            core_keywords=draft_request.core_keywords,
            duration_seconds=draft_request.duration_seconds,
            ratio=draft_request.ratio,
            resolution=draft_request.resolution,
            preset=draft_request.preset,
            concurrent_generation=draft_request.concurrent_generation,
            html_motion_overlay_enabled=draft_request.html_motion_overlay_enabled,
        )

    @staticmethod
    def _merge_result_payload(previous: dict, latest: dict) -> dict:
        merged = dict(previous)
        merged["request"] = previous.get("request") or latest.get("request")
        merged["firstFrame"] = latest.get("firstFrame") or previous.get("firstFrame")
        merged["dryRun"] = latest.get("dryRun", previous.get("dryRun", True))
        for key, index_key in (
            ("videos", "index"),
            ("jobs", "video_index"),
            ("outcomes", "video_index"),
            ("archives", "video_index"),
        ):
            merged[key] = AI8VideoConversationController._merge_items(previous.get(key) or [], latest.get(key) or [], index_key)
        merged["assetRecords"] = (previous.get("assetRecords") or []) + (latest.get("assetRecords") or [])
        return merged

    @staticmethod
    def _merge_items(current: list[dict], updates: list[dict], index_key: str) -> list[dict]:
        bucket: dict[int, dict] = {}
        order: list[int] = []
        for item in current:
            idx = int(item.get(index_key) or 0)
            if idx not in bucket:
                order.append(idx)
            bucket[idx] = item
        for item in updates:
            idx = int(item.get(index_key) or 0)
            if idx not in bucket:
                order.append(idx)
            bucket[idx] = item
        return [bucket[idx] for idx in sorted(order)]

    def _build_scope_summary(self, state: ConversationState) -> str:
        if state.draft.mode == "batch_videos":
            count = state.draft.video_count or 1
            style = f"，风格偏 {state.draft.style_hint}" if state.draft.style_hint else ""
            return f"好，我会基于这份素材规划 {count} 条独立视频{style}"
        style = f"，风格偏 {state.draft.style_hint}" if state.draft.style_hint else ""
        return f"好，我会先按单条视频来处理{style}"

    def _build_reference_summary(self, state: ConversationState) -> str:
        if state.draft.reference_image_enabled and state.draft.reference_image:
            return f"参考图已采用 {state.draft.reference_image}"
        return "这次不使用参考图"

    @staticmethod
    def _apply_default_reference_image(state: ConversationState) -> None:
        if state.draft.reference_image_enabled is not None or state.draft.reference_image:
            return
        image_path = default_reference_image_path()
        if not image_path:
            return
        state.draft.reference_image = image_path
        state.draft.reference_image_enabled = True
        state.draft.reference_image_transform_options = enabled_default_reference_image_options()
        state.draft.reference_image_custom_prompt = default_reference_image_custom_prompt()

    @staticmethod
    def _apply_default_generation_mode(state: ConversationState) -> None:
        if state.draft.concurrent_generation is None:
            state.draft.concurrent_generation = default_concurrent_generation_enabled()
        if state.draft.tail_frame_chaining is None:
            state.draft.tail_frame_chaining = default_tail_frame_chaining_enabled()
        if state.draft.tail_frame_chaining:
            state.draft.concurrent_generation = False

    @staticmethod
    def _apply_default_html_motion_overlay(state: ConversationState) -> None:
        if state.draft.html_motion_overlay_enabled is not None:
            return
        state.draft.html_motion_overlay_enabled = default_html_motion_overlay_enabled()

    @staticmethod
    def _uses_saved_reference_image(text: str) -> bool:
        return bool(re.search(r"(当前|默认|已选|选中|设置里|面板里).{0,8}(参考图|首帧|图片)", text))

    @staticmethod
    def _uses_saved_script_reference(text: str) -> bool:
        return bool(re.search(r"(当前|默认|已选|选中|设置里|面板里).{0,8}(知识库参考|剧本参考|脚本参考|剧本素材|素材)", text))

    @staticmethod
    def _merge_style_hints(current: str | None, incoming: str) -> str:
        items = []
        for chunk in (current or "").split("、") + incoming.split("、"):
            chunk = chunk.strip()
            if chunk and chunk not in items:
                items.append(chunk)
        return "、".join(items)

    @staticmethod
    def _extract_plain_number(text: str) -> int | None:
        match = re.fullmatch(r"\s*(\d{1,3})\s*", text)
        if not match:
            return None
        value = int(match.group(1))
        return value if 1 <= value <= 100 else None

    @staticmethod
    def _looks_like_base_request(text: str, ai_interpretation: dict | None = None) -> bool:
        if AI8VideoConversationController._ai_intent(ai_interpretation) == "generation":
            return True
        if AI8VideoConversationController._ai_intent(ai_interpretation) in {
            "batch_run",
            "batch_seed_followup",
            "rewrite",
            "content_completion_followup",
            "core_keywords_followup",
        }:
            return False
        keywords = ("剧本", "提示词", "生成", "视频", "拆成", "分成", "文案", "口播", "产品", "教程", "探店")
        has_base_keyword = any(token in text for token in keywords)
        if has_base_keyword:
            return True
        if re.fullmatch(r"\s*\d{1,3}\s*", text):
            return False
        has_reference_only = bool(extract_reference_image(text) or "参考图" in text or "首帧" in text or "图片地址" in text)
        return len(text) >= 18 and not has_reference_only

    @staticmethod
    def _is_rewrite_request(text: str, ai_interpretation: dict | None = None) -> bool:
        if AI8VideoConversationController._ai_intent(ai_interpretation) == "rewrite":
            return True
        return extract_video_index(text) is not None and detect_rewrite_instruction(text) is not None

    @staticmethod
    def _ai_intent(ai_interpretation: dict | None) -> str | None:
        if not ai_interpretation:
            return None
        intent = str(ai_interpretation.get("intent") or "").strip()
        return intent or None
