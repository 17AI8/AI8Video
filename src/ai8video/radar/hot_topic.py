from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai8video.core.config import AI8VideoConfig
from ai8video.radar.hot_topic_feeds import (
    FeedEntry,
    FeedSource,
    fetch_source_payloads,
    load_source_registry,
    registry_signature,
    save_custom_sources,
)
from ai8video.integrations.llm_provider import build_openai_compat_llm
from ai8video.assets.user_files import USER_FILE_ROOT


HOT_TOPIC_ROOT = (USER_FILE_ROOT / "爆款拆解" / "热点雷达").resolve()
HOT_TOPIC_CACHE_PATH = (HOT_TOPIC_ROOT / "cache.json").resolve()
HOT_TOPIC_SOURCE_CONFIG_PATH = (HOT_TOPIC_ROOT / "feeds.json").resolve()
HOT_TOPIC_SUMMARY_DIR = (HOT_TOPIC_ROOT / "摘要").resolve()
HOT_TOPIC_REQUEST_TIMEOUT_SECONDS = 12
HOT_TOPIC_CACHE_TTL_SECONDS = 600
HOT_TOPIC_FAILURE_CACHE_TTL_SECONDS = 60


def ensure_hot_topic_dirs() -> Path:
    for directory in (HOT_TOPIC_ROOT, HOT_TOPIC_SUMMARY_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    return HOT_TOPIC_ROOT


def list_hot_topic_sources() -> dict[str, Any]:
    ensure_hot_topic_dirs()
    registry = _source_registry()
    sources = [source.public_dict() for source in registry.values()]
    return {"ok": True, "sources": sources, "categories": _group_sources_by_category(sources)}


def update_hot_topic_sources(raw_feeds: object) -> dict[str, Any]:
    ensure_hot_topic_dirs()
    save_custom_sources(HOT_TOPIC_SOURCE_CONFIG_PATH, raw_feeds)
    return list_hot_topic_sources()


def list_hot_topics(
    *,
    sources: str | None = None,
    category: str | None = None,
    keyword: str | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    ensure_hot_topic_dirs()
    registry = _source_registry()
    all_source_ids = list(registry)
    selected_ids = _resolve_source_ids(registry, sources=sources, category=category)
    signature = registry_signature(registry, all_source_ids)
    cached = _read_cache()
    now = time.time()
    if force_refresh or not _cache_is_fresh(cached, all_source_ids, signature, now):
        payload = _fetch_hot_topics(registry, all_source_ids, signature, now)
        if not payload["items"] and _cache_matches(cached, all_source_ids, signature) and cached.get("items"):
            payload = _stale_cache_payload(cached, payload, now)
        _write_json(HOT_TOPIC_CACHE_PATH, payload)
    else:
        payload = cached
    items = _filter_items(payload.get("items"), keyword, selected_ids)
    source_payload = _decorate_source_payload(list_hot_topic_sources(), payload)
    return _build_topic_response(source_payload, payload, selected_ids, items)


def summarize_hot_topic(
    raw_topic: object,
    *,
    config: AI8VideoConfig | None = None,
) -> dict[str, Any]:
    ensure_hot_topic_dirs()
    topic = raw_topic if isinstance(raw_topic, dict) else {}
    title = str(topic.get("title") or "").strip()
    if not title:
        raise ValueError("热点标题不能为空")
    llm_config = config or AI8VideoConfig.from_env()
    llm = build_openai_compat_llm(
        llm_config,
        timeout_seconds=45,
        system_prompt="你是短视频热点选题分析助手，只输出清晰、可核验的中文分析。",
    )
    if llm is None:
        raise RuntimeError("文本模型没有配置完整，不能生成热点摘要")
    text = str(llm(_summary_prompt(topic)) or "").strip()
    if not text:
        raise RuntimeError("热点摘要返回为空")
    payload = _summary_payload(topic, text, llm_config)
    summary_path = HOT_TOPIC_SUMMARY_DIR / f"{_safe_filename(title)}.json"
    _write_json(summary_path, payload)
    payload["summaryPath"] = str(summary_path)
    return payload


def build_hot_topic_prompt(raw_topic: object) -> dict[str, Any]:
    topic = raw_topic if isinstance(raw_topic, dict) else {}
    title = str(topic.get("title") or "").strip()
    if not title:
        raise ValueError("热点标题不能为空")
    source_name = str(topic.get("sourceName") or topic.get("source") or "未知来源").strip()
    description = str(topic.get("description") or "").strip()
    url = str(topic.get("url") or "").strip()
    prompt = f"""请围绕下面的公开热点设计一条短视频，先核验事实，再给创作方案。

标题：{title}
来源：{source_name or '未知来源'}
链接：{url or '无'}
摘要：{description or '无'}

请输出：
1. 事实边界与仍需核验的信息；
2. 目标观众和内容价值；
3. 三个差异化拍摄角度；
4. 30 秒口播结构；
5. 不误导观众的标题与开场钩子。
""".strip()
    return {"ok": True, "topic": topic, "prompt": prompt, "promptChars": len(prompt)}


def _source_registry() -> dict[str, FeedSource]:
    return load_source_registry(HOT_TOPIC_SOURCE_CONFIG_PATH)


def _group_sources_by_category(sources: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    categories: dict[str, list[dict[str, Any]]] = {}
    for item in sources:
        categories.setdefault(str(item.get("category") or "未分类"), []).append(item)
    return categories


def _resolve_source_ids(
    registry: dict[str, FeedSource],
    *,
    sources: str | None,
    category: str | None,
) -> list[str]:
    if sources:
        requested = [item.strip() for item in sources.split(",") if item.strip()]
        return [source_id for source_id in requested if source_id in registry]
    if category:
        return [source.id for source in registry.values() if source.category == category]
    return list(registry)


def _fetch_hot_topics(
    registry: dict[str, FeedSource],
    source_ids: list[str],
    signature: str,
    now: float,
) -> dict[str, Any]:
    payloads = fetch_source_payloads(registry, source_ids, HOT_TOPIC_REQUEST_TIMEOUT_SECONDS)
    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for source_id in source_ids:
        source = registry[source_id]
        payload = payloads[source_id]
        if payload.get("error"):
            errors.append({"sourceId": source_id, "sourceName": source.name, "error": str(payload["error"])})
            continue
        entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
        items.extend(_topic_item(source, rank, entry) for rank, entry in enumerate(entries, 1))
    items = _deduplicate_items(items)
    return _fresh_payload(source_ids, signature, items, errors, now)


def _topic_item(source: FeedSource, rank: int, entry: FeedEntry) -> dict[str, Any]:
    digest = hashlib.sha256(f"{source.id}\0{entry.url}\0{entry.title}".encode("utf-8")).hexdigest()[:18]
    return {
        "id": f"{source.id}-{digest}",
        "rank": rank,
        "title": entry.title,
        "description": entry.description[:500],
        "url": entry.url,
        "sourceId": source.id,
        "sourceName": source.name,
        "category": source.category,
        "heat": entry.heat if entry.heat is not None else max(1, 101 - rank),
        "trend": "hot" if rank <= 5 else "stable",
        "publishedAt": entry.published_at,
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
    }


def _fresh_payload(
    source_ids: list[str],
    signature: str,
    items: list[dict[str, Any]],
    errors: list[dict[str, str]],
    now: float,
) -> dict[str, Any]:
    return {
        "sourceIds": source_ids,
        "sourceSignature": signature,
        "items": items,
        "errors": errors,
        "stale": False,
        "unavailableReason": "" if items else "全部公开数据源暂不可用",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "expiresAt": now + (HOT_TOPIC_CACHE_TTL_SECONDS if items else HOT_TOPIC_FAILURE_CACHE_TTL_SECONDS),
    }


def _decorate_source_payload(source_payload: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    item_counts: dict[str, int] = {}
    for item in payload.get("items") if isinstance(payload.get("items"), list) else []:
        source_id = str(item.get("sourceId") or "")
        item_counts[source_id] = item_counts.get(source_id, 0) + 1
    errors = {str(item.get("sourceId") or ""): str(item.get("error") or "") for item in payload.get("errors", [])}
    sources = [
        {
            **item,
            "available": item_counts.get(str(item.get("id") or ""), 0) > 0,
            "itemCount": item_counts.get(str(item.get("id") or ""), 0),
            "error": errors.get(str(item.get("id") or ""), ""),
        }
        for item in source_payload.get("sources", [])
    ]
    return {"ok": True, "sources": sources, "categories": _group_sources_by_category(sources)}


def _filter_items(
    items: object,
    keyword: str | None,
    source_ids: list[str],
) -> list[dict[str, Any]]:
    normalized_keyword = str(keyword or "").strip().lower()
    allowed_sources = set(source_ids)
    results: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or str(item.get("sourceId") or "") not in allowed_sources:
            continue
        searchable = " ".join(str(item.get(key) or "") for key in ("title", "description", "sourceName")).lower()
        if not normalized_keyword or normalized_keyword in searchable:
            results.append(item)
    return results


def _deduplicate_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for item in items:
        key = str(item.get("url") or item.get("title") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        results.append(item)
    return results


def _cache_is_fresh(payload: dict[str, Any], source_ids: list[str], signature: str, now: float) -> bool:
    return (
        _cache_matches(payload, source_ids, signature)
        and isinstance(payload.get("items"), list)
        and float(payload.get("expiresAt") or 0) > now
    )


def _cache_matches(payload: dict[str, Any], source_ids: list[str], signature: str) -> bool:
    return payload.get("sourceIds") == source_ids and payload.get("sourceSignature") == signature


def _stale_cache_payload(cached: dict[str, Any], failed: dict[str, Any], now: float) -> dict[str, Any]:
    return {
        **cached,
        "errors": failed.get("errors", []),
        "stale": True,
        "unavailableReason": "实时拉取失败，已展示最近一次缓存",
        "expiresAt": now + HOT_TOPIC_FAILURE_CACHE_TTL_SECONDS,
    }


def _build_topic_response(
    source_payload: dict[str, Any],
    payload: dict[str, Any],
    source_ids: list[str],
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    selected_errors = [item for item in payload.get("errors", []) if item.get("sourceId") in source_ids]
    unavailable_reason = ""
    if not items:
        unavailable_reason = "所选数据源暂不可用" if selected_errors else "所选范围暂无热点"
    return {
        "ok": True,
        "sources": source_payload["sources"],
        "categories": source_payload["categories"],
        "fetchRouteLabel": "公开数据源",
        "sourceIds": source_ids,
        "items": items,
        "itemCount": len(items),
        "updatedAt": payload.get("updatedAt"),
        "cachePath": str(HOT_TOPIC_CACHE_PATH),
        "sourceConfigPath": str(HOT_TOPIC_SOURCE_CONFIG_PATH),
        "errors": selected_errors,
        "stale": bool(payload.get("stale")),
        "realDataAvailable": bool(items),
        "unavailableReason": unavailable_reason,
    }


def _summary_payload(
    topic: dict[str, Any],
    text: str,
    config: AI8VideoConfig,
) -> dict[str, Any]:
    return {
        "ok": True,
        "topic": topic,
        "text": text,
        "textChars": len(text),
        "model": str(config.llm_model or ""),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }


def _summary_prompt(topic: dict[str, Any]) -> str:
    return f"""请分析这个公开热点，但不要补写来源中没有的事实。

标题：{str(topic.get('title') or '').strip()}
来源：{str(topic.get('sourceName') or topic.get('source') or '未知来源').strip()}
链接：{str(topic.get('url') or '无').strip() or '无'}
原始摘要：{str(topic.get('description') or '无').strip() or '无'}

请输出：
1. 已知事实与待核验项；
2. 受众为什么会关注；
3. 三个短视频切入角度；
4. 可能误导观众的表达；
5. 一段可直接交给 AI8video 的创作提示词。
""".strip()


def _read_cache() -> dict[str, Any]:
    if not HOT_TOPIC_CACHE_PATH.is_file():
        return {}
    try:
        payload = json.loads(HOT_TOPIC_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temp_path = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", value).strip(".-_")
    return (cleaned or "hot-topic")[:80]
