from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from typing import Any

from ai8video.core.config import AI8VideoConfig
from ai8video.integrations.llm_provider import build_openai_compat_splitter


QueryLLM = Callable[[str], str]
QUERY_SYSTEM_PROMPT = (
    "你是剧本知识库检索意图提炼器。区分正向主题与禁止项，"
    "只返回严格 JSON，不生成剧本正文。"
)


def build_script_query_llm(config: AI8VideoConfig) -> QueryLLM | None:
    if not _query_model_enabled():
        return None
    timeout = _bounded_env_int("AI8VIDEO_QUERY_MODEL_TIMEOUT_SECONDS", 8, 3, 20)
    return build_openai_compat_splitter(
        config,
        timeout_seconds=timeout,
        system_prompt=QUERY_SYSTEM_PROMPT,
        stream=False,
        transport_retry_count=0,
    )


def plan_retrieval_query(
    text: str,
    system_prompt: str,
    reference_hint: str,
    *,
    llm: QueryLLM | None,
) -> dict[str, Any]:
    fallback = _fallback_plan(text, reference_hint)
    if llm is None:
        return fallback
    try:
        data = _parse_plan(llm(_build_prompt(text, system_prompt, reference_hint)))
    except Exception as exc:
        fallback["fallbackReason"] = f"query_model_failed:{_safe_error(exc)}"
        return fallback
    return data


def _build_prompt(text: str, system_prompt: str, reference_hint: str) -> str:
    return f"""请提炼用于 PostgreSQL 剧本知识库召回的检索意图。

要求：
1. 正向关键词只保留用户希望画面或内容出现的主题、人物、场景、产品、受众和动作。
2. “禁止、不要、不允许、过滤、避免”等内容必须进入 excluded_terms，不能放入正向关键词。
3. 数量、开始生成等控制词不属于检索主题。
4. query 控制在 80 个中文字符以内。
5. 只返回 JSON：{{"query":"主题摘要","keywords":["词"],"excluded_terms":["词"]}}。

用户输入：{str(text or '').strip()[:500]}

用户系统提示词：{str(system_prompt or '').strip()[:1200] or '（无）'}

当前剧本元数据：{str(reference_hint or '').strip()[:800]}
"""


def _parse_plan(raw: str) -> dict[str, Any]:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(raw or "").strip(), flags=re.I)
    match = re.search(r"\{[\s\S]*\}", text)
    data = json.loads(match.group(0) if match else text)
    keywords = _string_list(data.get("keywords"), 12)
    excluded = _string_list(data.get("excluded_terms"), 12)
    query = str(data.get("query") or " ".join(keywords)).strip()[:160]
    if not query:
        raise ValueError("检索意图缺少 query")
    return _plan(query, keywords, excluded, applied=True, reason="")


def _fallback_plan(text: str, reference_hint: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    query = reference_hint if re.fullmatch(r"\s*\d{1,3}\s*(?:个|条|集|支|段)?\s*", raw) else raw
    query = re.sub(r"\s+", " ", str(query or reference_hint)).strip()[:160]
    return _plan(query, [], [], applied=False, reason="query_llm_unavailable")


def _plan(query: str, keywords: list[str], excluded: list[str], *, applied: bool, reason: str) -> dict[str, Any]:
    ranking_query = query
    if excluded:
        ranking_query += "；应排除：" + "、".join(excluded)
    return {
        "query": query,
        "rankingQuery": ranking_query,
        "keywords": keywords,
        "excludedTerms": excluded,
        "queryModelApplied": applied,
        "fallbackReason": reason,
    }


def _string_list(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()[:80]
        if text and text not in result:
            result.append(text)
    return result[:limit]


def _query_model_enabled() -> bool:
    value = str(os.getenv("AI8VIDEO_QUERY_MODEL_ENABLED") or "1").strip().lower()
    return value not in {"0", "false", "no", "off", "disabled"}


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _safe_error(exc: Exception) -> str:
    return (str(exc).splitlines()[0].strip() or exc.__class__.__name__)[:180]
