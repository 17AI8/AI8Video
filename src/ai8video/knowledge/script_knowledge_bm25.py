from __future__ import annotations

import hashlib
import math
import os
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ai8video.knowledge.script_knowledge_sql import (
    bm25_backfill_candidates_sql,
    bm25_document_state_sql,
    bm25_section_search_sql,
    section_search_sql,
)
from ai8video.knowledge.script_knowledge_text import (
    build_bm25_query_terms,
    build_search_terms,
    build_ts_query,
    escape_like,
    extract_protected_terms,
    tokenize_retrieval_text,
)


BM25_INDEX_VERSION = 1
BM25_TOKENIZER_VERSION = 1
BM25_K1 = 1.2
BM25_B = 0.75
RETRIEVAL_MODE_ENV = "AI8VIDEO_SCRIPT_RETRIEVAL_MODE"
VALID_RETRIEVAL_MODES = {"legacy", "shadow", "bm25"}


@dataclass(frozen=True)
class BM25SectionData:
    section_order: int
    heading: str
    content: str
    retrieval_text: str
    token_count: int
    term_frequencies: dict[str, int]


@dataclass(frozen=True)
class BM25CorpusData:
    sections: tuple[BM25SectionData, ...]
    section_count: int
    average_section_length: float
    corpus_hash: str


@dataclass(frozen=True)
class SectionSearchResult:
    rows: tuple[Mapping[str, Any], ...]
    trace: dict[str, Any]


def build_bm25_corpus(sections: Sequence[Mapping[str, Any]]) -> BM25CorpusData:
    indexed_sections = tuple(
        build_bm25_section(section, fallback_order=section_order)
        for section_order, section in enumerate(sections)
    )
    total_token_count = sum(section.token_count for section in indexed_sections)
    section_count = len(indexed_sections)
    average_section_length = total_token_count / section_count if section_count else 0.0
    return BM25CorpusData(
        sections=indexed_sections,
        section_count=section_count,
        average_section_length=average_section_length,
        corpus_hash=calculate_corpus_hash(indexed_sections),
    )


def search_bm25_sections_in_memory(
    query: str,
    sections: Sequence[Mapping[str, Any]],
    *,
    limit: int,
    source_id: str,
    source_title: str,
) -> list[dict[str, Any]]:
    """Search one request-scoped corpus with the same tokenizer and BM25 math as PostgreSQL."""
    query_terms = build_bm25_query_terms(query)
    corpus = build_bm25_corpus(sections)
    if not query_terms or not corpus.sections:
        return []

    document_frequencies = {
        term: sum(1 for section in corpus.sections if term in section.term_frequencies)
        for term in query_terms
    }
    candidates: list[dict[str, Any]] = []
    relative_path = f"temporary:{source_id or 'request'}"
    for candidate_id, section in enumerate(corpus.sections, start=1):
        score = sum(
            calculate_bm25_term_score(
                document_count=corpus.section_count,
                document_frequency=document_frequencies[term],
                term_frequency=section.term_frequencies.get(term, 0),
                section_length=section.token_count,
                average_section_length=corpus.average_section_length,
            )
            for term in query_terms
        )
        if score <= 0:
            continue
        candidates.append({
            "id": candidate_id,
            "documentId": 0,
            "documentName": source_title,
            "documentTitle": source_title,
            "relativePath": relative_path,
            "sectionOrder": section.section_order,
            "heading": section.heading,
            "content": section.content,
            "score": score,
            "bm25Score": score,
            "exactMatchScore": 0.0,
            "trigramScore": 0.0,
            "retrievalChannels": ["bm25"],
            "retrievalBackend": "memory_bm25",
            "retrievalTrace": {
                "retrievalMode": "bm25",
                "retrievalBackend": "memory_bm25",
                "relativePath": relative_path,
                "documentId": 0,
                "indexVersion": BM25_INDEX_VERSION,
                "tokenizerVersion": BM25_TOKENIZER_VERSION,
                "corpusHash": corpus.corpus_hash,
                "queryTerms": query_terms,
                "sectionCount": corpus.section_count,
                "knowledgeSource": "temporary",
            },
        })
    candidates.sort(key=lambda item: (-float(item["score"]), int(item["sectionOrder"])))
    return candidates[: max(1, int(limit))]


