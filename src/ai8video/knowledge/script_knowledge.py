from __future__ import annotations

import hashlib
import os
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ai8video.knowledge.script_knowledge_sql import (
    document_detail_sql as _document_detail_sql,
    document_list_sql as _document_list_sql,
    schema_statements as _schema_statements,
    section_search_sql as _section_search_sql,
    search_sql as _search_sql,
    upsert_document_sql as _upsert_document_sql,
)
from ai8video.knowledge.script_knowledge_text import (
    build_search_terms as _build_search_terms,
    build_ts_query as _build_ts_query,
    escape_like as _escape_like,
    normalize_query as _normalize_query,
    normalize_tags as _normalize_tags,
    preview as _preview,
    split_sections as _split_sections,
)


DATABASE_URL_ENV = "AI8VIDEO_SCRIPT_DATABASE_URL"
DEFAULT_DATABASE_URL = "postgresql:///ai8video"
SCRIPT_INDEX_VERSION = 2


class ScriptKnowledgeUnavailable(RuntimeError):
    pass


class ScriptKnowledgeStore:
    def __init__(
        self,
        database_url: str | None = None,
        connector: Callable[[], Any] | None = None,
    ) -> None:
        self.database_url = database_url if database_url is not None else _database_url_from_env()
        self._connector = connector
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.database_url or self._connector)

    def initialize(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            with self._connect() as connection, connection.cursor() as cursor:
                for statement in _schema_statements():
                    cursor.execute(statement)
            self._schema_ready = True

    def status(self) -> dict[str, Any]:
        if not self.configured:
            return _unavailable_status("未配置剧本知识库数据库")
        try:
            self.initialize()
            with self._connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) AS total, "
                    "COUNT(*) FILTER (WHERE index_status = 'ready') AS ready "
                    "FROM ai8_script_documents"
                )
                row = cursor.fetchone() or {}
        except Exception as exc:
            return _unavailable_status(_safe_error(exc))
        return {
            "configured": True,
            "available": True,
            "backend": "postgresql",
            "embeddingEnabled": False,
            "documentCount": int(row.get("total") or 0),
            "readyCount": int(row.get("ready") or 0),
            "error": "",
        }

    def sync_sources(
        self,
        sources: list[dict[str, Any]],
        content_reader: Callable[[str | Path], str],
    ) -> dict[str, int]:
        self.initialize()
        stats = {"indexed": 0, "unchanged": 0, "removed": 0}
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT relative_path, size_bytes, source_modified_at, index_version "
                "FROM ai8_script_documents"
            )
            known = {str(row["relative_path"]): row for row in cursor.fetchall()}
            current_paths: set[str] = set()
            for source in sources:
                relative_path = str(source.get("relativePath") or "").strip()
                if not relative_path:
                    continue
                current_paths.add(relative_path)
                if _source_is_unchanged(source, known.get(relative_path)):
                    stats["unchanged"] += 1
                    continue
                content = content_reader(str(source.get("path") or ""))
                self._upsert_document(cursor, source, content)
                stats["indexed"] += 1
            stale_paths = set(known) - current_paths
            if stale_paths:
                cursor.execute(
                    "DELETE FROM ai8_script_documents WHERE relative_path = ANY(%s)",
                    (list(stale_paths),),
                )
                stats["removed"] = len(stale_paths)
        return stats

    def upsert_source(self, source: dict[str, Any], content: str) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection, connection.cursor() as cursor:
            document_id = self._upsert_document(cursor, source, content)
        return self.get_document(document_id)

    def remove_document(self, relative_path: str) -> bool:
        self.initialize()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM ai8_script_documents WHERE relative_path = %s RETURNING id",
                (relative_path,),
            )
            return cursor.fetchone() is not None

    def list_documents(self, *, limit: int = 100) -> list[dict[str, Any]]:
        self.initialize()
        safe_limit = max(1, min(int(limit), 500))
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(_document_list_sql(), (safe_limit,))
            return [_document_summary(row) for row in cursor.fetchall()]

    def search(self, query: str, *, limit: int = 50) -> list[dict[str, Any]]:
        clean_query = _normalize_query(query)
        if not clean_query:
            return self.list_documents(limit=limit)
        self.initialize()
        ts_query = _build_ts_query(clean_query)
        like_pattern = f"%{_escape_like(clean_query)}%"
        safe_limit = max(1, min(int(limit), 100))
        params = (clean_query, like_pattern, ts_query, safe_limit * 4)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(_search_sql(), params)
            rows = cursor.fetchall()
        return _dedupe_search_rows(rows, safe_limit)

    def search_sections(
        self,
        query: str,
        *,
        relative_path: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clean_query = _normalize_query(query)
        if not clean_query:
            return []
        self.initialize()
        safe_limit = max(1, min(int(limit), 50))
        params = (
            clean_query,
            f"%{_escape_like(clean_query)}%",
            _build_ts_query(clean_query),
            str(relative_path or "").strip(),
            safe_limit,
        )
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(_section_search_sql(), params)
            return [_section_candidate(row) for row in cursor.fetchall()]

    def get_document(self, document_id: int) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(_document_detail_sql(), (int(document_id),))
            row = cursor.fetchone()
            if not row:
                raise KeyError("剧本文档不存在")
            cursor.execute(
                "SELECT id, section_order, heading, content, char_count "
                "FROM ai8_script_sections WHERE document_id = %s ORDER BY section_order",
                (int(document_id),),
            )
            sections = [dict(section) for section in cursor.fetchall()]
        detail = _document_summary(row)
        detail.update({"content": str(row.get("content") or ""), "sections": sections})
        return detail

    def update_document(
        self,
        document_id: int,
        *,
        title: str,
        summary: str,
        tags: list[str],
    ) -> dict[str, Any]:
        clean_title = str(title or "").strip()[:200]
        clean_summary = str(summary or "").strip()[:2000]
        clean_tags = _normalize_tags(tags)
        self.initialize()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE ai8_script_documents SET title = %s, summary = %s, tags = %s, "
                "updated_at = NOW() WHERE id = %s RETURNING id",
                (clean_title, clean_summary, clean_tags, int(document_id)),
            )
            if not cursor.fetchone():
                raise KeyError("剧本文档不存在")
        return self.get_document(document_id)

    def _upsert_document(self, cursor: Any, source: dict[str, Any], content: str) -> int:
        relative_path = str(source.get("relativePath") or "").strip()
        name = str(source.get("name") or Path(relative_path).name).strip()
        source_path = str(source.get("path") or "").strip()
        source_mtime = float(source.get("modifiedAt") or 0)
        size_bytes = int(source.get("sizeBytes") or len(content.encode("utf-8")))
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        status = "ready" if content.strip() else "empty"
        cursor.execute(
            _upsert_document_sql(),
            (
                relative_path,
                name,
                Path(name).stem,
                source_path,
                Path(name).suffix.lower().lstrip(".") or "text",
                content,
                content_hash,
                _preview(content),
                size_bytes,
                source_mtime,
                status,
                SCRIPT_INDEX_VERSION,
                Path(name).stem,
            ),
        )
        document_id = int(cursor.fetchone()["id"])
        cursor.execute("DELETE FROM ai8_script_sections WHERE document_id = %s", (document_id,))
        for section_order, section in enumerate(_split_sections(content)):
            cursor.execute(
                "INSERT INTO ai8_script_sections "
                "(document_id, section_order, heading, content, char_count, search_terms) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    document_id,
                    section_order,
                    section["heading"],
                    section["content"],
                    len(section["content"]),
                    _build_search_terms(f"{name} {section['heading']} {section['content']}"),
                ),
            )
        return document_id

    def _connect(self) -> Any:
        if self._connector:
            return self._connector()
        if not self.database_url:
            raise ScriptKnowledgeUnavailable("未配置剧本知识库数据库")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise ScriptKnowledgeUnavailable("缺少 PostgreSQL 驱动 psycopg") from exc
        return psycopg.connect(self.database_url, connect_timeout=3, row_factory=dict_row)


