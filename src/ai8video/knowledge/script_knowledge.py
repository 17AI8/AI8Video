from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ai8video.knowledge.script_knowledge_sql import (
    SCRIPT_KNOWLEDGE_SCHEMA_VERSION,
    document_detail_sql as _document_detail_sql,
    document_list_sql as _document_list_sql,
    schema_statements as _schema_statements,
    search_sql as _search_sql,
)
from ai8video.knowledge.script_knowledge_bm25 import (
    BM25_INDEX_VERSION,
    BM25_TOKENIZER_VERSION,
    backfill_ready_document_indexes,
    build_bm25_corpus,
    replace_document_sections,
    retrieval_mode,
    search_section_rows,
)
from ai8video.knowledge.script_knowledge_text import (
    build_search_terms as _build_search_terms,
    build_ts_query as _build_ts_query,
    escape_like as _escape_like,
    normalize_query as _normalize_query,
    normalize_tags as _normalize_tags,
)
from ai8video.knowledge.script_knowledge_records import (
    attach_search_trace as _attach_search_trace,
    dedupe_search_rows as _dedupe_search_rows,
    document_summary as _document_summary,
    section_candidate as _section_candidate,
)
from ai8video.knowledge.script_knowledge_sources import (
    SCRIPT_INDEX_VERSION,
    register_document_source,
    synchronize_document_sources,
)