def build_bm25_section(
    section: Mapping[str, Any],
    *,
    fallback_order: int,
) -> BM25SectionData:
    heading = str(section.get("heading") or "知识段").strip()
    content = str(section.get("content") or "")
    retrieval_text = build_retrieval_text(heading, content)
    term_frequencies = dict(Counter(tokenize_retrieval_text(retrieval_text)))
    return BM25SectionData(
        section_order=int(section.get("section_order", fallback_order)),
        heading=heading,
        content=content,
        retrieval_text=retrieval_text,
        token_count=sum(term_frequencies.values()),
        term_frequencies=term_frequencies,
    )


def build_retrieval_text(heading: str, content: str) -> str:
    values = [str(heading or "").strip(), str(content or "").strip()]
    return "\n".join(value for value in values if value)


def calculate_corpus_hash(sections: Sequence[BM25SectionData]) -> str:
    digest = hashlib.sha256()
    digest.update(f"bm25:{BM25_INDEX_VERSION}:tokenizer:{BM25_TOKENIZER_VERSION}\n".encode())
    for section in sections:
        digest.update(str(section.section_order).encode())
        digest.update(b"\0")
        digest.update(section.retrieval_text.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def build_posting_rows(
    document_id: int,
    section_ids: Sequence[int],
    corpus: BM25CorpusData,
) -> list[tuple[int, str, int, int]]:
    if len(section_ids) != len(corpus.sections):
        raise ValueError("BM25 section IDs do not match indexed sections")
    rows: list[tuple[int, str, int, int]] = []
    for section_id, section in zip(section_ids, corpus.sections, strict=True):
        rows.extend(
            (int(document_id), term, int(section_id), int(term_frequency))
            for term, term_frequency in section.term_frequencies.items()
        )
    return rows


def write_bm25_index(
    cursor: Any,
    document_id: int,
    section_ids: Sequence[int],
    corpus: BM25CorpusData,
    *,
    update_sections: bool,
) -> None:
    if update_sections:
        cursor.executemany(
            "UPDATE ai8_script_sections SET retrieval_text = %s, token_count = %s "
            "WHERE id = %s AND document_id = %s",
            [
                (section.retrieval_text, section.token_count, section_id, int(document_id))
                for section_id, section in zip(section_ids, corpus.sections, strict=True)
            ],
        )
    cursor.execute("DELETE FROM ai8_script_bm25_terms WHERE document_id = %s", (int(document_id),))
    posting_rows = build_posting_rows(document_id, section_ids, corpus)
    if posting_rows:
        cursor.executemany(
            "INSERT INTO ai8_script_bm25_terms "
            "(document_id, term, section_id, term_frequency) VALUES (%s, %s, %s, %s)",
            posting_rows,
        )
    cursor.execute(
        "INSERT INTO ai8_script_bm25_corpora "
        "(document_id, index_version, tokenizer_version, section_count, "
        "average_section_length, corpus_hash, built_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, NOW()) "
        "ON CONFLICT (document_id) DO UPDATE SET "
        "index_version = EXCLUDED.index_version, "
        "tokenizer_version = EXCLUDED.tokenizer_version, "
        "section_count = EXCLUDED.section_count, "
        "average_section_length = EXCLUDED.average_section_length, "
        "corpus_hash = EXCLUDED.corpus_hash, built_at = NOW()",
        (
            int(document_id),
            BM25_INDEX_VERSION,
            BM25_TOKENIZER_VERSION,
            corpus.section_count,
            corpus.average_section_length,
            corpus.corpus_hash,
        ),
    )


def replace_document_sections(
    cursor: Any,
    document_id: int,
    corpus: BM25CorpusData,
) -> None:
    cursor.execute("DELETE FROM ai8_script_sections WHERE document_id = %s", (int(document_id),))
    section_ids: list[int] = []
    for indexed_section in corpus.sections:
        cursor.execute(
            "INSERT INTO ai8_script_sections "
            "(document_id, section_order, heading, content, char_count, retrieval_text, "
            "token_count, search_terms) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                int(document_id),
                indexed_section.section_order,
                indexed_section.heading,
                indexed_section.content,
                len(indexed_section.content),
                indexed_section.retrieval_text,
                indexed_section.token_count,
                build_search_terms(indexed_section.retrieval_text),
            ),
        )
        section_ids.append(int(cursor.fetchone()["id"]))
    write_bm25_index(cursor, document_id, section_ids, corpus, update_sections=False)


