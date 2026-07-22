from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from typing import Any

from ai8video.core.config import AI8VideoConfig
from ai8video.integrations.llm_provider import build_openai_compat_splitter


RerankLLM = Callable[[str], str]
RERANK_SYSTEM_PROMPT = (
    "你是剧本知识库候选重排器。只根据用户需求判断候选知识段的相关性，"
    "用户需求可能包含禁止、不要、过滤等否定约束；被明确禁止的主题应降低优先级，"
    "应优先选择同时满足正向主题和约束的知识段。"
    "不得生成新内容，只返回严格 JSON：{\"ranking\":[候选ID]}。"
)


def build_script_rerank_llm(config: AI8VideoConfig) -> RerankLLM | None:
    if not _rerank_enabled():
        return None
    timeout_seconds = _bounded_env_int("AI8VIDEO_RERANK_TIMEOUT_SECONDS", 8, 3, 20)
    return build_openai_compat_splitter(
        config,
        timeout_seconds=timeout_seconds,
        system_prompt=RERANK_SYSTEM_PROMPT,
        stream=False,
        transport_retry_count=0,
    )


def rerank_candidates(
    query: str,
    candidates: list[dict[str, Any]],
    *,
    llm: RerankLLM | None,
    top_k: int = 5,
) -> dict[str, Any]:
    safe_top_k = max(1, min(int(top_k), 10))
    original = list(candidates)
    if not original:
        return _rerank_result([], applied=False, reason="no_candidates")
    if llm is None:
        return _rerank_result(original[:safe_top_k], applied=False, reason="llm_unavailable")
    try:
        ranking = _parse_ranking(llm(_build_prompt(query, original)))
        ordered = _apply_ranking(original, ranking)
    except Exception as exc:
        return _rerank_result(
            original[:safe_top_k],
            applied=False,
            reason=f"rerank_failed:{_safe_error(exc)}",
        )
    return _rerank_result(ordered[:safe_top_k], applied=True, reason="")


def _build_prompt(query: str, candidates: list[dict[str, Any]]) -> str:
    payload = [
        {
            "id": int(item.get("id") or 0),
            "title": str(item.get("heading") or item.get("documentTitle") or "")[:160],
            "text": str(item.get("content") or "")[:500],
        }
        for item in candidates
    ]
    return (
        "请按与用户需求的相关性从高到低排列候选 ID。\n"
        "只返回 JSON 对象，不要解释，不要遗漏高相关候选。\n"
        f"用户需求：{str(query or '').strip()[:500]}\n"
        f"候选：{json.dumps(payload, ensure_ascii=False)}"
    )


def _parse_ranking(raw_text: str) -> list[int]:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(raw_text or "").strip(), flags=re.IGNORECASE)
    data = _load_json_value(text)
    ranking = data.get("ranking") if isinstance(data, dict) else data
    if not isinstance(ranking, list):
        raise ValueError("Rerank 响应缺少 ranking 数组")
    result: list[int] = []
    for value in ranking:
        try:
            candidate_id = int(value)
        except (TypeError, ValueError):
            continue
        if candidate_id > 0 and candidate_id not in result:
            result.append(candidate_id)
    if not result:
        raise ValueError("Rerank 响应没有有效候选 ID")
    return result


def _load_json_value(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        object_match = re.search(r"\{[\s\S]*\}", text)
        array_match = re.search(r"\[[\s\S]*\]", text)
        match = object_match or array_match
        if not match:
            raise
        return json.loads(match.group(0))


def _apply_ranking(
    candidates: list[dict[str, Any]],
    ranking: list[int],
) -> list[dict[str, Any]]:
    by_id = {int(item.get("id") or 0): item for item in candidates}
    ordered = [by_id[candidate_id] for candidate_id in ranking if candidate_id in by_id]
    seen = {int(item.get("id") or 0) for item in ordered}
    ordered.extend(item for item in candidates if int(item.get("id") or 0) not in seen)
    return ordered


def _rerank_result(
    candidates: list[dict[str, Any]],
    *,
    applied: bool,
    reason: str,
) -> dict[str, Any]:
    return {
        "candidates": candidates,
        "rerankApplied": applied,
        "fallbackReason": reason,
    }


def _rerank_enabled() -> bool:
    value = str(os.getenv("AI8VIDEO_RERANK_ENABLED") or "1").strip().lower()
    return value not in {"0", "false", "no", "off", "disabled"}


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _safe_error(exc: Exception) -> str:
    return (str(exc).splitlines()[0].strip() or exc.__class__.__name__)[:180]