_store: ScriptKnowledgeStore | None = None
_store_url = ""
_store_lock = threading.Lock()


def get_script_knowledge_store() -> ScriptKnowledgeStore:
    global _store, _store_url
    database_url = _database_url_from_env()
    with _store_lock:
        if _store is None or database_url != _store_url:
            _store = ScriptKnowledgeStore(database_url)
            _store_url = database_url
        return _store


def script_knowledge_payload(query: str = "", *, limit: int = 100) -> dict[str, Any]:
    store = get_script_knowledge_store()
    status = store.status()
    if not status["available"]:
        return {"ok": False, "status": status, "items": [], "query": str(query or "")}
    sync_result = synchronize_script_knowledge()
    items = store.search(query, limit=limit) if str(query or "").strip() else store.list_documents(limit=limit)
    return {"ok": True, "status": store.status(), "sync": sync_result, "items": items, "query": str(query or "")}


def synchronize_script_knowledge() -> dict[str, int]:
    from ai8video.assets.user_materials import list_script_material_sources, read_script_material_text

    store = get_script_knowledge_store()
    sources = list_script_material_sources()
    return store.sync_sources(sources, lambda path: read_script_material_text(path, limit=None))


def index_script_path(path: str | Path, *, root: str | Path) -> dict[str, Any]:
    from ai8video.assets.user_materials import read_script_material_text

    target = Path(path).resolve()
    root_path = Path(root).resolve()
    stat = target.stat()
    source = {
        "name": target.name,
        "relativePath": target.relative_to(root_path).as_posix(),
        "path": str(target),
        "sizeBytes": stat.st_size,
        "modifiedAt": stat.st_mtime,
    }
    return get_script_knowledge_store().upsert_source(source, read_script_material_text(target, limit=None))


