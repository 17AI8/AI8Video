from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from ai8video.knowledge.script_knowledge_sql import upsert_document_sql
from ai8video.knowledge.script_knowledge_text import preview


SCRIPT_INDEX_VERSION = 4
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def synchronize_document_sources(
    cursor: Any,
    sources: Sequence[Mapping[str, Any]],
    content_reader: Callable[[str | Path], str],
) -> dict[str, int]:
    cursor.execute(
        "SELECT relative_path, size_bytes, source_modified_at, source_file_hash, "
        "content_hash, index_version FROM ai8_script_documents"
    )
    known_sources = {
        str(row["relative_path"]): row
        for row in cursor.fetchall()
    }
    statistics = {"registered": 0, "unchanged": 0, "removed": 0}
    current_paths: set[str] = set()
    for source in sources:
        relative_path = str(source.get("relativePath") or "").strip()
        if not relative_path:
            continue
        current_paths.add(relative_path)
        outcome = _synchronize_document_source(
            cursor,
            source,
            known_sources.get(relative_path),
            content_reader,
        )
        statistics[outcome] += 1
    stale_paths = set(known_sources) - current_paths
    if stale_paths:
        cursor.execute(
            "DELETE FROM ai8_script_documents WHERE relative_path = ANY(%s)",
            (list(stale_paths),),
        )
        statistics["removed"] = len(stale_paths)
    return statistics


def register_document_source(
    cursor: Any,
    source: Mapping[str, Any],
    content: str,
) -> int:
    document_id = _upsert_document_source(cursor, source, content)
    cursor.execute(
        "DELETE FROM ai8_script_sections WHERE document_id = %s",
        (document_id,),
    )
    return document_id


def _synchronize_document_source(
    cursor: Any,
    source: Mapping[str, Any],
    known_source: Mapping[str, Any] | None,
    content_reader: Callable[[str | Path], str],
) -> str:
    if known_source is None:
        content = content_reader(str(source.get("path") or ""))
        register_document_source(cursor, source, content)
        return "registered"
    relative_path = str(source.get("relativePath") or "").strip()
    if int(known_source.get("index_version") or 0) < SCRIPT_INDEX_VERSION:
        _mark_document_pending(cursor, relative_path)
        return "registered"
    if _source_can_skip_content_read(source, known_source):
        _refresh_source_fingerprint(cursor, source)
        return "unchanged"
    content = content_reader(str(source.get("path") or ""))
    if _content_matches_known_document(content, known_source):
        _refresh_source_fingerprint(cursor, source)
        return "unchanged"
    register_document_source(cursor, source, content)
    return "registered"


def _upsert_document_source(
    cursor: Any,
    source: Mapping[str, Any],
    content: str,
) -> int:
    relative_path = str(source.get("relativePath") or "").strip()
    name = str(source.get("name") or Path(relative_path).name).strip()
    source_path = str(source.get("path") or "").strip()
    source_modified_at = float(source.get("modifiedAt") or 0)
    size_bytes = int(source.get("sizeBytes") or len(content.encode("utf-8")))
    cursor.execute(
        upsert_document_sql(),
        (
            relative_path,
            name,
            Path(name).stem,
            source_path,
            Path(name).suffix.lower().lstrip(".") or "text",
            content,
            hashlib.sha256(content.encode("utf-8")).hexdigest(),
            _normalized_sha256(source.get("sourceFileHash")),
            preview(content),
            size_bytes,
            source_modified_at,
            "pending",
            SCRIPT_INDEX_VERSION,
            Path(name).stem,
        ),
    )
    return int(cursor.fetchone()["id"])


def _refresh_source_fingerprint(cursor: Any, source: Mapping[str, Any]) -> None:
    relative_path = str(source.get("relativePath") or "").strip()
    name = str(source.get("name") or Path(relative_path).name).strip()
    cursor.execute(
        "UPDATE ai8_script_documents SET name = %s, stem = %s, source_path = %s, "
        "content_type = %s, source_file_hash = COALESCE(NULLIF(%s, ''), source_file_hash), "
        "size_bytes = %s, source_modified_at = %s, updated_at = NOW() "
        "WHERE relative_path = %s",
        (
            name,
            Path(name).stem,
            str(source.get("path") or "").strip(),
            Path(name).suffix.lower().lstrip(".") or "text",
            _normalized_sha256(source.get("sourceFileHash")),
            int(source.get("sizeBytes") or 0),
            float(source.get("modifiedAt") or 0),
            relative_path,
        ),
    )


def _mark_document_pending(cursor: Any, relative_path: str) -> None:
    cursor.execute(
        "UPDATE ai8_script_documents SET index_status = 'pending', index_version = %s, "
        "indexed_at = NOW(), updated_at = NOW() WHERE relative_path = %s RETURNING id",
        (SCRIPT_INDEX_VERSION, relative_path),
    )
    row = cursor.fetchone()
    if row:
        cursor.execute(
            "DELETE FROM ai8_script_sections WHERE document_id = %s",
            (int(row["id"]),),
        )


def _source_can_skip_content_read(
    source: Mapping[str, Any],
    known_source: Mapping[str, Any],
) -> bool:
    source_file_hash = _normalized_sha256(source.get("sourceFileHash"))
    known_file_hash = _normalized_sha256(known_source.get("source_file_hash"))
    if source_file_hash and known_file_hash:
        return source_file_hash == known_file_hash
    return _source_metadata_matches(source, known_source)


def _source_metadata_matches(
    source: Mapping[str, Any],
    known_source: Mapping[str, Any],
) -> bool:
    same_size = int(source.get("sizeBytes") or 0) == int(known_source.get("size_bytes") or 0)
    modified_at_delta = abs(
        float(source.get("modifiedAt") or 0)
        - float(known_source.get("source_modified_at") or 0)
    )
    return same_size and modified_at_delta < 0.001


def _content_matches_known_document(
    content: str,
    known_source: Mapping[str, Any],
) -> bool:
    current_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    known_hash = _normalized_sha256(known_source.get("content_hash"))
    return bool(known_hash) and current_hash == known_hash


def _normalized_sha256(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if SHA256_PATTERN.fullmatch(candidate) else ""
