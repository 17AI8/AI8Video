from __future__ import annotations

import os
import re
from typing import Any

from ai8video.knowledge.script_knowledge import (
    ScriptKnowledgeUnavailable,
    get_script_knowledge_store,
)
from ai8video.knowledge.script_knowledge_rerank import RerankLLM, rerank_candidates
from ai8video.knowledge.script_knowledge_trace import append_retrieval_trace


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
    if not str(relative_path or "").strip():
        return _traced_failure("missing_document_scope", query)
    try:
        store = get_script_knowledge_store()
        candidates = store.search_sections(
            query,
            relative_path=relative_path,
            limit=recall_limit,
        )
    except ScriptKnowledgeUnavailable:
        return _traced_failure("postgres_unavailable", query)
    except Exception as exc:
        return _traced_failure(f"retrieval_failed:{_safe_error(exc)}", query)
    if not candidates:
        return _traced_failure("no_candidates", query)
    scope_error = _candidate_scope_error(candidates, relative_path)
    if scope_error:
        return _traced_failure(scope_error, query)
    retrieval_trace = _retrieval_trace(candidates)
    ranking_query = str(plan.get("rankingQuery") or query)
    reranked = _select_candidates(ranking_query, candidates, rerank_llm, inject_top_k)
    selected = list(reranked["candidates"])
    scope_error = _candidate_scope_error(selected, relative_path)
    if scope_error:
        return _traced_failure(scope_error, query, retrieval_trace=retrieval_trace)
    retrieval_trace["selectedCandidateIds"] = [int(item.get("id") or 0) for item in selected]
    retrieval_trace["rerankApplied"] = bool(reranked["rerankApplied"])
    retrieval_trace["rerankFallbackReason"] = str(reranked["fallbackReason"] or "")
    retrieval_trace["rerankCandidateIds"] = list(retrieval_trace["selectedCandidateIds"])
    append_retrieval_trace(retrieval_trace)
    return {
        "ok": True,
        "query": query,
        "queryPlan": plan,
        "retrievalMode": str(retrieval_trace.get("retrievalMode") or "legacy"),
        "retrievalBackend": str(retrieval_trace.get("retrievalBackend") or "legacy"),
        "retrievalBackendFallbackReason": str(
            retrieval_trace.get("retrievalBackendFallbackReason") or ""
        ),
        "retrievalTrace": retrieval_trace,
        "documentId": int(retrieval_trace.get("documentId") or 0),
        "indexVersion": int(retrieval_trace.get("indexVersion") or 0),
        "tokenizerVersion": int(retrieval_trace.get("tokenizerVersion") or 0),
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


def _failure(
    reason: str,
    query: str,
    *,
    retrieval_trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trace = dict(retrieval_trace or {})
    trace.setdefault("retrievalFailureReason", reason)
    trace.setdefault("query", query)
    return {
        "ok": False,
        "query": query,
        "retrievalMode": str(trace.get("retrievalMode") or ""),
        "retrievalBackend": str(trace.get("retrievalBackend") or ""),
        "retrievalBackendFallbackReason": str(
            trace.get("retrievalBackendFallbackReason") or ""
        ),
        "retrievalTrace": trace,
        "recallCount": 0,
        "topK": 0,
        "rerankApplied": False,
        "fallbackReason": reason,
        "sections": [],
        "contextText": "",
    }


def _traced_failure(
    reason: str,
    query: str,
    *,
    retrieval_trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    failure = _failure(reason, query, retrieval_trace=retrieval_trace)
    append_retrieval_trace(failure["retrievalTrace"])
    return failure


def _retrieval_trace(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        return {}
    trace = candidates[0].get("retrievalTrace")
    return dict(trace) if isinstance(trace, dict) else {
        "retrievalMode": "legacy",
        "retrievalBackend": str(candidates[0].get("retrievalBackend") or "legacy"),
        "documentId": int(candidates[0].get("documentId") or 0),
    }


def _candidate_scope_error(candidates: list[dict[str, Any]], relative_path: str) -> str:
    expected_path = str(relative_path or "").strip()
    if not expected_path:
        return "missing_document_scope"
    mismatched = [
        candidate for candidate in candidates
        if str(candidate.get("relativePath") or "").strip() != expected_path
    ]
    return "selected_document_scope_violation" if mismatched else ""


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _safe_error(exc: Exception) -> str:
    return (str(exc).splitlines()[0].strip() or exc.__class__.__name__)[:180]
