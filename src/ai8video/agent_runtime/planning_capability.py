"""现有 Planner 的类型化 Capability 适配器。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ai8video.agent_runtime.capabilities import AgentRunContext, CapabilitySpec
from ai8video.core.models import ParsedRequest, VideoPrompt


PLANNING_CAPABILITY_NAME = "planner.plan-video-content"


@dataclass(frozen=True)
class PlanningCapabilityInput:
    request: ParsedRequest
    target_duration: int
    task_constraints: str
    smart_split: bool
    allow_mock: bool
    llm: Callable[[str], str] | None
    trace_session_id: str | None
    smart_split_count_locked: bool = False
    use_parallel_episode_planning: bool = False


def build_planning_capability(
    *,
    infer_count: Callable[..., tuple[int, str]],
    smart_plan: Callable[..., list[VideoPrompt]],
    parallel_smart_plan: Callable[..., list[VideoPrompt]] | None = None,
    repeat_plan: Callable[..., list[VideoPrompt]],
    single_plan: Callable[..., list[VideoPrompt]],
) -> CapabilitySpec[PlanningCapabilityInput, list[VideoPrompt]]:
    def execute(_context: AgentRunContext, data: PlanningCapabilityInput) -> list[VideoPrompt]:
        return _execute_planning(
            data,
            infer_count,
            smart_plan,
            parallel_smart_plan or smart_plan,
            repeat_plan,
            single_plan,
        )

    return CapabilitySpec(
        name=PLANNING_CAPABILITY_NAME,
        agent_id="planner",
        description="把用户请求转换为可执行的独立视频规划。",
        handler=execute,
        input_type=PlanningCapabilityInput,
        output_type=list,
        policy_skills=("plan-video-content",),
        side_effects=False,
        replay_safe=True,
        execution_mode="parallel",
    )


def _execute_planning(
    data: PlanningCapabilityInput,
    infer_count: Callable[..., tuple[int, str]],
    smart_plan: Callable[..., list[VideoPrompt]],
    parallel_smart_plan: Callable[..., list[VideoPrompt]],
    repeat_plan: Callable[..., list[VideoPrompt]],
    single_plan: Callable[..., list[VideoPrompt]],
) -> list[VideoPrompt]:
    request = data.request
    video_count = request.video_count
    if data.smart_split:
        model_decides_count = not data.smart_split_count_locked and data.use_parallel_episode_planning
        if model_decides_count:
            # 标准智能分集的未锁定数量可能来自文本规则或旧状态；不能把它带入全局大纲提示词。
            video_count = None
        if not data.smart_split_count_locked and not model_decides_count:
            video_count, request.smart_split_reason = infer_count(
                request.raw_text,
                llm=data.llm,
                duration_seconds=data.target_duration,
                trace_session_id=data.trace_session_id,
            )
        if not video_count and not model_decides_count:
            raise ValueError("video_count is required for video planning")
        selected_smart_plan = parallel_smart_plan if data.use_parallel_episode_planning else smart_plan
        videos = selected_smart_plan(
            request.raw_text,
            video_count,
            request.style_hint,
            request.core_keywords,
            task_constraints=data.task_constraints,
            final_duration_seconds=data.target_duration,
            llm=data.llm,
            allow_mock=data.allow_mock,
            trace_session_id=data.trace_session_id,
            tail_frame_chaining=bool(request.tail_frame_chaining),
        )
        if model_decides_count:
            request.video_count = len(videos)
            request.smart_split_reason = f"全局分集大纲模型规划为 {len(videos)} 条视频。"
        return videos
    if request.mode == "batch_videos":
        if not video_count:
            raise ValueError("video_count is required for manual batch generation")
        return repeat_plan(
            request.raw_text,
            video_count,
            request.style_hint,
            request.core_keywords,
        )
    return single_plan(request.raw_text, request.style_hint, request.core_keywords)