def backfill_ready_document_indexes(connect: Any) -> dict[str, Any]:
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            bm25_backfill_candidates_sql(),
            (BM25_INDEX_VERSION, BM25_TOKENIZER_VERSION),
        )
        document_ids = [int(row["id"]) for row in cursor.fetchall()]
    stats: dict[str, Any] = {
        "rebuilt": 0,
        "unchanged": 0,
        "failed": 0,
        "lastError": "",
    }
    for document_id in document_ids:
        try:
            rebuilt = _backfill_document_index(connect, document_id)
        except Exception as exc:
            stats["failed"] += 1
            stats["lastError"] = _safe_error(exc)
        else:
            stats["rebuilt" if rebuilt else "unchanged"] += 1
    return stats


def _backfill_document_index(connect: Any, document_id: int) -> bool:
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('ai8_script_bm25:' || %s::TEXT, 0))",
            (int(document_id),),
        )
        cursor.execute(
            "SELECT d.id, c.index_version, c.tokenizer_version "
            "FROM ai8_script_documents d "
            "LEFT JOIN ai8_script_bm25_corpora c ON c.document_id = d.id "
            "WHERE d.id = %s AND d.index_status = 'ready' FOR UPDATE OF d",
            (int(document_id),),
        )
        document_state = cursor.fetchone()
        if not document_state or _bm25_index_is_current(document_state):
            return False
        cursor.execute(
            "SELECT id, section_order, heading, content FROM ai8_script_sections "
            "WHERE document_id = %s ORDER BY section_order",
            (int(document_id),),
        )
        section_rows = list(cursor.fetchall())
        if not section_rows:
            return False
        corpus = build_bm25_corpus(section_rows)
        section_ids = [int(row["id"]) for row in section_rows]
        write_bm25_index(
            cursor,
            document_id,
            section_ids,
            corpus,
            update_sections=True,
        )
    return True


def search_section_rows(
    cursor: Any,
    query: str,
    relative_path: str,
    limit: int,
) -> SectionSearchResult:
    mode = retrieval_mode()
    query_terms = build_bm25_query_terms(query) if mode != "legacy" else []
    trace = _base_search_trace(mode, query, relative_path, query_terms)
    started_at = time.perf_counter()
    if not relative_path:
        return _search_result([], trace, "legacy", "missing_document_scope", started_at)
    document_state = _resolve_document_state(cursor, relative_path)
    if mode == "legacy":
        rows = _run_legacy_search(cursor, query, relative_path, limit)
        return _search_result(rows, trace, "legacy", "", started_at, document_state)
    if not _bm25_index_is_current(document_state) or not query_terms:
        reason = "bm25_index_unavailable" if query_terms else "bm25_empty_query_terms"
        rows = _run_legacy_search(cursor, query, relative_path, limit)
        return _search_result(rows, trace, "legacy", reason, started_at, document_state)
    if mode == "shadow":
        return _run_shadow_search(cursor, query, relative_path, limit, query_terms, trace, document_state, started_at)
    return _run_primary_bm25_search(cursor, query, relative_path, limit, query_terms, trace, document_state, started_at)