DATABASE_URL_ENV = "AI8VIDEO_SCRIPT_DATABASE_URL"
DEFAULT_DATABASE_URL = "postgresql:///ai8video"


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
        self._bm25_last_error = ""

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
                    "COUNT(*) FILTER (WHERE d.index_status = 'ready') AS ready, "
                    "COUNT(*) FILTER (WHERE d.index_status = 'ready' "
                    "AND c.index_version = %s AND c.tokenizer_version = %s) AS bm25_ready, "
                    "current_setting('server_version') AS server_version "
                    "FROM ai8_script_documents d "
                    "LEFT JOIN ai8_script_bm25_corpora c ON c.document_id = d.id",
                    (BM25_INDEX_VERSION, BM25_TOKENIZER_VERSION),
                )
                row = cursor.fetchone() or {}
        except Exception as exc:
            return _unavailable_status(_safe_error(exc))
        return {
            "configured": True,
            "available": True,
            "backend": "postgresql",
            "embeddingEnabled": False,
            "databaseVersion": str(row.get("server_version") or ""),
            "schemaVersion": SCRIPT_KNOWLEDGE_SCHEMA_VERSION,
            "documentCount": int(row.get("total") or 0),
            "readyCount": int(row.get("ready") or 0),
            "bm25ReadyCount": int(row.get("bm25_ready") or 0),
            "bm25PendingCount": max(0, int(row.get("ready") or 0) - int(row.get("bm25_ready") or 0)),
            "bm25IndexVersion": BM25_INDEX_VERSION,
            "bm25TokenizerVersion": BM25_TOKENIZER_VERSION,
            "retrievalMode": retrieval_mode(),
            "bm25LastError": self._bm25_last_error,
            "error": "",
        }

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
        safe_limit = max(1, min(int(limit), 50))
        with self._connect() as connection, connection.cursor() as cursor:
            search_result = search_section_rows(
                cursor,
                clean_query,
                str(relative_path or "").strip(),
                safe_limit,
            )
        return [
            _attach_search_trace(_section_candidate(row), search_result.trace)
            for row in search_result.rows
        ]

    def backfill_bm25_indexes(self) -> dict[str, Any]:
        if not self._schema_ready:
            self.initialize()
        stats = backfill_ready_document_indexes(self._connect)
        if stats["failed"]:
            self._bm25_last_error = str(stats["lastError"] or "BM25 index backfill failed")
        else:
            self._bm25_last_error = ""
        return stats

    def get_document(self, document_id: int) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(_document_detail_sql(), (int(document_id),))
            row = cursor.fetchone()
            if not row:
                raise KeyError("剧本文档不存在")
            cursor.execute(
                "SELECT id, section_order, heading, content, char_count, retrieval_text, token_count "
                "FROM ai8_script_sections WHERE document_id = %s ORDER BY section_order",
                (int(document_id),),
            )
            sections = [dict(section) for section in cursor.fetchall()]
        detail = _document_summary(row)
        detail.update({"content": str(row.get("content") or ""), "sections": sections})
        return detail

    def get_document_by_relative_path(self, relative_path: str) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM ai8_script_documents WHERE relative_path = %s",
                (str(relative_path or "").strip(),),
            )
            row = cursor.fetchone()
        if not row:
            raise KeyError("剧本文档不存在")
        return self.get_document(int(row["id"]))

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

    def replace_document_tree(
        self,
        document_id: int,
        tree: dict[str, Any],
        leaves: list[dict[str, Any]],
        *,
        ingestion_metadata: dict[str, Any] | None = None,
        expected_content_hash: str = "",
    ) -> dict[str, Any]:
        self.initialize()
        corpus = build_bm25_corpus(leaves)
        metadata = {
            "knowledgeTree": tree.get("tree") or [],
            "ingestion": "multi_agent_reviewed",
            **dict(ingestion_metadata or {}),
        }
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT content_hash FROM ai8_script_documents WHERE id = %s FOR UPDATE",
                (int(document_id),),
            )
            document_row = cursor.fetchone()
            if not document_row:
                raise KeyError("剧本文档不存在")
            current_content_hash = str(document_row.get("content_hash") or "")
            if expected_content_hash and current_content_hash != expected_content_hash:
                raise RuntimeError("原文已在知识入库期间更新，请重新执行知识入库")
            replace_document_sections(cursor, document_id, corpus)
            cursor.execute(
                "UPDATE ai8_script_documents SET title = %s, summary = %s, tags = %s, "
                "metadata = metadata || %s::jsonb, index_status = 'ready', index_version = %s, "
                "indexed_at = NOW(), updated_at = NOW() WHERE id = %s",
                (
                    str(tree.get("title") or ""),
                    str(tree.get("summary") or ""),
                    _normalize_tags(list(tree.get("tags") or [])),
                    json.dumps(metadata),
                    SCRIPT_INDEX_VERSION,
                    int(document_id),
                ),
            )
        return self.get_document(document_id)

    def register_sources(
        self,
        sources: list[dict[str, Any]],
        content_reader: Callable[[str | Path], str],
    ) -> dict[str, int]:
        self.initialize()
        with self._connect() as connection, connection.cursor() as cursor:
            stats = synchronize_document_sources(cursor, sources, content_reader)
        try:
            self.backfill_bm25_indexes()
        except Exception as exc:
            self._bm25_last_error = _safe_error(exc)
        return stats

    def register_source(self, source: dict[str, Any], content: str) -> int:
        self.initialize()
        with self._connect() as connection, connection.cursor() as cursor:
            return register_document_source(cursor, source, content)

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
    sync_result = register_script_knowledge_sources()
    items = store.search(query, limit=limit) if str(query or "").strip() else store.list_documents(limit=limit)
    return {"ok": True, "status": store.status(), "sync": sync_result, "items": items, "query": str(query or "")}


def register_script_knowledge_sources() -> dict[str, int]:
    from ai8video.assets.user_materials import list_script_material_sources, read_script_material_text

    store = get_script_knowledge_store()
    sources = list_script_material_sources()
    return store.register_sources(sources, lambda path: read_script_material_text(path, limit=None))


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
        "sourceFileHash": hashlib.sha256(target.read_bytes()).hexdigest(),
    }
    store = get_script_knowledge_store()
    store.register_source(source, read_script_material_text(target, limit=None))
    documents = store.list_documents(limit=500)
    return next(item for item in documents if item["relativePath"] == source["relativePath"])


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
        "databaseVersion": "",
        "schemaVersion": SCRIPT_KNOWLEDGE_SCHEMA_VERSION,
        "documentCount": 0,
        "readyCount": 0,
        "bm25ReadyCount": 0,
        "bm25PendingCount": 0,
        "bm25IndexVersion": BM25_INDEX_VERSION,
        "bm25TokenizerVersion": BM25_TOKENIZER_VERSION,
        "retrievalMode": retrieval_mode(),
        "bm25LastError": "",
        "error": str(error or "PostgreSQL 不可用"),
    }
