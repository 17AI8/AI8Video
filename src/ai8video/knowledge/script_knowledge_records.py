from __future__ import annotations

from typing import Any

from ai8video.knowledge.script_knowledge_bm25 import (
    BM25_INDEX_VERSION,
    BM25_TOKENIZER_VERSION,
)


def document_summary(row: dict[str, Any]) -> dict[str, Any]:
    index_status = str(row.get("index_status") or "")
    section_count = int(row.get("section_count") or 0)
    return {
        "id": int(row["id"]),
        "name": str(row.get("name") or ""),
        "stem": str(row.get("stem") or ""),
        "relativePath": str(row.get("relative_path") or ""),
        "path": str(row.get("source_path") or ""),
        "contentType": str(row.get("content_type") or ""),
        "contentHash": str(row.get("content_hash") or ""),
        "preview": str(row.get("preview") or ""),
        "title": str(row.get("title") or row.get("stem") or ""),
        "summary": str(row.get("summary") or ""),
        "tags": list(row.get("tags") or []),
        "metadata": dict(row.get("metadata") or {}),
        "sizeBytes": int(row.get("size_bytes") or 0),
        "modifiedAt": float(row.get("source_modified_at") or 0),
        "indexStatus": index_status,
        "indexVersion": int(row.get("index_version") or 0),
        "sectionCount": section_count,
        "bm25Status": _bm25_status(row, index_status, section_count),
        "bm25IndexVersion": int(row.get("bm25_index_version") or 0),
        "bm25TokenizerVersion": int(row.get("bm25_tokenizer_version") or 0),
        "bm25SectionCount": int(row.get("bm25_section_count") or 0),
        "bm25AverageSectionLength": float(row.get("bm25_average_section_length") or 0),
        "bm25CorpusHash": str(row.get("bm25_corpus_hash") or ""),
        "bm25BuiltAt": str(row.get("bm25_built_at") or ""),
        "score": float(row.get("score") or 0),
        "matchedSectionId": int(row.get("matched_section_id") or 0),
        "matchedHeading": str(row.get("matched_heading") or ""),
        "matchedExcerpt": str(row.get("matched_excerpt") or ""),
        "kind": "script",
    }


def section_candidate(row: dict[str, Any]) -> dict[str, Any]:
    retrieval_channels = list(row.get("retrieval_channels") or []) or ["legacy"]
    return {
        "id": int(row["section_id"]),
        "documentId": int(row["document_id"]),
        "documentName": str(row.get("name") or ""),
        "documentTitle": str(row.get("title") or row.get("name") or ""),
        "relativePath": str(row.get("relative_path") or ""),
        "sectionOrder": int(row.get("section_order") or 0),
        "heading": str(row.get("heading") or ""),
        "content": str(row.get("content") or ""),
        "score": float(row.get("score") or 0),
        "bm25Score": float(row.get("bm25_score") or 0),
        "exactMatchScore": float(row.get("exact_match_score") or 0),
        "trigramScore": float(row.get("trigram_score") or 0),
        "retrievalChannels": retrieval_channels,
    }


def attach_search_trace(candidate: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    result = dict(candidate)
    result["retrievalBackend"] = str(trace.get("retrievalBackend") or "legacy")
    result["indexVersion"] = int(trace.get("indexVersion") or 0)
    result["tokenizerVersion"] = int(trace.get("tokenizerVersion") or 0)
    result["retrievalTrace"] = dict(trace)
    return result


def dedupe_search_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen_document_ids: set[int] = set()
    for row in rows:
        document_id = int(row["id"])
        if document_id in seen_document_ids:
            continue
        seen_document_ids.add(document_id)
        results.append(document_summary(row))
        if len(results) >= limit:
            break
    return results


def _bm25_status(row: dict[str, Any], index_status: str, section_count: int) -> str:
    bm25_ready = (
        int(row.get("bm25_index_version") or 0) == BM25_INDEX_VERSION
        and int(row.get("bm25_tokenizer_version") or 0) == BM25_TOKENIZER_VERSION
    )
    if bm25_ready:
        return "ready"
    if index_status == "ready" and section_count:
        return "pending"
    return "not_ready"
