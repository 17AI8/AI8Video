"""Policy boundary for high-level Agent actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ai8video.application.conversation_store import ConversationStoreError


@dataclass(frozen=True)
class AgentPolicyContext:
    planned_video_count: int | None = None
    generated_video_count: int = 0
    paid_retry_count: int = 0
    approved_operations: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class PolicyAuthorization:
    tool_name: str
    arguments: dict[str, Any]
    side_effects: bool
    replay_safe: bool
    requires_approval: bool
    cost_units: float


class ActionPolicyGuard:
    _TOOL_POLICY = {
        "prepare_video_plan": (False, True),
        "review_video_plan": (False, True),
        "generate_video_batch": (True, True),
        "inspect_generation_result": (False, True),
        "archive_and_deliver": (True, True),
        "task_user": (False, True),
    }

    def authorize(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: AgentPolicyContext,
    ) -> PolicyAuthorization:
        name = str(tool_name or "").strip()
        if name not in self._TOOL_POLICY:
            raise ConversationStoreError(
                "agent_tool_not_allowed",
                f"Agent 无权调用工具：{name or 'unknown'}。",
                status=403,
            )
        clean = dict(arguments or {})
        side_effects, replay_safe = self._TOOL_POLICY[name]
        requires_approval = False
        cost_units = 0.0
        if name == "generate_video_batch":
            count = self._positive_int(clean.get("count"), field_name="count")
            planned_count = int(context.planned_video_count or 0)
            if planned_count < 1:
                raise ConversationStoreError(
                    "agent_generation_plan_required",
                    "Agent 必须先依据已准备并审核的视频方案确定生成数量。",
                )
            retry_failed = bool(clean.get("retryFailedOnly"))
            count_mismatch = count > planned_count or (not retry_failed and count != planned_count)
            if count_mismatch:
                raise ConversationStoreError(
                    "agent_generation_count_mismatch",
                    "Agent 生成数量必须服从已审核方案。",
                    details={"planned": planned_count, "attempted": count},
                )
            clean["count"] = count
            requires_approval = retry_failed and "paid_retry" not in context.approved_operations
            cost_units = float(count)
        elif name == "archive_and_deliver":
            if bool(clean.get("publishExternally")):
                raise ConversationStoreError(
                    "agent_external_publish_blocked",
                    "Agent 模式不会自动发布到外部平台。",
                    status=403,
                )
            clean["publishExternally"] = False
        elif name == "task_user":
            question = str(clean.get("question") or "").strip()
            if not question:
                raise ConversationStoreError("agent_question_required", "task_user 必须提供问题。", status=400)
            clean["question"] = question[:1000]
        return PolicyAuthorization(
            tool_name=name,
            arguments=clean,
            side_effects=side_effects,
            replay_safe=replay_safe,
            requires_approval=requires_approval,
            cost_units=cost_units,
        )

    @staticmethod
    def _positive_int(value: Any, *, field_name: str) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise ConversationStoreError(
                "invalid_agent_action_input",
                f"{field_name} 必须是正整数。",
                status=400,
            ) from exc
        if number < 1:
            raise ConversationStoreError(
                "invalid_agent_action_input",
                f"{field_name} 必须是正整数。",
                status=400,
            )
        return number
