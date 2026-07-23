from __future__ import annotations

import os
import re
from typing import Any

from ai8video.knowledge.script_knowledge import get_script_knowledge_store, register_script_knowledge_sources
from ai8video.knowledge.script_knowledge_rerank import RerankLLM, rerank_candidates


def retrieve_reference_context(
    text: str,
    relative_path: str,
    *,
    rerank_llm: RerankLLM | None = None,
    query_hint: str = "",
    query_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan = query_plan or _fallback_query_plan(text, query_hint)
    query = build_retrieval_query(str(plan.get("query") or ""), query_hint=query_hint)
    recall_limit = _bounded_env_int("AI8VIDEO_SCRIPT_RECALL_TOP_K", 20, 5, 30)
    inject_top_k = _bounded_env_int("AI8VIDEO_SCRIPT_INJECT_TOP_K", 5, 1, 10)
    try:
        register_script_knowledge_sources()
        store = get_script_knowledge_store()
        status = store.status()
        if not status.get("available"):
            return _failure("postgres_unavailable", query)
        candidates = store.search_sections(
            query,
            relative_path=relative_path,
            limit=recall_limit,
        )
    except Exception as exc:
        return _failure(f"retrieval_failed:{_safe_error(exc)}", query)
    if not candidates:
        return _failure("no_candidates", query)
    ranking_query = str(plan.get("rankingQuery") or query)
    reranked = _select_candidates(ranking_query, candidates, rerank_llm, inject_top_k)
    selected = list(reranked["candidates"])
    return {
        "ok": True,
        "query": query,
        "queryPlan": plan,
        "recallCount": len(candidates),
        "topK": len(selected),
        "rerankApplied": bool(reranked["rerankApplied"]),
        "fallbackReason": str(reranked["fallbackReason"] or ""),
        "sections": selected,
        "contextText": format_reference_sections(selected),
    }


def build_retrieval_query(text: str, *, query_hint: str = "") -> str:
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    cleaned = re.sub(
        r"(?:请|帮我|使用|根据|参考|结合|调用|从|用)?"
        r"(?:当前|默认|已选|选中|设置里|面板里)?"
        r"(?:剧本参考|脚本参考|剧本知识库|知识库)",
        " ",
        raw,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，。；：,.;:")
    if re.fullmatch(r"\d{1,3}\s*(?:个|条|集|支|段)?", cleaned):
        cleaned = re.sub(r"\s+", " ", str(query_hint or "")).strip()
    return (cleaned or raw)[:500]


def format_reference_sections(sections: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for index, section in enumerate(sections, start=1):
        heading = str(section.get("heading") or f"知识段 {index}").strip()
        content = str(section.get("content") or "").strip()
        blocks.append(f"[知识段 {index}｜{heading}]\n{content}")
    return "\n\n".join(blocks)


def _fallback_query_plan(text: str, query_hint: str) -> dict[str, Any]:
    query = build_retrieval_query(text, query_hint=query_hint)
    return {
        "query": query,
        "rankingQuery": query,
        "keywords": [],
        "excludedTerms": [],
        "queryModelApplied": False,
        "fallbackReason": "precheck_unavailable",
    }


def _select_candidates(
    query: str,
    candidates: list[dict[str, Any]],
    rerank_llm: RerankLLM | None,
    top_k: int,
) -> dict[str, Any]:
    if len(candidates) <= top_k:
        return {
            "candidates": candidates[:top_k],
            "rerankApplied": False,
            "fallbackReason": "within_top_k",
        }
    return rerank_candidates(query, candidates, llm=rerank_llm, top_k=top_k)


def _failure(reason: str, query: str) -> dict[str, Any]:
    return {
        "ok": False,
        "query": query,
        "recallCount": 0,
        "topK": 0,
        "rerankApplied": False,
        "fallbackReason": reason,
        "sections": [],
        "contextText": "",
    }


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _safe_error(exc: Exception) -> str:
    return (str(exc).splitlines()[0].strip() or exc.__class__.__name__)[:180]
