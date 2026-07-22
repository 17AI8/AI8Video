from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ai8video.generation.business_prompt import read_business_prompt
from ai8video.knowledge.script_knowledge_query import plan_retrieval_query
from ai8video.assets.user_files import USER_FILE_ROOT, ensure_user_file_root
from ai8video.assets.user_materials import list_user_materials, read_script_material_text
from ai8video.knowledge.script_knowledge_context import retrieve_reference_context


DEFAULT_SCRIPT_REFERENCE_DIR = (USER_FILE_ROOT / "剧本参考").resolve()
DEFAULT_SCRIPT_REFERENCE_SETTINGS_PATH = DEFAULT_SCRIPT_REFERENCE_DIR / "settings.json"


def default_script_reference_status() -> dict[str, Any]:
    item = load_default_script_reference()
    return {
        "ok": True,
        "enabled": bool(item),
        "item": item,
    }


def load_default_script_reference() -> dict[str, Any] | None:
    data = _read_settings()
    relative_path = str(data.get("relativePath") or "").strip()
    if not relative_path:
        return None
    return _find_script_material(relative_path)


def select_default_script_reference(relative_path: str) -> dict[str, Any]:
    item = _find_script_material(relative_path)
    if not item:
        raise ValueError("剧本参考不在剧本素材库里")
    _write_settings({
        "relativePath": item.get("relativePath") or item.get("name") or "",
        "name": item.get("name") or "",
    })
    return default_script_reference_status()


def clear_default_script_reference() -> dict[str, Any]:
    DEFAULT_SCRIPT_REFERENCE_SETTINGS_PATH.unlink(missing_ok=True)
    return default_script_reference_status()


def apply_default_script_reference(
    text: str,
    material_context: dict[str, Any] | None,
    *,
    prefer_full: bool = True,
    rerank_llm=None,
    query_llm=None,
) -> tuple[str, dict[str, Any]]:
    context = _normalize_material_context(material_context)
    item = load_default_script_reference()
    if not item:
        return text, context
    item_path = str(item.get("path") or "").strip()
    if not item_path:
        return text, context
    scripts = list(context.get("scripts") or [])
    if any(str(script.get("path") or "") == item_path for script in scripts):
        return text, context
    relative_path = str(item.get("relativePath") or item.get("name") or "").strip()
    if not prefer_full and relative_path:
        retrieval = retrieve_script_reference_context(
            text,
            item,
            query_llm=query_llm,
            rerank_llm=rerank_llm,
        )
        if retrieval.get("ok"):
            return _apply_retrieved_reference(text, context, item, scripts, retrieval)
    return _apply_full_reference(text, context, item, scripts, item_path)


def retrieve_script_reference_context(
    text: str,
    item: dict[str, Any],
    *,
    query_llm=None,
    rerank_llm=None,
) -> dict[str, Any]:
    relative_path = str(item.get("relativePath") or item.get("name") or "").strip()
    if not relative_path:
        return {"ok": False, "fallbackReason": "missing_relative_path"}
    query_hint = _reference_query_hint(item)
    query_plan = plan_retrieval_query(
        text,
        read_business_prompt(),
        query_hint,
        llm=query_llm,
    )
    return retrieve_reference_context(
        text,
        relative_path,
        rerank_llm=rerank_llm,
        query_hint=query_hint,
        query_plan=query_plan,
    )


def _apply_full_reference(
    text: str,
    context: dict[str, Any],
    item: dict[str, Any],
    scripts: list[dict[str, Any]],
    item_path: str,
) -> tuple[str, dict[str, Any]]:
    content = read_script_material_text(item_path, limit=None)
    if not content:
        return text, context
    enriched = dict(item)
    enriched["source"] = "defaultScriptReference"
    enriched["contentPreview"] = re.sub(r"\s+", " ", content).strip()[:180]
    enriched["contentCharCount"] = len(content)
    scripts.append(enriched)
    context["scripts"] = scripts
    addition = f"剧本参考《{item.get('name') or item.get('relativePath') or '剧本素材'}》内容：\n{content}"
    return text.rstrip() + "\n\n" + addition, context


def _apply_retrieved_reference(
    text: str,
    context: dict[str, Any],
    item: dict[str, Any],
    scripts: list[dict[str, Any]],
    retrieval: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    content = str(retrieval.get("contextText") or "").strip()
    enriched = dict(item)
    enriched.update({
        "source": "defaultScriptReference",
        "retrievalMode": "topK",
        "retrievalQuery": str(retrieval.get("query") or ""),
        "retrievalQueryPlan": dict(retrieval.get("queryPlan") or {}),
        "recallCount": int(retrieval.get("recallCount") or 0),
        "topK": int(retrieval.get("topK") or 0),
        "rerankApplied": bool(retrieval.get("rerankApplied")),
        "rerankFallbackReason": str(retrieval.get("fallbackReason") or ""),
        "retrievedSections": _retrieved_section_meta(retrieval.get("sections") or []),
        "contentPreview": re.sub(r"\s+", " ", content).strip()[:180],
        "contentCharCount": len(content),
    })
    scripts.append(enriched)
    context["scripts"] = scripts
    name = item.get("name") or item.get("relativePath") or "剧本素材"
    addition = f"剧本参考《{name}》相关知识段（Top {enriched['topK']}）：\n{content}"
    return text.rstrip() + "\n\n" + addition, context


def _retrieved_section_meta(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": int(section.get("id") or 0),
            "heading": str(section.get("heading") or ""),
            "score": float(section.get("score") or 0),
        }
        for section in sections
    ]


def _reference_query_hint(item: dict[str, Any]) -> str:
    tags = " ".join(str(tag).strip() for tag in item.get("tags") or [] if str(tag).strip())
    values = (
        item.get("title"),
        item.get("summary"),
        tags,
        item.get("preview"),
        item.get("name"),
    )
    return " ".join(str(value).strip() for value in values if str(value or "").strip())[:500]


def _read_settings() -> dict[str, Any]:
    try:
        data = json.loads(DEFAULT_SCRIPT_REFERENCE_SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_settings(data: dict[str, Any]) -> None:
    ensure_user_file_root()
    DEFAULT_SCRIPT_REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_SCRIPT_REFERENCE_SETTINGS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _find_script_material(relative_path: str) -> dict[str, Any] | None:
    target = str(relative_path or "").strip()
    if not target:
        return None
    for item in list_user_materials().get("scripts") or []:
        if target in {
            str(item.get("relativePath") or ""),
            str(item.get("name") or ""),
            str(item.get("path") or ""),
        }:
            return item
    return None


def _normalize_material_context(material_context: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(material_context, dict):
        return {
            "mentions": list(material_context.get("mentions") or []),
            "images": list(material_context.get("images") or []),
            "scripts": list(material_context.get("scripts") or []),
            "missing": list(material_context.get("missing") or []),
        }
    return {"mentions": [], "images": [], "scripts": [], "missing": []}