def remove_script_knowledge_document(relative_path: str) -> dict[str, Any]:
    store = get_script_knowledge_store()
    status = store.status()
    if not status["available"]:
        return {"ok": False, "removed": False, "status": status}
    removed = store.remove_document(str(relative_path or "").strip())
    return {"ok": True, "removed": removed, "status": store.status()}


def _database_url_from_env() -> str:
    value = str(os.getenv(DATABASE_URL_ENV) or "").strip()
    if value.lower() in {"off", "none", "disabled"}:
        return ""
    return value or DEFAULT_DATABASE_URL


def _document_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": str(row.get("name") or ""),
        "stem": str(row.get("stem") or ""),
        "relativePath": str(row.get("relative_path") or ""),
        "path": str(row.get("source_path") or ""),
        "contentType": str(row.get("content_type") or ""),
        "preview": str(row.get("preview") or ""),
        "title": str(row.get("title") or row.get("stem") or ""),
        "summary": str(row.get("summary") or ""),
        "tags": list(row.get("tags") or []),
        "metadata": dict(row.get("metadata") or {}),
        "sizeBytes": int(row.get("size_bytes") or 0),
        "modifiedAt": float(row.get("source_modified_at") or 0),
        "indexStatus": str(row.get("index_status") or ""),
        "indexVersion": int(row.get("index_version") or 0),
        "sectionCount": int(row.get("section_count") or 0),
        "score": float(row.get("score") or 0),
        "matchedSectionId": int(row.get("matched_section_id") or 0),
        "matchedHeading": str(row.get("matched_heading") or ""),
        "matchedExcerpt": str(row.get("matched_excerpt") or ""),
        "kind": "script",
    }


def _section_candidate(row: dict[str, Any]) -> dict[str, Any]:
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
    }


def _dedupe_search_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in rows:
        document_id = int(row["id"])
        if document_id in seen:
            continue
        seen.add(document_id)
        results.append(_document_summary(row))
        if len(results) >= limit:
            break
    return results


def _source_is_unchanged(source: dict[str, Any], known: dict[str, Any] | None) -> bool:
    if not known:
        return False
    return (
        int(source.get("sizeBytes") or 0) == int(known.get("size_bytes") or 0)
        and abs(float(source.get("modifiedAt") or 0) - float(known.get("source_modified_at") or 0)) < 0.001
        and int(known.get("index_version") or 0) == SCRIPT_INDEX_VERSION
    )


def _safe_error(exc: Exception) -> str:
    line = str(exc).splitlines()[0].strip()
    line = re.sub(r"postgres(?:ql)?://[^\s@]+@", "postgresql://***@", line, flags=re.IGNORECASE)
    return line[:300] or exc.__class__.__name__


def _unavailable_status(error: str) -> dict[str, Any]:
    return {
        "configured": bool(_database_url_from_env()),
        "available": False,
        "backend": "postgresql",
        "embeddingEnabled": False,
        "documentCount": 0,
        "readyCount": 0,
        "error": str(error or "PostgreSQL 不可用"),
    }