def retrieval_mode() -> str:
    configured_mode = str(os.getenv(RETRIEVAL_MODE_ENV) or "bm25").strip().lower()
    return configured_mode if configured_mode in VALID_RETRIEVAL_MODES else "bm25"


def _run_primary_bm25_search(
    cursor: Any,
    query: str,
    relative_path: str,
    limit: int,
    query_terms: list[str],
    trace: dict[str, Any],
    document_state: Mapping[str, Any],
    started_at: float,
) -> SectionSearchResult:
    rows, bm25_error = _try_bm25_search(cursor, query, query_terms, document_state, limit)
    if bm25_error is not None:
        rows = _run_legacy_search(cursor, query, relative_path, limit)
        reason = f"bm25_failed:{_safe_error(bm25_error)}"
        return _search_result(rows, trace, "legacy", reason, started_at, document_state)
    return _search_result(rows, trace, "bm25", "", started_at, document_state)


def _run_shadow_search(
    cursor: Any,
    query: str,
    relative_path: str,
    limit: int,
    query_terms: list[str],
    trace: dict[str, Any],
    document_state: Mapping[str, Any],
    started_at: float,
) -> SectionSearchResult:
    legacy_started_at = time.perf_counter()
    legacy_rows = _run_legacy_search(cursor, query, relative_path, limit)
    trace["legacyLatencyMs"] = _elapsed_milliseconds(legacy_started_at)
    bm25_started_at = time.perf_counter()
    bm25_rows, bm25_error = _try_bm25_search(
        cursor,
        query,
        query_terms,
        document_state,
        limit,
    )
    if bm25_error is None:
        trace["bm25LatencyMs"] = _elapsed_milliseconds(bm25_started_at)
        trace["bm25CandidateIds"] = _candidate_ids(bm25_rows)
        trace["bm25Candidates"] = _candidate_scores(bm25_rows)
    else:
        trace["retrievalBackendFallbackReason"] = f"bm25_shadow_failed:{_safe_error(bm25_error)}"
    trace["legacyCandidateIds"] = _candidate_ids(legacy_rows)
    return _search_result(legacy_rows, trace, "legacy", trace["retrievalBackendFallbackReason"], started_at, document_state)


def _run_legacy_search(cursor: Any, query: str, relative_path: str, limit: int) -> list[Mapping[str, Any]]:
    cursor.execute(
        section_search_sql(),
        (
            query,
            f"%{escape_like(query)}%",
            build_ts_query(query),
            relative_path,
            int(limit),
        ),
    )
    return list(cursor.fetchall())


def _run_bm25_search(
    cursor: Any,
    query: str,
    query_terms: list[str],
    document_state: Mapping[str, Any],
    limit: int,
) -> list[Mapping[str, Any]]:
    cursor.execute(
        bm25_section_search_sql(),
        (
            query,
            f"%{escape_like(query)}%",
            BM25_K1,
            BM25_B,
            int(limit),
            query_terms,
            int(document_state["document_id"]),
            BM25_INDEX_VERSION,
            BM25_TOKENIZER_VERSION,
            int(limit),
        ),
    )
    return list(cursor.fetchall())


def _try_bm25_search(
    cursor: Any,
    query: str,
    query_terms: list[str],
    document_state: Mapping[str, Any],
    limit: int,
) -> tuple[list[Mapping[str, Any]], Exception | None]:
    cursor.execute("SAVEPOINT ai8video_bm25_search")
    try:
        rows = _run_bm25_search(cursor, query, query_terms, document_state, limit)
    except Exception as exc:
        cursor.execute("ROLLBACK TO SAVEPOINT ai8video_bm25_search")
        cursor.execute("RELEASE SAVEPOINT ai8video_bm25_search")
        return [], exc
    cursor.execute("RELEASE SAVEPOINT ai8video_bm25_search")
    return rows, None


