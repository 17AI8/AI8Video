"""Supervisor 对知识建树与 Reviewer 审核执行有界编排。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ai8video.knowledge.knowledge_base_agent import (
    KnowledgeBaseAgent,
    KnowledgeBaseAgentRequest,
    KnowledgeBaseAgentResult,
)
from ai8video.knowledge.reviewer_agent import KnowledgeReviewDecision, ReviewerAgent


StageEmitter = Callable[[str, str], None]


@dataclass(frozen=True)
class KnowledgeIngestionOutcome:
    proposal: KnowledgeBaseAgentResult
    review: KnowledgeReviewDecision
    revision_count: int


class KnowledgeIngestionSupervisor:
    """最多允许 Reviewer 要求一次返工，审核通过后才返回可落库结果。"""

    role = "supervisor"

    def __init__(
        self,
        knowledge_agent: KnowledgeBaseAgent,
        reviewer: ReviewerAgent,
        *,
        max_revisions: int = 1,
    ) -> None:
        self._knowledge_agent = knowledge_agent
        self._reviewer = reviewer
        self._max_revisions = max(0, min(int(max_revisions), 1))

    def run(
        self,
        request: KnowledgeBaseAgentRequest,
        emit: StageEmitter | None = None,
    ) -> KnowledgeIngestionOutcome:
        feedback = ""
        for revision_count in range(self._max_revisions + 1):
            _emit(emit, "knowledge_agent", self._build_message(revision_count))
            try:
                proposal = self._knowledge_agent.run(request, feedback)
            except RuntimeError as exc:
                feedback = _agent_revision_feedback(exc)
                if not feedback or revision_count >= self._max_revisions:
                    raise
                _emit(emit, "revision", "候选结构未通过完整性校验，Supervisor 要求有界返工")
                continue
            _emit(emit, "reviewer", "Reviewer 正在进行全树审核：原子性、覆盖度与检索价值")
            review = self._reviewer.review_knowledge(request, proposal)
            _emit(emit, "reviewer", self._review_message(review))
            if review.decision == "accept":
                return KnowledgeIngestionOutcome(proposal, review, revision_count)
            if review.decision == "reject":
                raise RuntimeError(review.summary or "Reviewer 拒绝本次知识入库")
            if revision_count >= self._max_revisions:
                raise RuntimeError(review.summary or "Reviewer 复审后仍未通过")
            feedback = review.revision_feedback()
            _emit(emit, "revision", "Supervisor 允许一次有界返工")
        raise RuntimeError("知识入库未收敛")

    @staticmethod
    def _build_message(revision_count: int) -> str:
        return "知识库 Agent 正在返工知识树" if revision_count else "知识库 Agent 正在规划知识树"

    @staticmethod
    def _review_message(review: KnowledgeReviewDecision) -> str:
        labels = {"accept": "审核通过", "revise": "要求返工", "reject": "审核拒绝"}
        label = labels.get(review.decision, review.decision)
        return f"Reviewer {label}：{review.summary or '无补充说明'}"


def _emit(emit: StageEmitter | None, stage: str, text: str) -> None:
    if emit is not None:
        emit(stage, text)


def _agent_revision_feedback(exc: RuntimeError) -> str:
    message = str(exc).strip()
    recoverable_prefixes = (
        "知识库 Agent 完整性校验未通过",
        "模型返回的知识树",
    )
    if not message.startswith(recoverable_prefixes):
        return ""
    return f"{message}。请拆分过大的叶子、修正 sourceUnitIds，并重新输出完整合法 JSON。"
