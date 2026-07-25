from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai8video.application.request_interpreter import interpret_generation_request_with_ai
from ai8video.generation.video_prompt_planner import LLMCallable


@dataclass(frozen=True)
class IntentContext:
    awaiting: str | None = None
    completed_runs: int = 0


@dataclass(frozen=True)
class IntentDecision:
    route: str
    reset_session: bool
    reason: str
    interpretation: dict[str, Any] | None = None


class IntentAgent:
    """只判断请求意图与会话路由，不修改会话或规划视频内容。"""

    def __init__(self, llm: LLMCallable | None = None):
        self.llm = llm

    def decide(self, text: str, context: IntentContext) -> IntentDecision:
        control_route = self._control_route(text, context.awaiting)
        if control_route:
            return IntentDecision(control_route, False, "命中当前会话控制意图")

        interpretation = interpret_generation_request_with_ai(text, llm=self.llm)
        intent = str((interpretation or {}).get("intent") or "unknown")
        if context.completed_runs > 0 and intent == "rewrite":
            return IntentDecision("rewrite", False, "修改上一轮结果", interpretation)
        if context.completed_runs > 0 and intent in {"generation", "batch_run"}:
            return IntentDecision("new_request", True, "上一轮已完成且收到新的生成任务", interpretation)
        return IntentDecision("continue_session", False, "继续当前任务收集或执行", interpretation)

    @staticmethod
    def _control_route(text: str, awaiting: str | None) -> str | None:
        if awaiting == "smart_split_confirmation":
            return "smart_split_followup"
        if awaiting:
            return "followup"
        return None