def _resolve_document_state(cursor: Any, relative_path: str) -> Mapping[str, Any]:
    cursor.execute(bm25_document_state_sql(), (relative_path,))
    return cursor.fetchone() or {}


def _bm25_index_is_current(document_state: Mapping[str, Any]) -> bool:
    return bool(document_state) and (
        int(document_state.get("index_version") or 0) == BM25_INDEX_VERSION
        and int(document_state.get("tokenizer_version") or 0) == BM25_TOKENIZER_VERSION
    )


def _base_search_trace(
    mode: str,
    query: str,
    relative_path: str,
    query_terms: list[str],
) -> dict[str, Any]:
    return {
        "query": query,
        "relativePath": relative_path,
        "retrievalMode": mode,
        "retrievalBackend": "",
        "retrievalBackendFallbackReason": "",
        "documentId": 0,
        "contentHash": "",
        "indexVersion": 0,
        "tokenizerVersion": 0,
        "corpusHash": "",
        "queryTerms": list(query_terms),
        "protectedTerms": extract_protected_terms(query),
        "legacyCandidateIds": [],
        "bm25CandidateIds": [],
        "candidates": [],
        "latencyMs": 0.0,
    }


def _search_result(
    rows: Sequence[Mapping[str, Any]],
    trace: dict[str, Any],
    backend: str,
    fallback_reason: str,
    started_at: float,
    document_state: Mapping[str, Any] | None = None,
) -> SectionSearchResult:
    state = document_state or {}
    trace.update({
        "retrievalBackend": backend,
        "retrievalBackendFallbackReason": fallback_reason,
        "documentId": int(state.get("document_id") or 0),
        "contentHash": str(state.get("content_hash") or ""),
        "indexVersion": int(state.get("index_version") or 0),
        "tokenizerVersion": int(state.get("tokenizer_version") or 0),
        "corpusHash": str(state.get("corpus_hash") or ""),
        "latencyMs": _elapsed_milliseconds(started_at),
        "candidates": _candidate_scores(rows),
    })
    candidate_key = "bm25CandidateIds" if backend == "bm25" else "legacyCandidateIds"
    trace[candidate_key] = _candidate_ids(rows)
    return SectionSearchResult(rows=tuple(rows), trace=trace)


def _candidate_ids(rows: Sequence[Mapping[str, Any]]) -> list[int]:
    return [int(row.get("section_id") or 0) for row in rows if int(row.get("section_id") or 0) > 0]


def _candidate_scores(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": int(row.get("section_id") or 0),
            "score": float(row.get("score") or 0),
            "bm25Score": float(row.get("bm25_score") or 0),
            "trigramScore": float(row.get("trigram_score") or 0),
            "retrievalChannels": list(row.get("retrieval_channels") or ["legacy"]),
        }
        for row in rows
        if int(row.get("section_id") or 0) > 0
    ]


def _elapsed_milliseconds(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000.0, 3)


def _safe_error(exc: Exception) -> str:
    return (str(exc).splitlines()[0].strip() or exc.__class__.__name__)[:160]


def calculate_bm25_term_score(
    *,
    document_count: int,
    document_frequency: int,
    term_frequency: int,
    section_length: int,
    average_section_length: float,
    k1: float = BM25_K1,
    b: float = BM25_B,
) -> float:
    if document_count <= 0 or document_frequency <= 0 or term_frequency <= 0:
        return 0.0
    safe_average_length = max(float(average_section_length), 1.0)
    inverse_document_frequency = math.log(
        1.0 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5)
    )
    length_normalization = 1.0 - b + b * max(section_length, 0) / safe_average_length
    denominator = term_frequency + k1 * length_normalization
    return inverse_document_frequency * term_frequency * (k1 + 1.0) / denominator
